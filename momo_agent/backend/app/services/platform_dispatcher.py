from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from app.api.v1.schemas import Job
from app.core.logging_config import get_platform_logger
from app.services.platform_processes import process_exists, terminate_process_tree
from app.services.platform_store import (
    PlatformStore,
    collect_case_progress,
    job_progress_dir,
    platform_store,
)


WorkerCommandFactory = Callable[[Path, str], list[str]]
logger = get_platform_logger("dispatcher")

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
            elif job.worker is not None and process_exists(job.worker.pid):
                terminate_process_tree(job.worker.pid)
                logger.info(
                    'recovered worker terminated by cancellation',
                    extra={
                        'event': 'dispatcher_process_tree_terminated',
                        'job_id': job_id,
                        'worker_pid': job.worker.pid,
                        'termination_reason': 'CANCELLED',
                    },
                )
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
            recovered_jobs = [job for job in reversed(self.store.jobs) if job.status == 'RUNNING']
            if recovered_jobs:
                for recovered in recovered_jobs:
                    self._inspect_recovered_process(recovered)
                return
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

    def _inspect_recovered_process(self, job: Job) -> None:
        """服务重启后监督仍存活的独立 worker，等待其自行登记终态。"""

        worker = job.worker
        if worker is None or not process_exists(worker.pid):
            logger.error(
                'recovered worker process is missing',
                extra={
                    'event': 'dispatcher_job_failed',
                    'job_id': job.job_id,
                    'worker_pid': worker.pid if worker is not None else None,
                    'termination_reason': 'WORKER_EXITED',
                },
            )
            self.store.fail_running_job(
                job.job_id,
                code='WORKER_EXITED',
                message='服务恢复后未找到原工作进程，任务未登记完成状态',
            )
            return
        execution_timeout_s = self._execution_timeout_seconds(job)
        if (
            execution_timeout_s is not None
            and job.started_at
            and self._heartbeat_age_seconds(job.started_at) > execution_timeout_s
        ):
            terminate_process_tree(worker.pid)
            logger.error(
                'recovered worker exceeded execution timeout',
                extra={
                    'event': 'dispatcher_job_failed',
                    'job_id': job.job_id,
                    'worker_pid': worker.pid,
                    'termination_reason': 'EXECUTION_TIMEOUT',
                },
            )
            self.store.fail_running_job(
                job.job_id,
                code='EXECUTION_TIMEOUT',
                message=f'作业墙钟时间超过 {execution_timeout_s:g} 秒，进程树已终止',
            )
            return
        if worker.heartbeat_at and self._heartbeat_age_seconds(worker.heartbeat_at) > self.heartbeat_timeout_s:
            if self._preserve_worker_with_fresh_solver_progress(job):
                return
            terminate_process_tree(worker.pid)
            logger.error(
                'recovered worker exceeded heartbeat timeout',
                extra={
                    'event': 'dispatcher_job_failed',
                    'job_id': job.job_id,
                    'worker_pid': worker.pid,
                    'termination_reason': 'HEARTBEAT_TIMEOUT',
                },
            )
            self.store.fail_running_job(
                job.job_id,
                code='HEARTBEAT_TIMEOUT',
                message=f'worker 心跳超过 {self.heartbeat_timeout_s:g} 秒未更新，进程树已终止',
            )

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
            if self._preserve_worker_with_fresh_solver_progress(job):
                return
            terminate_process_tree(self._active_process.pid)
            self._active_process.wait(timeout=10)
            self.store.fail_running_job(
                job.job_id,
                code='HEARTBEAT_TIMEOUT',
                message=f'worker 心跳超过 {self.heartbeat_timeout_s:g} 秒未更新，进程树已终止',
            )
            self._active_process = None
            self._active_job_id = None

    def _preserve_worker_with_fresh_solver_progress(self, job: Job) -> bool:
        """求解文件仍在推进时容忍陈旧心跳，避免误杀真实计算。"""

        directory = job_progress_dir(job.job_id)
        cutoff = time.time() - self.heartbeat_timeout_s
        try:
            fresh = any(
                path.is_file() and path.stat().st_mtime >= cutoff
                for path in directory.glob('*.json')
            )
        except OSError:
            return False
        if not fresh:
            return False
        try:
            if collect_case_progress(job) is None:
                return False
        except Exception:
            return False
        try:
            self.store.record_worker_observation(job.job_id, phase='求解中')
        except Exception:
            logger.exception(
                'solver 进度仍在推进，但 dispatcher 无法代写 worker 观测',
                extra={
                    'event': 'dispatcher_progress_liveness_write_failed',
                    'job_id': job.job_id,
                    'worker_pid': job.worker.pid if job.worker else None,
                },
            )
        logger.warning(
            'stale worker heartbeat tolerated because solver progress is fresh',
            extra={
                'event': 'dispatcher_progress_liveness_preserved',
                'job_id': job.job_id,
                'worker_pid': job.worker.pid if job.worker else None,
            },
        )
        return True

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
