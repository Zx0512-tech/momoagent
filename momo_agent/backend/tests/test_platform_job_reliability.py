from __future__ import annotations

import os
import json
import math
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pyansys_bridge.core.progress_sink import write_case_progress

from app.main import app
from app.services import platform_worker as platform_worker_module
from app.services.platform_dispatcher import PlatformJobDispatcher, process_exists
from app.services.platform_repository import SQLitePlatformRepository
from app.services.platform_store import PlatformStore, job_progress_dir, platform_store
from app.services.platform_worker import run_worker


def wait_until(predicate, *, timeout_s: float = 10.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError('等待平台任务状态超时')


def test_refresh_skips_full_reload_when_state_revision_unchanged(tmp_path: Path) -> None:
    """轮询驱动的 refresh 在版本戳未变时跳过全量重载，绕过 persist 的写入仍会触发重载。"""
    store = PlatformStore(state_path=tmp_path / 'state' / 'platform.sqlite3', recover_orphans=False)

    calls = {'load': 0}
    original_load = store.repository.load

    def counting_load():
        calls['load'] += 1
        return original_load()

    store.repository.load = counting_load

    # 初始化时 persist 已同步版本标记；版本未变的轮询不再全量重载。
    store.refresh()
    store.refresh()
    assert calls['load'] == 0

    # update_job 绕过 persist 直接写库（worker 心跳/终态路径），必须推进版本戳。
    job_payload = store.jobs[0].model_dump(by_alias=True, mode='json')
    store.repository.update_job(job_payload)
    store.refresh()
    assert calls['load'] == 1

    # 重载后标记再次同步，后续轮询继续跳过。
    store.refresh()
    assert calls['load'] == 1


QUEUE_TEST_PARAMS = {
    'solver': 'ANSYS',
    'bridgeId': 'stbridge',
    'caseSetId': 'queue_reliability',
    'moduleConfig': {
        'modules': ['MODEL_IMPORT'],
        'damper': {},
        'responseTargets': ['max_girder_end_displacement'],
    },
}


def create_queued_test_job(store: PlatformStore):
    """用已实现的命令流能力验证队列，不重新开放占位求解器。"""
    original = store._should_queue_job
    store._should_queue_job = lambda *_args, **_kwargs: True
    try:
        return store.create_job('COMMAND_STREAM_ASSEMBLY', dict(QUEUE_TEST_PARAMS))
    finally:
        store._should_queue_job = original


def post_queued_test_job(client: TestClient):
    original = platform_store._should_queue_job
    platform_store._should_queue_job = lambda *_args, **_kwargs: True
    try:
        return client.post(
            '/api/v1/jobs',
            json={'type': 'COMMAND_STREAM_ASSEMBLY', 'params': QUEUE_TEST_PARAMS},
        )
    finally:
        platform_store._should_queue_job = original


def test_implemented_job_is_queued_and_persisted_in_sqlite(tmp_path: Path) -> None:
    state_path = tmp_path / 'platform_state.sqlite3'
    store = PlatformStore(state_path=state_path)

    job = create_queued_test_job(store)

    assert job.status == 'QUEUED'
    assert job.started_at is None
    assert job.finished_at is None
    assert job.artifacts == []
    assert state_path.read_bytes().startswith(b'SQLite format 3\x00')

    reloaded = PlatformStore(state_path=state_path)
    assert reloaded.get_job(job.job_id).status == 'QUEUED'


@pytest.mark.parametrize(
    'run_mode',
    ['REAL_DAMPER_COMPARISON', 'REAL_DAMPER_PARAMETER_SWEEP'],
)
def test_controlled_damper_batches_cross_the_worker_queue_boundary(
    tmp_path: Path,
    run_mode: str,
) -> None:
    """对比和参数批量都必须交给 dispatcher，不能在 HTTP 线程内同步求解。"""
    store = PlatformStore(state_path=tmp_path / f'{run_mode}.sqlite3', recover_orphans=False)

    assert store._should_queue_job(
        'SOLVER_BATCH',
        {
            'runMode': run_mode,
            'cases': [{'caseId': 'case_1'}],
        },
    ) is True


def test_stale_store_persist_does_not_delete_concurrent_jobs_or_artifacts(tmp_path: Path) -> None:
    state_path = tmp_path / 'platform_state.sqlite3'
    stale_store = PlatformStore(state_path=state_path, recover_orphans=False)
    concurrent_store = PlatformStore(state_path=state_path, recover_orphans=False)
    concurrent_job = create_queued_test_job(concurrent_store)
    concurrent_artifact = concurrent_store.register_artifact(
        kind='JSON_SUMMARY',
        name='concurrent.json',
        path='output/concurrent.json',
        mime_type='application/json',
        preview={'source': 'concurrent'},
    )
    concurrent_store.save_engineering_config({'projectConfig': {'projectName': 'concurrent'}})

    stale_store.persist()

    reloaded = PlatformStore(state_path=state_path, recover_orphans=False)
    assert reloaded.get_job(concurrent_job.job_id).status == 'QUEUED'
    assert reloaded.get_artifact(concurrent_artifact.artifact_id).preview == {'source': 'concurrent'}
    assert reloaded.engineering_config == {'projectConfig': {'projectName': 'concurrent'}}


def test_state_transaction_blocks_concurrent_refresh(tmp_path: Path) -> None:
    state_path = tmp_path / 'platform_state.sqlite3'
    store = PlatformStore(state_path=state_path, recover_orphans=False)
    refresh_started = threading.Event()
    refresh_finished = threading.Event()

    def refresh_in_background() -> None:
        refresh_started.set()
        store.refresh()
        refresh_finished.set()

    with store.state_transaction():
        thread = threading.Thread(target=refresh_in_background)
        thread.start()
        assert refresh_started.wait(timeout=1)
        assert not refresh_finished.wait(timeout=0.1)

    thread.join(timeout=1)
    assert not thread.is_alive()
    assert refresh_finished.is_set()


def test_synchronous_job_does_not_block_refresh_while_handler_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_path = tmp_path / 'platform_state.sqlite3'
    store = PlatformStore(state_path=state_path, recover_orphans=False)
    handler_started = threading.Event()
    allow_handler = threading.Event()
    refresh_started = threading.Event()
    refresh_finished = threading.Event()
    created: list = []

    def delayed_generate(*_args, **_kwargs):
        handler_started.set()
        allow_handler.wait(timeout=5)
        return []

    def create_in_background() -> None:
        created.append(store.create_job('PROJECT_GATE_FAST', {}))

    def refresh_in_background() -> None:
        refresh_started.set()
        store.refresh()
        refresh_finished.set()

    monkeypatch.setattr(store, '_generate_artifacts', delayed_generate)
    create_thread = threading.Thread(target=create_in_background)
    refresh_thread = threading.Thread(target=refresh_in_background)
    create_thread.start()
    assert handler_started.wait(timeout=1)
    refresh_thread.start()
    assert refresh_started.wait(timeout=1)
    assert refresh_finished.wait(timeout=1)
    allow_handler.set()
    create_thread.join(timeout=2)
    refresh_thread.join(timeout=2)

    assert not create_thread.is_alive()
    assert not refresh_thread.is_alive()
    assert created[0].status == 'SUCCEEDED'
    persisted = PlatformStore(state_path=state_path, recover_orphans=False).get_job(created[0].job_id)
    assert persisted.status == 'SUCCEEDED'


def test_jobs_api_returns_202_for_queued_implemented_job() -> None:
    response = post_queued_test_job(TestClient(app))

    assert response.status_code == 202
    assert response.json()['status'] == 'QUEUED'


def test_cancelled_queued_job_survives_reload(tmp_path: Path) -> None:
    state_path = tmp_path / 'platform_state.sqlite3'
    store = PlatformStore(state_path=state_path)
    job = create_queued_test_job(store)

    assert store.cancel_job(job.job_id) == {'success': True}

    reloaded = PlatformStore(state_path=state_path)
    cancelled = reloaded.get_job(job.job_id)
    assert cancelled.status == 'CANCELLED'
    assert cancelled.finished_at is not None


def test_orphaned_running_job_is_failed_on_reload(tmp_path: Path) -> None:
    state_path = tmp_path / 'platform_state.sqlite3'
    store = PlatformStore(state_path=state_path)
    job = create_queued_test_job(store)
    job.status = 'RUNNING'
    job.started_at = job.created_at
    store.persist()

    reloaded = PlatformStore(state_path=state_path)
    recovered = reloaded.get_job(job.job_id)
    assert recovered.status == 'FAILED'
    assert recovered.finished_at is not None
    assert recovered.error is not None
    assert recovered.error.code == 'WORKER_RESTARTED'


def test_live_worker_is_not_failed_when_service_reloads(tmp_path: Path) -> None:
    """服务重启时 worker 仍存活，Job 必须保持 RUNNING 等待其登记结果。"""

    state_path = tmp_path / 'platform_state.sqlite3'
    process = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
    try:
        store = PlatformStore(state_path=state_path)
        job = create_queued_test_job(store)
        store.mark_job_running(job.job_id, pid=process.pid)

        recovered = PlatformStore(state_path=state_path).get_job(job.job_id)

        assert recovered.status == 'RUNNING'
        assert recovered.error is None
    finally:
        process.terminate()
        process.wait(timeout=10)


def test_artifact_bytes_are_file_backed_next_to_sqlite(tmp_path: Path) -> None:
    state_path = tmp_path / 'platform_state.sqlite3'
    store = PlatformStore(state_path=state_path)
    artifact = store.register_artifact(
        kind='JSON_SUMMARY',
        name='evidence.json',
        path='output/evidence.json',
        mime_type='application/json',
        preview={'accepted': True},
    )

    artifact_path = state_path.parent / 'artifacts' / f'{artifact.artifact_id}.bin'
    assert artifact_path.read_bytes()

    reloaded = PlatformStore(state_path=state_path)
    assert reloaded.get_artifact(artifact.artifact_id).content == artifact_path.read_bytes()


def test_platform_json_artifact_coerces_non_finite_values(tmp_path: Path) -> None:
    store = PlatformStore(state_path=tmp_path / 'platform_state.sqlite3')
    artifact = store.register_artifact(
        kind='JSON_SUMMARY',
        name='non_finite.json',
        path='output/non_finite.json',
        mime_type='application/json',
        preview={'nan': math.nan, 'positiveInfinity': math.inf, 'nested': [-math.inf, 1.5]},
    )

    record = store.get_artifact(artifact.artifact_id)
    parsed = json.loads(
        record.content.decode('utf-8'),
        parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
    )

    assert parsed == {'nan': None, 'positiveInfinity': None, 'nested': [None, 1.5]}
    assert record.preview == parsed


def test_dispatcher_executes_queued_job_in_independent_worker(tmp_path: Path) -> None:
    state_path = tmp_path / 'platform_state.sqlite3'
    store = PlatformStore(state_path=state_path)
    job = create_queued_test_job(store)
    dispatcher = PlatformJobDispatcher(store, poll_interval_s=0.05, heartbeat_timeout_s=5.0)

    dispatcher.start()
    try:
        wait_until(
            lambda: (
                (current := PlatformStore(state_path=state_path, recover_orphans=False).get_job(job.job_id)).status
                in {'SUCCEEDED', 'FAILED'}
                and current.worker is not None
                and current.worker.exit_code is not None
            )
        )
    finally:
        dispatcher.stop()

    completed = PlatformStore(state_path=state_path).get_job(job.job_id)
    assert completed.status == 'SUCCEEDED', completed.error
    assert completed.worker is not None
    assert completed.worker.pid > 0
    assert completed.worker.heartbeat_at is not None
    assert completed.worker.exit_code == 0
    assert completed.artifacts


def test_worker_import_does_not_recover_its_own_running_job(tmp_path: Path) -> None:
    state_path = tmp_path / 'platform_state.sqlite3'
    store = PlatformStore(state_path=state_path)
    job = create_queued_test_job(store)
    store.mark_job_running(job.job_id, pid=12345)
    env = os.environ.copy()
    env['PYTHONPATH'] = os.pathsep.join([str(Path(__file__).resolve().parents[3]), str(Path(__file__).resolve().parents[1])])
    env['MOMO_PLATFORM_STATE_PATH'] = str(state_path)
    env['MOMO_PLATFORM_WORKER'] = '1'

    completed = subprocess.run(
        [
            sys.executable,
            '-c',
            (
                'from app.services.platform_store import platform_store; '
                'print(platform_store is None)'
            ),
        ],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == 'True'
    assert PlatformStore(state_path=state_path, recover_orphans=False).get_job(job.job_id).status == 'RUNNING'


def test_fastapi_lifespan_dispatches_queued_implemented_job() -> None:
    with TestClient(app) as client:
        response = post_queued_test_job(client)
        assert response.status_code == 202
        job_id = response.json()['jobId']

        wait_until(
            lambda: (
                (payload := client.get(f'/api/v1/jobs/{job_id}').json())['status']
                in {'SUCCEEDED', 'FAILED'}
                and (payload.get('worker') or {}).get('exitCode') is not None
            )
        )
        completed = client.get(f'/api/v1/jobs/{job_id}').json()

    assert completed['status'] == 'SUCCEEDED', completed.get('error')
    assert completed['worker']['pid'] > 0
    assert completed['worker']['exitCode'] == 0
    assert completed['artifacts']


def test_worker_heartbeat_survives_repository_reload(tmp_path: Path) -> None:
    state_path = tmp_path / 'platform_state.sqlite3'
    store = PlatformStore(state_path=state_path)
    job = create_queued_test_job(store)

    store.mark_job_running(job.job_id, pid=12345)
    store.record_worker_heartbeat(job.job_id, phase='求解中')

    reloaded = PlatformStore(state_path=state_path, recover_orphans=False).get_job(job.job_id)
    assert reloaded.status == 'RUNNING'
    assert reloaded.worker is not None
    assert reloaded.worker.pid == 12345
    assert reloaded.worker.heartbeat_at is not None
    assert reloaded.progress is not None
    assert reloaded.progress.phase == '求解中'


def test_worker_heartbeat_continues_while_executor_is_busy(tmp_path: Path) -> None:
    state_path = tmp_path / 'platform_state.sqlite3'
    store = PlatformStore(state_path=state_path)
    job = create_queued_test_job(store)
    running = store.mark_job_running(job.job_id, pid=os.getpid())
    initial_heartbeat = running.worker.heartbeat_at
    errors: list[Exception] = []

    def supervise() -> None:
        try:
            run_worker(
                state_path,
                job.job_id,
                heartbeat_interval_s=0.05,
                executor_command=[sys.executable, '-c', 'import time; time.sleep(0.4)'],
            )
        except Exception as exc:
            errors.append(exc)

    worker_thread = threading.Thread(target=supervise)
    worker_thread.start()
    wait_until(
        lambda: (
            PlatformStore(state_path=state_path, recover_orphans=False)
            .get_job(job.job_id)
            .worker.heartbeat_at
            != initial_heartbeat
        ),
        timeout_s=1.0,
    )
    worker_thread.join(timeout=2.0)

    assert not errors
    assert not worker_thread.is_alive()


def test_worker_heartbeat_never_loads_full_artifact_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """心跳只读取当前 Job，不能随历史制品增长而反复加载全部二进制。"""

    state_path = tmp_path / 'platform_state.sqlite3'
    store = PlatformStore(state_path=state_path)
    job = create_queued_test_job(store)
    running = store.mark_job_running(job.job_id, pid=os.getpid())
    initial_heartbeat = running.worker.heartbeat_at

    def reject_full_state_load(_self):
        raise AssertionError('worker heartbeat attempted a full artifact-state load')

    monkeypatch.setattr(SQLitePlatformRepository, 'load', reject_full_state_load)

    exit_code = run_worker(
        state_path,
        job.job_id,
        heartbeat_interval_s=0.03,
        executor_command=[sys.executable, '-c', 'import time; time.sleep(0.1)'],
    )

    assert exit_code == 1
    persisted = SQLitePlatformRepository(state_path).load_job(job.job_id)
    assert persisted is not None
    assert persisted['worker']['heartbeatAt'] != initial_heartbeat
    assert persisted['error']['code'] == 'EXECUTOR_INCOMPLETE'


def test_worker_subprocess_does_not_create_default_platform_store(tmp_path: Path) -> None:
    """worker/executor 导入模块时不能额外加载一份默认历史制品快照。"""

    state_path = tmp_path / 'worker_default_state.sqlite3'
    completed = subprocess.run(
        [
            sys.executable,
            '-c',
            'from app.services import platform_store as module; '
            'print(module.platform_store is None)',
        ],
        cwd=Path(__file__).resolve().parents[1],
        env={
            **os.environ,
            'MOMO_PLATFORM_WORKER': '1',
            'MOMO_PLATFORM_STATE_PATH': str(state_path),
            'PYTHONPATH': os.pathsep.join(
                [
                    str(Path(__file__).resolve().parents[3]),
                    str(Path(__file__).resolve().parents[1]),
                    os.environ.get('PYTHONPATH', ''),
                ]
            ),
        },
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stdout.strip() == 'True'
    assert not state_path.exists()


def test_progress_collection_failure_does_not_terminate_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """进度通道异常只能丢失观测数据，不能终止 worker 监督循环。"""

    state_path = tmp_path / 'platform_state.sqlite3'
    store = PlatformStore(state_path=state_path)
    job = create_queued_test_job(store)
    store.mark_job_running(job.job_id, pid=os.getpid())

    def broken_progress(_repository, _job_id: str, *, phase: str = '执行中'):
        raise ValueError('broken progress payload')

    monkeypatch.setattr(platform_worker_module, 'record_worker_observation', broken_progress)

    exit_code = run_worker(
        state_path,
        job.job_id,
        heartbeat_interval_s=0.03,
        executor_command=[sys.executable, '-c', 'import time; time.sleep(0.1)'],
    )

    assert exit_code == 1
    persisted = PlatformStore(state_path=state_path, recover_orphans=False).get_job(job.job_id)
    assert persisted.status == 'FAILED'
    assert persisted.error.code == 'EXECUTOR_INCOMPLETE'


def test_dashboard_remains_readable_while_dispatcher_refreshes_state(tmp_path: Path) -> None:
    store = PlatformStore(state_path=tmp_path / 'platform_state.sqlite3')
    load_started = threading.Event()
    allow_load = threading.Event()
    original_load = store.repository.load

    def delayed_load():
        load_started.set()
        allow_load.wait(timeout=5)
        return original_load()

    store.repository.load = delayed_load
    # 失效版本标记，强制本次 refresh 走全量重载路径（本测试验证的正是重载期间的可读性）。
    store.repository._last_loaded_revision = None
    refresh_thread = threading.Thread(target=store.refresh)
    refresh_thread.start()
    assert load_started.wait(timeout=5)
    try:
        summary = store.dashboard_summary()
    finally:
        allow_load.set()
        refresh_thread.join(timeout=5)

    assert summary['latestGate']['finishedAt'] is not None


def test_dispatcher_cancel_terminates_registered_process_tree(tmp_path: Path) -> None:
    state_path = tmp_path / 'platform_state.sqlite3'
    child_pid_path = tmp_path / 'child.pid'
    store = PlatformStore(state_path=state_path)
    job = create_queued_test_job(store)

    child_code = 'import time; time.sleep(60)'
    parent_code = (
        'import pathlib, subprocess, sys, time; '
        f'p=subprocess.Popen([sys.executable, "-c", {child_code!r}]); '
        f'pathlib.Path({str(child_pid_path)!r}).write_text(str(p.pid), encoding="utf-8"); '
        'time.sleep(60)'
    )
    dispatcher = PlatformJobDispatcher(
        store,
        poll_interval_s=0.05,
        heartbeat_timeout_s=30.0,
        worker_command_factory=lambda *_: [sys.executable, '-c', parent_code],
    )

    dispatcher.start()
    try:
        wait_until(child_pid_path.exists)
        child_pid = int(child_pid_path.read_text(encoding='utf-8'))
        assert process_exists(child_pid)
        assert dispatcher.cancel_job(job.job_id) == {'success': True}
        wait_until(lambda: not process_exists(child_pid))
    finally:
        dispatcher.stop()

    cancelled = PlatformStore(state_path=state_path).get_job(job.job_id)
    assert cancelled.status == 'CANCELLED'
    assert cancelled.finished_at is not None


def test_dispatcher_cancel_terminates_recovered_worker(tmp_path: Path) -> None:
    """服务重启后没有 Popen 句柄时，取消仍必须按已登记 PID 终止 worker。"""

    state_path = tmp_path / 'platform_state.sqlite3'
    process = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
    store = PlatformStore(state_path=state_path)
    job = create_queued_test_job(store)
    store.mark_job_running(job.job_id, pid=process.pid)
    dispatcher = PlatformJobDispatcher(store)

    try:
        assert dispatcher.cancel_job(job.job_id) == {'success': True}
        wait_until(lambda: not process_exists(process.pid))
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)

    cancelled = PlatformStore(state_path=state_path, recover_orphans=False).get_job(job.job_id)
    assert cancelled.status == 'CANCELLED'


def test_dispatcher_fails_worker_after_heartbeat_timeout(tmp_path: Path) -> None:
    state_path = tmp_path / 'platform_state.sqlite3'
    store = PlatformStore(state_path=state_path)
    job = create_queued_test_job(store)
    dispatcher = PlatformJobDispatcher(
        store,
        poll_interval_s=0.05,
        heartbeat_timeout_s=0.2,
        worker_command_factory=lambda *_: [sys.executable, '-c', 'import time; time.sleep(60)'],
    )

    dispatcher.start()
    try:
        wait_until(
            lambda: PlatformStore(state_path=state_path, recover_orphans=False).get_job(job.job_id).status == 'FAILED'
        )
    finally:
        dispatcher.stop()

    failed = PlatformStore(state_path=state_path).get_job(job.job_id)
    assert failed.error is not None
    assert failed.error.code == 'HEARTBEAT_TIMEOUT'


def test_dispatcher_keeps_worker_when_solver_progress_is_fresh(tmp_path: Path) -> None:
    """真实求解仍在推进时，陈旧 heartbeat 不能触发进程树误杀。"""

    state_path = tmp_path / 'platform_state.sqlite3'
    process = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
    try:
        store = PlatformStore(state_path=state_path)
        job = create_queued_test_job(store)
        running = store.mark_job_running(job.job_id, pid=process.pid)
        stale_heartbeat = (datetime.now(UTC) - timedelta(seconds=10)).isoformat()
        running.worker.heartbeat_at = stale_heartbeat
        store.persist()
        write_case_progress(
            job_progress_dir(job.job_id),
            'active_case',
            step=50,
            total_steps=100,
        )
        dispatcher = PlatformJobDispatcher(store, heartbeat_timeout_s=0.2)

        dispatcher._inspect_recovered_process(running)

        persisted = PlatformStore(state_path=state_path, recover_orphans=False).get_job(job.job_id)
        assert persisted.status == 'RUNNING'
        assert process.poll() is None
        assert persisted.worker is not None
        assert persisted.worker.heartbeat_at != stale_heartbeat
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)


def test_dispatcher_terminates_worker_after_execution_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_path = tmp_path / 'platform_state.sqlite3'
    store = PlatformStore(state_path=state_path)
    job = create_queued_test_job(store)
    running = store.mark_job_running(job.job_id, pid=54321)
    running.request['resources'] = {'executionTimeoutS': 1}
    running.started_at = (datetime.now(UTC) - timedelta(seconds=2)).isoformat()
    store.persist()
    terminated: list[int] = []

    class FakeProcess:
        pid = 54321

        @staticmethod
        def poll():
            return None

        @staticmethod
        def wait(*, timeout: float):
            assert timeout == 10
            return 1

    monkeypatch.setattr(
        'app.services.platform_dispatcher.terminate_process_tree',
        terminated.append,
    )
    dispatcher = PlatformJobDispatcher(store, heartbeat_timeout_s=300)
    dispatcher._active_job_id = job.job_id
    dispatcher._active_process = FakeProcess()

    dispatcher._inspect_active_process()

    failed = PlatformStore(state_path=state_path, recover_orphans=False).get_job(job.job_id)
    assert terminated == [54321]
    assert failed.status == 'FAILED'
    assert failed.error is not None
    assert failed.error.code == 'EXECUTION_TIMEOUT'
    assert dispatcher._active_process is None
    assert dispatcher._active_job_id is None


def test_dispatcher_records_worker_launch_failure(tmp_path: Path) -> None:
    state_path = tmp_path / 'platform_state.sqlite3'
    store = PlatformStore(state_path=state_path)
    job = create_queued_test_job(store)
    dispatcher = PlatformJobDispatcher(
        store,
        poll_interval_s=0.05,
        worker_command_factory=lambda *_: [str(tmp_path / 'missing-worker.exe')],
    )

    dispatcher.start()
    try:
        wait_until(
            lambda: PlatformStore(state_path=state_path, recover_orphans=False).get_job(job.job_id).status == 'FAILED'
        )
    finally:
        dispatcher.stop()

    failed = PlatformStore(state_path=state_path).get_job(job.job_id)
    assert failed.error is not None
    assert failed.error.code == 'WORKER_LAUNCH_ERROR'


def test_dispatcher_rejects_legacy_queue_records_instead_of_running_them(tmp_path: Path) -> None:
    state_path = tmp_path / 'platform_state.sqlite3'
    store = PlatformStore(state_path=state_path)
    job = create_queued_test_job(store)
    job.queue_version = None
    store.persist()
    dispatcher = PlatformJobDispatcher(store, poll_interval_s=0.05)

    dispatcher.start()
    try:
        wait_until(
            lambda: PlatformStore(state_path=state_path, recover_orphans=False).get_job(job.job_id).status == 'FAILED'
        )
    finally:
        dispatcher.stop()

    failed = PlatformStore(state_path=state_path).get_job(job.job_id)
    assert failed.error is not None
    assert failed.error.code == 'QUEUE_VERSION_UNSUPPORTED'
    assert failed.worker is None
