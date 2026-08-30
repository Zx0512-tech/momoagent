"""求解进度的纵向链路测试。

覆盖真实链路的每一段接口，中间不打桩：

    命令流模板渲染出的 reporter 代码（求解子进程实际执行的那段）
      → 进度 JSON 落盘
      → worker 心跳循环 record_case_progress 汇总
      → SQLite Job.progress 持久化
      → AgentService._job_progress 透出给前端的 camelCase 字段

前端一侧的断言在 platform-ui/src/pages/chat/cards/cards.test.tsx。
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

from app.services import agent_service as agent_service_module
from app.services import platform_store as platform_store_module
from app.services.agent_service import AgentService
from app.services.platform_store import PlatformStore, job_progress_dir
from app.services.platform_worker import run_worker
from pyansys_bridge.core.progress_sink import write_batch_progress

from tests.test_command_stream_progress import _render_with_progress
from tests.test_platform_job_reliability import create_queued_test_job, wait_until

TOTAL_STEPS = 500
REPORTED_STEP = 250


def _reporter_source(progress_dir: Path) -> str:
    """从真实命令流模板里截出 reporter 段，保证测的是求解器实际执行的代码。"""

    rendered = _render_with_progress(progress_dir.as_posix())
    start = rendered.index('_progress_dir = Path(')
    end = rendered.index('timeseries_path = Path(')
    return rendered[start:end]


def _writer_script(tmp_path: Path, progress_dir: Path) -> Path:
    """把 reporter 段包成一个可独立执行的脚本，充当求解子进程。"""

    script = tmp_path / 'fake_solver.py'
    script.write_text(
        '\n'.join([
            'import time',
            'from pathlib import Path',
            f'analysis_steps = {TOTAL_STEPS}',
            _reporter_source(progress_dir),
            f'_report_progress({REPORTED_STEP})',
            # 停留一会儿，让 worker 心跳至少汇总一次进度。
            'time.sleep(1.0)',
        ]),
        encoding='utf-8',
    )
    return script


def test_progress_flows_from_solver_script_to_agent_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_path = tmp_path / 'platform_state.sqlite3'
    store = PlatformStore(state_path=state_path)
    job = create_queued_test_job(store)
    store.mark_job_running(job.job_id, pid=1234)

    progress_dir = job_progress_dir(job.job_id)
    # 派发方先写完成计数（单算例分析写 0/1），求解子进程只写自己的 case 文件。
    write_batch_progress(progress_dir, completed=0, total=1)
    script = _writer_script(tmp_path, progress_dir)

    errors: list[Exception] = []

    def supervise() -> None:
        try:
            run_worker(
                state_path,
                job.job_id,
                heartbeat_interval_s=0.05,
                executor_command=[sys.executable, str(script)],
            )
        except Exception as exc:  # pragma: no cover - 线程内异常需带回主线程
            errors.append(exc)

    worker_thread = threading.Thread(target=supervise)
    worker_thread.start()
    try:
        wait_until(
            lambda: bool(
                (progress := PlatformStore(state_path=state_path, recover_orphans=False)
                 .get_job(job.job_id).progress)
                and progress.active_cases
            ),
            timeout_s=5.0,
        )

        # SQLite 已落库：另一个进程（API 进程）也能读到同一份进度。
        persisted = PlatformStore(state_path=state_path, recover_orphans=False).get_job(job.job_id)
        assert persisted.progress.total_cases == 1
        assert persisted.progress.completed_cases == 0
        case = persisted.progress.active_cases[0]
        assert case.percent == 50
        assert case.step == REPORTED_STEP
        assert case.total_steps == TOTAL_STEPS
        assert case.phase == 'transient'

        # AgentService 透出的必须是前端约定的 camelCase 字段。
        monkeypatch.setattr(
            agent_service_module,
            'platform_store',
            PlatformStore(state_path=state_path, recover_orphans=False),
        )
        exposed = AgentService._job_progress(job.job_id)

        assert exposed is not None
        assert exposed['phase'] == '求解中'
        assert exposed['percent'] == 50
        assert exposed['totalCases'] == 1
        assert exposed['completedCases'] == 0
        assert exposed['activeCases'][0] == {
            'caseId': case.case_id,
            'percent': 50,
            'step': REPORTED_STEP,
            'totalSteps': TOTAL_STEPS,
            'phase': 'transient',
        }
    finally:
        worker_thread.join(timeout=10.0)

    assert not errors
    assert not worker_thread.is_alive()


def test_single_analysis_job_opens_the_progress_channel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """单次分析（非 DOE）必须把 progress_dir 写进求解配置并预置完成计数。

    这是用户报的核心缺陷：只有整单优化配了进度目录，普通分析一片空白。
    """

    monkeypatch.setattr(
        platform_store_module,
        'EARTHQUAKE_WORKFLOW_OUTPUT_ROOT',
        tmp_path / 'real_workflows',
    )
    store = PlatformStore(state_path=tmp_path / 'platform_state.sqlite3')
    seen_configs: list[dict] = []

    def fake_runner(config_path: Path, *, execution_timeout_s: float | None) -> dict:
        seen_configs.append(store._load_json_config(config_path))
        return {
            'case_id': 'case_single',
            'solver': 'openseespy_inproc',
            'status': 'completed',
            'objectives': {'max_tower_base_shear': 1.0},
            'timeseries': {},
            'metadata': {'execution_mode': 'run'},
        }

    monkeypatch.setattr(store, '_run_real_agent_analysis', fake_runner)

    store._generate_real_agent_analysis_artifacts(
        {'solver': 'OPENSEESPY_INPROC', 'responseIds': ['max_tower_base_shear']},
        job_id='job_single_analysis',
    )

    assert seen_configs[0]['progress_dir'] == str(job_progress_dir('job_single_analysis'))
    # 求解结束后完成计数落在 1/1，前端不会停在 0/1。
    from pyansys_bridge.core.progress_sink import read_batch_progress

    batch = read_batch_progress(job_progress_dir('job_single_analysis'))
    assert batch == {'completedCases': 1, 'totalCases': 1, 'percent': 100}


def test_single_analysis_without_job_id_leaves_config_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """没有 job_id 时（同步执行路径）不能凭空写入进度目录。"""

    monkeypatch.setattr(
        platform_store_module,
        'EARTHQUAKE_WORKFLOW_OUTPUT_ROOT',
        tmp_path / 'real_workflows',
    )
    store = PlatformStore(state_path=tmp_path / 'platform_state.sqlite3')
    seen_configs: list[dict] = []

    def fake_runner(config_path: Path, *, execution_timeout_s: float | None) -> dict:
        seen_configs.append(store._load_json_config(config_path))
        return {
            'case_id': 'case_single',
            'solver': 'openseespy_inproc',
            'status': 'completed',
            'objectives': {},
            'timeseries': {},
            'metadata': {'execution_mode': 'run'},
        }

    monkeypatch.setattr(store, '_run_real_agent_analysis', fake_runner)

    store._generate_real_agent_analysis_artifacts({'solver': 'OPENSEESPY_INPROC'})

    assert 'progress_dir' not in seen_configs[0]


def test_ansys_damper_comparison_reports_batch_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """没有真实 MAPDL 输出的测试替身仍必须逐个推进 ANSYS 批次完成计数。"""

    from pyansys_bridge.core.progress_sink import read_batch_progress

    monkeypatch.setattr(
        platform_store_module,
        'EARTHQUAKE_WORKFLOW_OUTPUT_ROOT',
        tmp_path / 'real_workflows',
    )
    store = PlatformStore(state_path=tmp_path / 'platform_state.sqlite3')
    progress_dir = job_progress_dir('job_comparison')
    seen_counts: list[dict | None] = []

    def fake_case_runner(config_path: Path, *, execution_timeout_s: float | None) -> dict:
        # 每个算例开跑时读一次，验证计数是随算例推进而非最后一次性写入。
        seen_counts.append(read_batch_progress(progress_dir))
        return {
            'case_id': 'case_' + config_path.parent.name,
            'solver': 'ansys',
            'status': 'completed',
            'objectives': {'max_girder_end_displacement': 0.1},
            'timeseries': {},
            'metadata': {'execution_mode': 'run'},
        }

    monkeypatch.setattr(store, '_run_real_damper_comparison_case', fake_case_runner)

    store._generate_real_damper_comparison_artifacts(
        {
            'cases': [
                {
                    'caseId': 'viscous',
                    'damperType': 'VISCOUS',
                    'solverModule': 'damper_user300_viscous',
                    'parameters': {'c': 7600.0, 'alpha': 0.8, 'vfloor': 0.001},
                    'theoreticalPeakForceN': 1.0,
                    'designVelocityMps': 0.5,
                    'parameterSource': 'AGENT',
                },
                {
                    'caseId': 'eddy',
                    'damperType': 'EDDY_CURRENT',
                    'solverModule': 'damper_user300_eddy',
                    'parameters': {'fmax': 2000.0, 'vcr': 0.4},
                    'theoreticalPeakForceN': 1.0,
                    'designVelocityMps': 0.5,
                    'parameterSource': 'AGENT',
                },
            ],
        },
        job_id='job_comparison',
    )

    assert [entry['completedCases'] for entry in seen_counts] == [0, 1]
    assert read_batch_progress(progress_dir) == {
        'completedCases': 2,
        'totalCases': 2,
        'percent': 100,
    }
