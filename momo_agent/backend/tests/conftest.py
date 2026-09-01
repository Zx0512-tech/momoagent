from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest


# Hosted Linux CI must not cross local ANSYS/version/template-preflight boundaries.
# Keep the classification next to the shared test fixtures so the workflow can select
# by semantic pytest markers instead of maintaining a duplicated --deselect list.
_ANSYS_REQUIRED_NODEIDS = frozenset(
    {
        'tests/test_agent_load_api.py::test_analysis_freezes_real_solver_job_after_load_mapping',
        'tests/test_agent_load_api.py::test_analysis_without_upload_freezes_bundled_earthquake_artifact',
        'tests/test_damper_sweep_wind.py::test_wind_sweep_freezes_registered_wind_template_and_target_set',
        'tests/test_damper_sweep_wind.py::test_earthquake_sweep_keeps_no_target_set_and_earthquake_template',
        'tests/test_traffic_damper_optimization.py::test_traffic_optimization_template_is_registered_and_single_objective',
        'tests/test_traffic_damper_optimization.py::test_traffic_optimization_freezes_registered_traffic_workflow_template',
        'tests/test_traffic_damper_optimization.py::test_traffic_optimization_approval_does_not_gate_missing_point_mapping',
        'tests/test_wind_damper_optimization.py::test_wind_optimization_template_is_registered_and_single_objective',
        'tests/test_wind_damper_optimization.py::test_wind_optimization_freezes_registered_wind_workflow_template',
        'tests/test_wind_damper_optimization.py::test_wind_optimization_freezes_load_target_set',
        'tests/test_wind_damper_optimization.py::test_earthquake_optimization_does_not_freeze_wind_target_set',
        'tests/test_wind_damper_optimization.py::test_earthquake_optimization_still_freezes_joint_template',
    }
)
_ANSYS_REQUIRED_NODEID_PREFIXES = (
    'tests/test_traffic_damper_sweep.py::test_traffic_sweep_freezes_registered_template_and_point_mapping[ANSYS-',
)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Attach solver-environment markers to legacy tests before selection.

    The parameterized traffic sweep contains both ANSYS and OpenSees cases, so only the
    ANSYS parameter instance is marked. This keeps the OpenSees half eligible for hosted
    fast CI while removing workflow-level knowledge of individual test node IDs.
    """

    for item in items:
        nodeid = item.nodeid.replace('\\', '/')
        if nodeid in _ANSYS_REQUIRED_NODEIDS or any(
            nodeid.startswith(prefix) for prefix in _ANSYS_REQUIRED_NODEID_PREFIXES
        ):
            item.add_marker(pytest.mark.requires_ansys)


@pytest.fixture(autouse=True)
def isolate_llm_env(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """测试默认不接触真实 LLM；需要 LLM 行为的用例自行 mock 请求。"""
    # 生产默认运行时是 Harness；仅旧兼容接口回归模块显式保留 LEGACY。
    legacy_modules = {
        'test_agent_damper_comparison_api.py',
        'test_agent_full_optimization_api.py',
        'test_agent_llm.py',
        'test_agent_load_api.py',
        'test_result_inquiry.py',
    }
    runtime = 'LEGACY' if request.node.path.name in legacy_modules else 'WORKFLOW_HARNESS'
    monkeypatch.setenv('MOMO_AGENT_RUNTIME', runtime)
    for name in (
        'MOMO_LLM_BASE_URL',
        'MOMO_LLM_MODEL',
        'MOMO_LLM_API_KEY',
        'MOMO_LLM_TIMEOUT_S',
        'MOMO_LLM_HARNESS_THINKING',
        'MOMO_LLM_HARNESS_MAX_TOKENS',
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def isolate_llm_planner(monkeypatch: pytest.MonkeyPatch) -> None:
    """模块级 planner 已在测试收集时初始化，需同步清空其配置。"""
    from app.services import agent_llm

    monkeypatch.setattr(agent_llm.llm_planner, 'base_url', '')
    monkeypatch.setattr(agent_llm.llm_planner, 'model', '')
    monkeypatch.setattr(agent_llm.llm_planner, 'api_key', '')


@pytest.fixture(autouse=True)
def isolate_job_progress_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """进度目录默认指向仓库 output/，测试一律改指 tmp_path。

    真实求解路径现在都会为 job 建进度目录，不隔离的话每跑一次测试就往
    output/platform_store/job_progress/ 里留一堆残留目录。
    """

    from app.services import platform_store

    monkeypatch.setattr(platform_store, 'JOB_PROGRESS_ROOT', tmp_path / 'job_progress')


@pytest.fixture(autouse=True)
def isolate_platform_runtime_state(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[None]:
    if request.node.path.name not in {'test_platform_api_v1.py', 'test_platform_job_reliability.py'}:
        yield
        return

    from app.services.platform_dispatcher import platform_dispatcher
    from app.services.platform_store import PlatformStore, platform_store

    isolated = PlatformStore(state_path=tmp_path / 'api_v1_state.sqlite3')
    original = (
        platform_store.state_path,
        platform_store.repository,
        platform_store.jobs,
        platform_store.artifacts,
        platform_store.engineering_config,
        platform_store.real_workflow_root,
        platform_dispatcher.store,
    )
    platform_store.state_path = isolated.state_path
    platform_store.repository = isolated.repository
    platform_store.jobs = isolated.jobs
    platform_store.artifacts = isolated.artifacts
    platform_store.engineering_config = isolated.engineering_config
    platform_store.real_workflow_root = isolated.real_workflow_root
    platform_dispatcher.store = platform_store
    try:
        yield
    finally:
        platform_dispatcher.stop()
        (
            platform_store.state_path,
            platform_store.repository,
            platform_store.jobs,
            platform_store.artifacts,
            platform_store.engineering_config,
            platform_store.real_workflow_root,
            platform_dispatcher.store,
        ) = original
