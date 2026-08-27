from __future__ import annotations

import ctypes
import os
import signal
import subprocess
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from ctypes import wintypes

from app.api.v1.schemas import Job
from app.core.logging_config import get_platform_logger
from app.services.platform_store import PlatformStore, platform_store


WorkerCommandFactory = Callable[[Path, str], list[str]]
logger = get_platform_logger("dispatcher")


def process_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == 'nt':
        process = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not process:
            return False
        try:
            exit_code = ctypes.c_ulong()
            return bool(ctypes.windll.kernel32.GetExitCodeProcess(process, ctypes.byref(exit_code))) and exit_code.value == 259
        finally:
            ctypes.windll.kernel32.CloseHandle(process)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def terminate_process_tree(pid: int) -> None:
    if not process_exists(pid):
        return
    if os.name == 'nt':
        for process_id in reversed(_windows_process_tree(pid)):
            handle = ctypes.windll.kernel32.OpenProcess(0x0001, False, process_id)
            if not handle:
                continue
            try:
                ctypes.windll.kernel32.TerminateProcess(handle, 1)
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
        return
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except ProcessLookupError:
        return


class _ProcessEntry32W(ctypes.Structure):
    _fields_ = [
        ('dwSize', wintypes.DWORD),
        ('cntUsage', wintypes.DWORD),
        ('th32ProcessID', wintypes.DWORD),
        ('th32DefaultHeapID', ctypes.c_size_t),
        ('th32ModuleID', wintypes.DWORD),
        ('cntThreads', wintypes.DWORD),
        ('th32ParentProcessID', wintypes.DWORD),
        ('pcPriClassBase', wintypes.LONG),
        ('dwFlags', wintypes.DWORD),
        ('szExeFile', wintypes.WCHAR * 260),
    ]


def _windows_process_tree(root_pid: int) -> list[int]:
    snapshot = ctypes.windll.kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        return [root_pid]
    children: dict[int, list[int]] = {}
    entry = _ProcessEntry32W()
    entry.dwSize = ctypes.sizeof(entry)
    try:
        has_entry = ctypes.windll.kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while has_entry:
            children.setdefault(int(entry.th32ParentProcessID), []).append(int(entry.th32ProcessID))
            has_entry = ctypes.windll.kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        ctypes.windll.kernel32.CloseHandle(snapshot)

    ordered: list[int] = []

    def visit(process_id: int) -> None:
        ordered.append(process_id)
        for child_pid in children.get(process_id, []):
            visit(child_pid)

    visit(root_pid)
    return ordered


class PlatformJobDispatcher:
    def __init__(
        self,
        store: PlatformStore,
        *,
        poll_interval_s: float = 0.5,
        heartbeat_timeout_s: float = 300.0,
        worker_command_factory: WorkerCommandFactory | None = None,
    ) -> None:
        self.store = store
        self.poll_interval_s = poll_interval_s
        self.heartbeat_timeout_s = heartbeat_timeout_s
        self.worker_command_factory = worker_command_factory or self._default_worker_command
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._active_job_id: str | None = None
        self._active_process: subprocess.Popen[bytes] | None = None
        self._lock = threading.RLock()

    @property
    def is_running(self) -> bool:
        thread = self._thread
        return bool(thread and thread.is_alive() and not self._stop_event.is_set())

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self.store.fail_legacy_queued_jobs()
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._run, name='platform-job-dispatcher', daemon=True)
            self._thread.start()
            logger.info("dispatcher started", extra={"event": "dispatcher_started"})

    def stop(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread:
            thread.join(timeout=max(self.poll_interval_s * 4, 2.0))
        with self._lock:
            if self._active_process and self._active_process.poll() is None:
                terminate_process_tree(self._active_process.pid)
                if self._active_job_id:
                    self.store.fail_running_job(
                        self._active_job_id,
                        code='DISPATCHER_STOPPED',
                        message='平台调度器停止，工作进程树已终止',
                    )
            self._active_process = None
            self._active_job_id = None
        logger.info("dispatcher stopped", extra={"event": "dispatcher_stopped"})

    def cancel_job(self, job_id: str) -> dict[str, bool]:
        with self._lock:
            self.store.refresh()
            job = self.store.get_job(job_id)
            if job.status in ('SUCCEEDED', 'FAILED', 'CANCELLED'):
                return {'success': False}
            if self._active_job_id == job_id and self._active_process is not None:
                terminate_process_tree(self._active_process.pid)
                exit_code = self._active_process.wait(timeout=10)
                self.store.record_worker_exit(job_id, exit_code=exit_code)
                self._active_process = None
                self._active_job_id = None
            return self.store.cancel_job(job_id)

    def _run(self) -> None:
        while not self._stop_event.wait(self.poll_interval_s):
            try:
                self._tick()
            except Exception:
                logger.exception("dispatcher tick failed", extra={"event": "dispatcher_tick_failed"})
                # 轮询线程必须继续；具体 Job 错误由状态机记录。
                continue

    def _tick(self) -> None:
        with self._lock:
            if self._active_process is not None:
                self._inspect_active_process()
                return
            self.store.refresh()
            queued = next(
                (
                    job
                    for job in reversed(self.store.jobs)
                    if job.status == 'QUEUED' and job.queue_version == 1
                ),
                None,
            )
            if queued is not None:
                self._launch(queued)

    def _launch(self, job: Job) -> None:
        command = self.worker_command_factory(self.store.state_path, job.job_id)
        backend_root = Path(__file__).resolve().parents[2]
        env = os.environ.copy()
        repo_root = backend_root.parents[1]
        python_paths = [str(repo_root), str(backend_root)]
        if env.get('PYTHONPATH'):
            python_paths.append(env['PYTHONPATH'])
        env['PYTHONPATH'] = os.pathsep.join(python_paths)
        env['MOMO_PLATFORM_STATE_PATH'] = str(self.store.state_path.resolve())
        env['MOMO_PLATFORM_WORKER'] = '1'
        kwargs: dict[str, object] = {'cwd': str(backend_root), 'env': env}
        if os.name == 'nt':
            kwargs['creationflags'] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        else:
            kwargs['start_new_session'] = True
        try:
            process = subprocess.Popen(command, **kwargs)
        except OSError as exc:
            self.store.fail_running_job(
                job.job_id,
                code='WORKER_LAUNCH_ERROR',
                message=f'无法启动独立 worker：{exc}',
            )
            return
        self._active_job_id = job.job_id
        self._active_process = process
        logger.info(
            "worker launched",
            extra={
                "event": "worker_launched",
                "job_id": job.job_id,
                "worker_pid": process.pid,
            },
        )
        try:
            self.store.mark_job_running(job.job_id, pid=process.pid)
        except Exception as exc:
            terminate_process_tree(process.pid)
            exit_code = process.wait(timeout=10)
            self._active_job_id = None
            self._active_process = None
            self.store.fail_running_job(
                job.job_id,
                code='WORKER_ATTACH_ERROR',
                message=f'worker 已启动但无法登记运行状态：{exc}',
                exit_code=exit_code,
            )

    def _inspect_active_process(self) -> None:
        assert self._active_process is not None
        assert self._active_job_id is not None
        exit_code = self._active_process.poll()
        self.store.refresh()
        job = self.store.get_job(self._active_job_id)
        if exit_code is not None:
            self.store.record_worker_exit(job.job_id, exit_code=exit_code)
            if job.status == 'RUNNING':
                self.store.fail_running_job(
                    job.job_id,
                    code='WORKER_EXITED',
                    message=f'worker 在完成状态写入前退出，exit_code={exit_code}',
                    exit_code=exit_code,
                )
            self._active_process = None
            self._active_job_id = None
            return
        if job.status == 'CANCELLED':
            terminate_process_tree(self._active_process.pid)
            self._active_process.wait(timeout=10)
            self._active_process = None
            self._active_job_id = None
            return
        execution_timeout_s = self._execution_timeout_seconds(job)
        if (
            execution_timeout_s is not None
            and job.started_at
            and self._heartbeat_age_seconds(job.started_at) > execution_timeout_s
        ):
            terminate_process_tree(self._active_process.pid)
            self._active_process.wait(timeout=10)
            self.store.fail_running_job(
                job.job_id,
                code='EXECUTION_TIMEOUT',
                message=f'作业墙钟时间超过 {execution_timeout_s:g} 秒，进程树已终止',
            )
            self._active_process = None
            self._active_job_id = None
            return
        heartbeat = job.worker.heartbeat_at if job.worker else None
        if heartbeat and self._heartbeat_age_seconds(heartbeat) > self.heartbeat_timeout_s:
            terminate_process_tree(self._active_process.pid)
            self._active_process.wait(timeout=10)
            self.store.fail_running_job(
                job.job_id,
                code='HEARTBEAT_TIMEOUT',
                message=f'worker 心跳超过 {self.heartbeat_timeout_s:g} 秒未更新，进程树已终止',
            )
            self._active_process = None
            self._active_job_id = None

    @staticmethod
    def _execution_timeout_seconds(job: Job) -> float | None:
        request = job.request or {}
        raw_timeout = request.get('executionTimeoutS')
        if raw_timeout is None:
            raw_timeout = (request.get('resources') or {}).get('executionTimeoutS')
        if isinstance(raw_timeout, bool) or raw_timeout is None:
            return None
        try:
            timeout = float(raw_timeout)
        except (TypeError, ValueError):
            return None
        return timeout if timeout > 0 else None

    @staticmethod
    def _heartbeat_age_seconds(value: str) -> float:
        heartbeat = datetime.fromisoformat(value)
        if heartbeat.tzinfo is None:
            heartbeat = heartbeat.replace(tzinfo=UTC)
        return max((datetime.now(UTC) - heartbeat).total_seconds(), 0.0)

    @staticmethod
    def _default_worker_command(state_path: Path, job_id: str) -> list[str]:
        return [
            sys.executable,
            '-m',
            'app.services.platform_worker',
            '--state-path',
            str(state_path),
            '--job-id',
            job_id,
        ]


platform_dispatcher = PlatformJobDispatcher(platform_store)
