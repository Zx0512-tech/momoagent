from __future__ import annotations

import argparse
import logging
import subprocess
import sys
import time
from pathlib import Path

from app.services.platform_store import PlatformStore

logger = logging.getLogger(__name__)


def run_worker(
    state_path: Path,
    job_id: str,
    *,
    heartbeat_interval_s: float = 5.0,
    executor_command: list[str] | None = None,
) -> int:
    deadline = time.monotonic() + 5.0
    while True:
        store = PlatformStore(state_path=state_path, recover_orphans=False)
        job = store.get_job(job_id)
        if job.status != 'QUEUED' or time.monotonic() >= deadline:
            break
        time.sleep(0.02)
    if job.status != 'RUNNING':
        return 3

    store.record_worker_heartbeat(job_id, phase='worker 已接管')
    try:
        executor = subprocess.Popen(executor_command or _default_executor_command(state_path, job_id))
    except Exception as exc:
        failure_store = PlatformStore(state_path=state_path, recover_orphans=False)
        failure_store.fail_running_job(job_id, code='EXECUTOR_LAUNCH_ERROR', message=str(exc))
        failure_store.record_worker_exit(job_id, exit_code=1)
        return 1

    while True:
        try:
            exit_code = executor.wait(timeout=heartbeat_interval_s)
            break
        except subprocess.TimeoutExpired:
            heartbeat_store = PlatformStore(state_path=state_path, recover_orphans=False)
            heartbeat_store.record_worker_heartbeat(job_id, phase='求解中')
            # 求解子进程把 case 进度写在文件里，这里顺带汇总进任务状态。
            try:
                heartbeat_store.record_case_progress(job_id)
            except Exception:
                # 进度只是观测信号，读取或格式异常不能终止求解监督循环。
                logger.exception(
                    '求解进度汇总失败，继续监督执行子进程',
                    extra={'event': 'job_progress_collection_failed', 'job_id': job_id},
                )

    exit_store = PlatformStore(state_path=state_path, recover_orphans=False)
    completed = exit_store.get_job(job_id)
    if completed.status == 'RUNNING':
        exit_store.fail_running_job(
            job_id,
            code='EXECUTOR_INCOMPLETE',
            message=f'执行子进程退出但任务未写入完成状态，exit_code={exit_code}',
        )
        exit_code = exit_code or 1
    exit_store.record_worker_exit(job_id, exit_code=exit_code)
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
