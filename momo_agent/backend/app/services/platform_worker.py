from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

from app.api.v1.schemas import Job, JobError, JobProgress
from app.core.logging_config import get_platform_logger
from app.services.platform_repository import SQLitePlatformRepository
from app.services.platform_store import collect_case_progress, utc_now

logger = get_platform_logger('worker')


def _load_job(repository: SQLitePlatformRepository, job_id: str) -> Job | None:
    payload = repository.load_job(job_id)
    return Job.model_validate(payload) if payload is not None else None


def record_worker_observation(
    repository: SQLitePlatformRepository,
    job_id: str,
    *,
    phase: str = '执行中',
) -> Job | None:
    """只读取当前 Job 并原子写回心跳/进度，避免加载全部历史制品。"""

    job = _load_job(repository, job_id)
    if job is None or job.status != 'RUNNING' or job.worker is None:
        return job
    job.worker.heartbeat_at = utc_now()
    try:
        progress = collect_case_progress(job)
    except Exception:
        logger.exception(
            '求解进度汇总失败，保留上一帧并继续记录心跳',
            extra={
                'event': 'job_progress_collection_failed',
                'job_id': job_id,
                'worker_pid': job.worker.pid,
            },
        )
        progress = None
    if progress is not None:
        job.progress = progress
    elif job.progress is None:
        job.progress = JobProgress(phase=phase, message='worker 心跳正常', percent=None)
    else:
        job.progress = job.progress.model_copy(
            update={'phase': phase, 'message': 'worker 心跳正常'}
        )
    if repository.update_job(
        job.model_dump(by_alias=True, mode='json'), expected_status='RUNNING'
    ):
        return job
    return _load_job(repository, job_id)


def _fail_running_job(
    repository: SQLitePlatformRepository,
    job_id: str,
    *,
    code: str,
    message: str,
    exit_code: int | None = None,
) -> Job | None:
    job = _load_job(repository, job_id)
    if job is None or job.status not in ('QUEUED', 'RUNNING'):
        return job
    observed_status = job.status
    job.status = 'FAILED'
    job.finished_at = utc_now()
    job.error = JobError(code=code, message=message)
    job.progress = JobProgress(phase='执行失败', message=message, percent=None)
    if job.worker is not None:
        job.worker.exit_code = exit_code
    if repository.update_job(
        job.model_dump(by_alias=True, mode='json'), expected_status=observed_status
    ):
        return job
    return _load_job(repository, job_id)


def _record_worker_exit(
    repository: SQLitePlatformRepository,
    job_id: str,
    *,
    exit_code: int,
) -> Job | None:
    for _ in range(2):
        job = _load_job(repository, job_id)
        if job is None or job.worker is None:
            return job
        observed_status = job.status
        job.worker.exit_code = exit_code
        if repository.update_job(
            job.model_dump(by_alias=True, mode='json'), expected_status=observed_status
        ):
            return job
    return _load_job(repository, job_id)


def run_worker(
    state_path: Path,
    job_id: str,
    *,
    heartbeat_interval_s: float = 5.0,
    executor_command: list[str] | None = None,
) -> int:
    repository = SQLitePlatformRepository(state_path)
    deadline = time.monotonic() + 5.0
    while True:
        job = _load_job(repository, job_id)
        if job is None:
            return 3
        if job.status != 'QUEUED' or time.monotonic() >= deadline:
            break
        time.sleep(0.02)
    if job.status != 'RUNNING':
        return 3

    try:
        record_worker_observation(repository, job_id, phase='worker 已接管')
    except Exception:
        logger.exception(
            'worker 初始观测写入失败，继续启动执行子进程',
            extra={'event': 'worker_heartbeat_failed', 'job_id': job_id, 'worker_pid': os.getpid()},
        )
    try:
        executor = subprocess.Popen(executor_command or _default_executor_command(state_path, job_id))
    except Exception as exc:
        _fail_running_job(repository, job_id, code='EXECUTOR_LAUNCH_ERROR', message=str(exc))
        _record_worker_exit(repository, job_id, exit_code=1)
        return 1

    while True:
        try:
            exit_code = executor.wait(timeout=heartbeat_interval_s)
            break
        except subprocess.TimeoutExpired:
            try:
                record_worker_observation(repository, job_id, phase='求解中')
            except Exception:
                logger.exception(
                    'worker 心跳或进度写入失败，继续监督执行子进程',
                    extra={
                        'event': 'worker_heartbeat_failed',
                        'job_id': job_id,
                        'worker_pid': os.getpid(),
                        'executor_pid': executor.pid,
                    },
                )

    completed = _load_job(repository, job_id)
    if completed is not None and completed.status == 'RUNNING':
        _fail_running_job(
            repository,
            job_id,
            code='EXECUTOR_INCOMPLETE',
            message=f'执行子进程退出但任务未写入完成状态，exit_code={exit_code}',
        )
        exit_code = exit_code or 1
    _record_worker_exit(repository, job_id, exit_code=exit_code)
    return exit_code


def _default_executor_command(state_path: Path, job_id: str) -> list[str]:
    return [
        sys.executable,
        '-m',
        'app.services.platform_job_executor',
        '--state-path',
        str(state_path),
        '--job-id',
        job_id,
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description='执行一个持久化平台任务')
    parser.add_argument('--state-path', type=Path, required=True)
    parser.add_argument('--job-id', required=True)
    parser.add_argument('--heartbeat-interval-s', type=float, default=5.0)
    args = parser.parse_args()
    return run_worker(args.state_path.resolve(), args.job_id, heartbeat_interval_s=args.heartbeat_interval_s)


if __name__ == '__main__':
    raise SystemExit(main())
