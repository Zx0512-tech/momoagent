"""进度文件 → 任务状态的汇总。

写入方在求解子进程，读取方在 worker 进程，两者只通过 job_id 派生的目录
相遇，所以这里同时验证路径派生与汇总逻辑。
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import HTTPException
from pyansys_bridge.core.progress_sink import write_batch_progress, write_case_progress

from app.services.platform_store import PlatformStore, job_progress_dir

from tests.test_platform_job_reliability import create_queued_test_job

# 进度根目录的 tmp_path 隔离由 conftest 的 isolate_job_progress_root 统一负责。


def running_job(store: PlatformStore):
    job = create_queued_test_job(store)
    store.mark_job_running(job.job_id, pid=1234)
    return store.get_job(job.job_id)


def test_progress_dir_is_derived_from_job_id_only() -> None:
    """两个进程必须各自算出同一个目录，不能靠传参。"""

    assert job_progress_dir('job_abc') == job_progress_dir('job_abc')
    assert job_progress_dir('job_abc') != job_progress_dir('job_def')
    assert job_progress_dir('job_abc').name == 'job_abc'


def test_progress_root_honours_env_override(tmp_path: Path) -> None:
    """worker 是独立子进程，只能靠继承的环境变量对齐根目录。

    在子进程里验证，避免 reload 重建本进程的 platform_store 单例。
    """

    import os
    import subprocess
    import sys

    backend_dir = Path(__file__).resolve().parents[1]
    target = tmp_path / '_env_probe'
    env = {
        **os.environ,
        'MOMO_PLATFORM_PROGRESS_ROOT': str(target),
        # pytest.ini 的 pythonpath=../.. 只作用于 pytest 进程，这里要自己给。
        'PYTHONPATH': os.pathsep.join([str(backend_dir), str(backend_dir.parents[1])]),
    }
    completed = subprocess.run(
        [
            sys.executable,
            '-c',
            'from app.services.platform_store import job_progress_dir;'
            "print(job_progress_dir('job_x'))",
        ],
        cwd=backend_dir,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )

    assert completed.stdout.strip() == str(target / 'job_x')


def test_no_progress_files_leaves_job_untouched(tmp_path: Path) -> None:
    store = PlatformStore(state_path=tmp_path / 'state.sqlite3')
    job = running_job(store)
    before = job.progress

    updated = store.record_case_progress(job.job_id)

    assert updated.progress == before


def test_batch_counts_are_summarised(tmp_path: Path) -> None:
    store = PlatformStore(state_path=tmp_path / 'state.sqlite3')
    job = running_job(store)
    write_batch_progress(job_progress_dir(job.job_id), completed=8, total=15)

    updated = store.record_case_progress(job.job_id)

    assert updated.progress.completed_cases == 8
    assert updated.progress.total_cases == 15
    assert '8/15' in updated.progress.message


def test_batch_completion_updates_primary_percent(tmp_path: Path) -> None:
    """主进度条必须反映真实批次完成率，不能一直停在 worker 启动值。"""

    store = PlatformStore(state_path=tmp_path / 'state.sqlite3')
    job = running_job(store)
    write_batch_progress(job_progress_dir(job.job_id), completed=8, total=15)

    updated = store.record_case_progress(job.job_id)

    assert updated.progress.percent == 53


def test_active_case_prevents_batch_progress_from_reaching_100(tmp_path: Path) -> None:
    """批次计数已满但仍有活动算例时，主进度必须保持未完成。"""

    store = PlatformStore(state_path=tmp_path / 'state.sqlite3')
    job = running_job(store)
    directory = job_progress_dir(job.job_id)
    write_batch_progress(directory, completed=15, total=15)
    write_case_progress(directory, 'case_still_running', step=6, total_steps=100)

    updated = store.record_case_progress(job.job_id)

    assert updated.progress.percent == 99
    assert updated.progress.active_cases[0].case_id == 'case_still_running'


def test_running_job_never_reports_complete_percent_from_batch_count(tmp_path: Path) -> None:
    """批次计数先完成但 Job 仍 RUNNING 时，必须等终态再显示 100%。"""

    store = PlatformStore(state_path=tmp_path / 'state.sqlite3')
    job = running_job(store)
    write_batch_progress(job_progress_dir(job.job_id), completed=15, total=15)

    updated = store.record_case_progress(job.job_id)

    assert updated.status == 'RUNNING'
    assert updated.progress.percent == 99


def test_active_cases_exclude_finished_ones(tmp_path: Path) -> None:
    """已经 100% 的算例不再算"正在求解"，否则计数永远不降。"""

    store = PlatformStore(state_path=tmp_path / 'state.sqlite3')
    job = running_job(store)
    directory = job_progress_dir(job.job_id)
    write_case_progress(directory, 'case_running', step=120, total_steps=400)
    write_case_progress(directory, 'case_done', step=400, total_steps=400)

    updated = store.record_case_progress(job.job_id)

    assert [case.case_id for case in updated.progress.active_cases] == ['case_running']
    assert updated.progress.active_cases[0].percent == 30
    assert updated.progress.active_cases[0].step == 120
    assert updated.progress.active_cases[0].total_steps == 400
    assert updated.progress.percent == 30


def test_invalid_case_progress_is_ignored_without_breaking_valid_progress(tmp_path: Path) -> None:
    """进度是观测信号，字段损坏不能让 worker 或真实求解失败。"""

    store = PlatformStore(state_path=tmp_path / 'state.sqlite3')
    job = running_job(store)
    directory = job_progress_dir(job.job_id)
    write_case_progress(directory, 'case_valid', step=40, total_steps=100)
    (directory / 'case_invalid.json').write_text(
        '{"caseId":"case_invalid","percent":"unknown","step":{},"totalSteps":100}',
        encoding='utf-8',
    )

    updated = store.record_case_progress(job.job_id)

    assert updated.status == 'RUNNING'
    assert [case.case_id for case in updated.progress.active_cases] == ['case_valid']
    assert updated.progress.percent == 40


def test_case_progress_survives_reload(tmp_path: Path) -> None:
    """API 进程是另一个进程，必须能从 SQLite 读到进度。"""

    state_path = tmp_path / 'state.sqlite3'
    store = PlatformStore(state_path=state_path)
    job = running_job(store)
    write_batch_progress(job_progress_dir(job.job_id), completed=3, total=5)
    store.record_case_progress(job.job_id)

    reloaded = PlatformStore(state_path=state_path, recover_orphans=False)

    assert reloaded.get_job(job.job_id).progress.completed_cases == 3


def test_non_running_job_is_not_updated(tmp_path: Path) -> None:
    store = PlatformStore(state_path=tmp_path / 'state.sqlite3')
    job = create_queued_test_job(store)
    write_batch_progress(job_progress_dir(job.job_id), completed=1, total=2)

    updated = store.record_case_progress(job.job_id)

    assert updated.status == 'QUEUED'
    assert updated.progress is None or updated.progress.completed_cases is None


def test_stage_percent_is_preserved_when_no_numeric_solver_progress_exists(tmp_path: Path) -> None:
    """只有心跳而没有可计算的求解进度时，保留既有阶段百分比。"""

    store = PlatformStore(state_path=tmp_path / 'state.sqlite3')
    job = running_job(store)
    percent_before = job.progress.percent
    directory = job_progress_dir(job.job_id)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'case_invalid.json').write_text(
        '{"caseId":"case_invalid","percent":"unknown"}', encoding='utf-8'
    )

    updated = store.record_case_progress(job.job_id)

    assert updated.progress.percent == percent_before


def test_case_progress_does_not_clobber_a_terminal_status(tmp_path: Path) -> None:
    """worker 心跳读到 RUNNING 之后，执行子进程可能刚写入 SUCCEEDED。

    盲写会把终态覆盖回 RUNNING，worker 随后误判成 EXECUTOR_INCOMPLETE。
    这里用两个 store 模拟两个进程：进度方先读，执行方后写终态。
    """

    state_path = tmp_path / 'state.sqlite3'
    progress_store = PlatformStore(state_path=state_path)
    job = running_job(progress_store)
    write_batch_progress(job_progress_dir(job.job_id), completed=2, total=3)

    # 进度方已经拿到 RUNNING 副本（refresh 在 record_case_progress 内部），
    # 在它写回之前让另一个进程写入终态。
    executor_store = PlatformStore(state_path=state_path, recover_orphans=False)
    terminal = executor_store.get_job(job.job_id)
    terminal.status = 'SUCCEEDED'
    assert executor_store.repository.update_job(
        terminal.model_dump(by_alias=True, mode='json'), expected_status='RUNNING'
    )

    returned = progress_store.record_case_progress(job.job_id)

    assert returned.status == 'SUCCEEDED'
    reloaded = PlatformStore(state_path=state_path, recover_orphans=False)
    assert reloaded.get_job(job.job_id).status == 'SUCCEEDED'


def test_heartbeat_does_not_clobber_a_terminal_status(tmp_path: Path) -> None:
    state_path = tmp_path / 'state.sqlite3'
    heartbeat_store = PlatformStore(state_path=state_path)
    job = running_job(heartbeat_store)

    executor_store = PlatformStore(state_path=state_path, recover_orphans=False)
    terminal = executor_store.get_job(job.job_id)
    terminal.status = 'SUCCEEDED'
    executor_store.repository.update_job(
        terminal.model_dump(by_alias=True, mode='json'), expected_status='RUNNING'
    )

    heartbeat_store.record_worker_heartbeat(job.job_id, phase='求解中')

    reloaded = PlatformStore(state_path=state_path, recover_orphans=False)
    assert reloaded.get_job(job.job_id).status == 'SUCCEEDED'


def test_fail_running_job_loses_to_a_concurrent_terminal_write(tmp_path: Path) -> None:
    """worker 判定 EXECUTOR_INCOMPLETE 时，成功状态若已落库则不得改判为失败。"""

    state_path = tmp_path / 'state.sqlite3'
    worker_store = PlatformStore(state_path=state_path)
    job = running_job(worker_store)
    worker_store.refresh()  # worker 侧持有 RUNNING 副本

    executor_store = PlatformStore(state_path=state_path, recover_orphans=False)
    terminal = executor_store.get_job(job.job_id)
    terminal.status = 'SUCCEEDED'
    executor_store.repository.update_job(
        terminal.model_dump(by_alias=True, mode='json'), expected_status='RUNNING'
    )

    # worker_store 缓存里仍是 RUNNING，但 fail_running_job 会 refresh 后条件写。
    returned = worker_store.fail_running_job(job.job_id, code='EXECUTOR_INCOMPLETE', message='x')

    assert returned.status == 'SUCCEEDED'
    reloaded = PlatformStore(state_path=state_path, recover_orphans=False)
    assert reloaded.get_job(job.job_id).status == 'SUCCEEDED'


def test_worker_exit_code_is_recorded_on_a_terminal_job(tmp_path: Path) -> None:
    """退出码与状态无关，终态任务上也必须写进去。"""

    state_path = tmp_path / 'state.sqlite3'
    store = PlatformStore(state_path=state_path)
    job = running_job(store)
    terminal = store.get_job(job.job_id)
    terminal.status = 'SUCCEEDED'
    store.repository.update_job(
        terminal.model_dump(by_alias=True, mode='json'), expected_status='RUNNING'
    )

    store.record_worker_exit(job.job_id, exit_code=0)

    reloaded = PlatformStore(state_path=state_path, recover_orphans=False)
    persisted = reloaded.get_job(job.job_id)
    assert persisted.status == 'SUCCEEDED'
    assert persisted.worker.exit_code == 0


def test_update_job_still_raises_for_a_missing_job(tmp_path: Path) -> None:
    store = PlatformStore(state_path=tmp_path / 'state.sqlite3')
    job = running_job(store)
    payload = store.get_job(job.job_id).model_dump(by_alias=True, mode='json')
    payload['jobId'] = 'job_does_not_exist'

    with pytest.raises(KeyError):
        store.repository.update_job(payload)
    with pytest.raises(KeyError):
        store.repository.update_job(payload, expected_status='RUNNING')


class _BrokenStore:
    """模拟进度通道故障：读 Job 本身就炸。"""

    def get_job(self, job_id: str):
        raise sqlite3.DatabaseError('database disk image is malformed')


@pytest.fixture
def platform_log_records(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[logging.LogRecord]]:
    """平台 logger 设了 propagate=False，caplog 抓不到，直接挂一个收集 handler。"""

    records: list[logging.LogRecord] = []

    class _Collector(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    logger = logging.getLogger('momo.agent_service')
    handler = _Collector(level=logging.DEBUG)
    logger.addHandler(handler)
    monkeypatch.setattr(logger, 'level', logging.DEBUG, raising=False)
    try:
        yield records
    finally:
        logger.removeHandler(handler)


def test_broken_progress_channel_is_logged_and_surfaced(
    monkeypatch: pytest.MonkeyPatch, platform_log_records: list[logging.LogRecord]
) -> None:
    """读不到进度不能表现成“不支持进度”，要留日志并给前端可见标记。"""

    from app.services import agent_service as agent_service_module

    monkeypatch.setattr(agent_service_module, 'platform_store', _BrokenStore())
    progress = agent_service_module.AgentService._job_progress('job_broken')

    assert progress is not None
    assert '进度不可用' in progress['phase']
    records = [r for r in platform_log_records if getattr(r, 'event', None) == 'job_progress_read_failed']
    assert records and records[0].job_id == 'job_broken'
    assert records[0].exc_info is not None


def test_missing_job_logs_a_warning_and_returns_none(
    monkeypatch: pytest.MonkeyPatch, platform_log_records: list[logging.LogRecord]
) -> None:
    """Job 不存在是正常情况（该 run 没有求解通道），只留 warning。"""

    from app.services import agent_service as agent_service_module

    class _EmptyStore:
        def get_job(self, job_id: str):
            raise HTTPException(status_code=404, detail={'code': 'NOT_FOUND'})

    monkeypatch.setattr(agent_service_module, 'platform_store', _EmptyStore())
    progress = agent_service_module.AgentService._job_progress('job_gone')

    assert progress is None
    assert any(getattr(r, 'event', None) == 'job_progress_job_missing' for r in platform_log_records)


def test_absent_progress_is_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import agent_service as agent_service_module

    class _Job:
        progress = None

    class _Store:
        def get_job(self, job_id: str):
            return _Job()

    monkeypatch.setattr(agent_service_module, 'platform_store', _Store())

    assert agent_service_module.AgentService._job_progress('job_1') is None
    assert agent_service_module.AgentService._job_progress(None) is None
