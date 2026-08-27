from __future__ import annotations

import argparse
from pathlib import Path

from app.services.platform_store import PlatformStore


def execute_job(state_path: Path, job_id: str) -> int:
    store = PlatformStore(state_path=state_path, recover_orphans=False)
    try:
        completed = store.execute_running_job(job_id)
    except Exception as exc:
        failure_store = PlatformStore(state_path=state_path, recover_orphans=False)
        failure_store.fail_running_job(job_id, code='EXECUTOR_ERROR', message=str(exc))
        return 1
    return 0 if completed.status in ('SUCCEEDED', 'CANCELLED') else 1


def main() -> int:
    parser = argparse.ArgumentParser(description='执行一个平台任务的计算主体')
    parser.add_argument('--state-path', type=Path, required=True)
    parser.add_argument('--job-id', required=True)
    args = parser.parse_args()
    return execute_job(args.state_path.resolve(), args.job_id)


if __name__ == '__main__':
    raise SystemExit(main())
