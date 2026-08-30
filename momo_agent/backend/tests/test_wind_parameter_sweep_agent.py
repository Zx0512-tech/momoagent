"""风荷载 × OpenSeesPy 批量参数计算的审批门与工况透传测试。"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.agents.analysis import ANALYSIS_WIND_TARGET_SET_ID, ANALYSIS_WIND_WORKFLOW_PATHS
from app.agents.damper_parameter_sweep import (
    PARAMETER_SWEEP_WIND_WORKFLOW_PATHS,
    DamperParameterSweepAgent,
)
from app.services.agent_engineering import build_parameter_sweep_contract
from app.services.platform_store import PlatformStore


REPO_ROOT = Path(__file__).resolve().parents[3]
WIND_TEMPLATE = PARAMETER_SWEEP_WIND_WORKFLOW_PATHS['OPENSEESPY_INPROC']


def _solver_profile(solver: str) -> dict:
    """求解器版本画像必须跟随被测求解器。

    ANSYS 走 USER300 自定义单元，画像里缺少 calibrationHashVerified 会让
    预检以 FAILED 结束；OpenSeesPy 没有该单元，塞进去测出的是假通过。
    """
    if str(solver).upper() == 'ANSYS':
        return {
            'schemaVersion': '1.0',
            'solver': {'name': 'ANSYS_MAPDL', 'version': '2024 R1'},
            'responseContract': {'id': 'ANSYS_RESPONSE_R1'},
            'userElement': {'name': 'USER300', 'calibrationHashVerified': True},
        }
    return {
        'schemaVersion': '1.0',
        'solver': {'name': 'OPENSEESPY', 'version': '3.5.1'},
        'responseContract': {'id': 'OPENSEES_RESPONSE_R1'},
    }


def _agent() -> DamperParameterSweepAgent:
    return DamperParameterSweepAgent(
        planner=SimpleNamespace(),
        store=None,
        dispatcher=None,
        repo_root=REPO_ROOT,
        readiness_builder=lambda *_args, **_kwargs: {'status': 'READY'},
        preflight_runner=lambda _path: {'path_checks': {'model': {'exists': True}}},
        solver_profile_builder=lambda _path, *, solver: _solver_profile(solver),
    )


def _cases() -> list[dict]:
    return [
        {
            'caseId': 'case-1',
            'damperType': 'VISCOUS',
            'parameters': {'c': 2000.0, 'alpha': 0.4, 'vfloor': 0.001},
        },
        {
            'caseId': 'case-2',
            'damperType': 'VISCOUS',
            'parameters': {'c': 4000.0, 'alpha': 0.4, 'vfloor': 0.001},
        },
    ]


def _run(solver: str = 'OPENSEESPY_INPROC', load_kind: str = 'WIND') -> dict:
    return {
        'runId': 'run-sweep-wind-1',
        'workflowContract': build_parameter_sweep_contract(
            solver=solver,
            cases=_cases(),
            response_ids=['max_girder_end_displacement'],
            selected_layout_id='TWO_PER_TOWER',
            load_kind=load_kind,
            max_concurrent_cases=2,
        ),
    }


def _wind_mapping(**channel_overrides) -> dict:
    return {
        'version': 2,
        'loadKind': 'WIND',
        'time': {'column': 'time', 'unit': 's'},
        'channels': [{
            'valueColumn': 'wind_fy',
            'applicationType': 'NODAL_FORCE',
            'targetType': 'NODE_GROUP',
            'targetId': ANALYSIS_WIND_TARGET_SET_ID,
            'component': 'UY',
            'quantity': 'FORCE',
            'sourceUnit': 'N',
            'scale': 1.0,
            **channel_overrides,
        }],
        'solver': 'OPENSEESPY_INPROC',
    }


def _prepare(agent, run, mapping, *, artifact_id='load-wind-1', sha='c' * 64):
    return agent.prepare_approval(
        run,
        mapping=mapping,
        standard_artifact_id=artifact_id,
        standard_sha256=sha,
    )


def test_wind_sweep_freezes_wind_template_target_and_scenario() -> None:
    prepared = _prepare(_agent(), _run(), _wind_mapping())

    assert prepared.passed
    frozen = prepared.frozen_action
    assert frozen['workflowConfigPath'] == WIND_TEMPLATE
    assert frozen['loadKind'] == 'WIND'
    # scenario 是执行侧的工况判据，必须跟随 loadKind 而不是留在地震默认值。
    assert frozen['scenario'] == 'WIND'
    assert frozen['loadTargetSetId'] == ANALYSIS_WIND_TARGET_SET_ID
    assert frozen['loadDatasetArtifactId'] == 'load-wind-1'
    assert prepared.contract_updates['loadTargetSetId'] == ANALYSIS_WIND_TARGET_SET_ID
    assert prepared.contract_updates['loadKind'] == 'WIND'
    assert '风荷载' in prepared.plan[0]


def test_wind_sweep_frozen_action_passes_execution_gate() -> None:
    prepared = _prepare(_agent(), _run(), _wind_mapping())

    # 审批通过但执行侧拒收会让整单卡在 Job 创建，两道门必须同口径。
    assert PlatformStore._is_real_damper_parameter_sweep_request(
        'SOLVER_BATCH', prepared.frozen_action,
    )


def test_wind_sweep_on_ansys_freezes_ansys_wind_template() -> None:
    """ANSYS 风工况批量已接入，冻结的是 ANSYS 风模板而不是 OpenSees 那份。"""
    prepared = _prepare(_agent(), _run(solver='ANSYS'), _wind_mapping())

    assert prepared.passed
    frozen = prepared.frozen_action
    assert frozen['solver'] == 'ANSYS'
    assert frozen['scenario'] == 'WIND'
    assert frozen['workflowConfigPath'] == ANALYSIS_WIND_WORKFLOW_PATHS['ANSYS']
    assert frozen['workflowConfigPath'] != WIND_TEMPLATE
    assert frozen['loadTargetSetId'] == ANALYSIS_WIND_TARGET_SET_ID


def test_wind_sweep_without_registered_load_artifact_fails_closed() -> None:
    prepared = _prepare(_agent(), _run(), _wind_mapping(), artifact_id=None, sha=None)

    assert not prepared.passed
    assert prepared.preflight['reason'] == 'WIND_LOAD_ARTIFACT_REQUIRED'


@pytest.mark.parametrize(
    ('channel_overrides', 'reason'),
    [
        ({'component': 'UX'}, 'WIND_LOAD_MAPPING_UNSUPPORTED'),
        ({'sourceUnit': 'g'}, 'WIND_LOAD_MAPPING_UNSUPPORTED'),
        ({'quantity': 'ACCELERATION'}, 'WIND_LOAD_MAPPING_UNSUPPORTED'),
        ({'targetType': 'NODE'}, 'WIND_LOAD_TARGET_UNSUPPORTED'),
        ({'targetId': 'STBRIDGE_TRAFFIC_CENTERLINE'}, 'WIND_LOAD_TARGET_UNSUPPORTED'),
    ],
)
def test_wind_sweep_rejects_mismatched_channel_contract(channel_overrides: dict, reason: str) -> None:
    prepared = _prepare(_agent(), _run(), _wind_mapping(**channel_overrides))

    assert not prepared.passed
    assert prepared.preflight['reason'] == reason


def test_earthquake_sweep_keeps_earthquake_template_and_no_wind_target() -> None:
    prepared = _prepare(
        _agent(),
        _run(load_kind='EARTHQUAKE'),
        {'loadKind': 'EARTHQUAKE', 'channels': []},
    )

    assert prepared.passed
    frozen = prepared.frozen_action
    assert frozen['scenario'] == 'EARTHQUAKE'
    assert frozen['workflowConfigPath'] != WIND_TEMPLATE
    assert 'loadTargetSetId' not in frozen
    assert '地震荷载' in prepared.plan[0]


def test_parameter_sweep_contract_rejects_unregistered_load_kind() -> None:
    # 车流已放行（见 test_traffic_parameter_sweep 覆盖）；这里用仍未登记的
    # OPERATION 守住契约层门禁：放行口径只认注册表，不按工况名猜测。
    with pytest.raises(ValueError, match='Unsupported parameter sweep load kind'):
        build_parameter_sweep_contract(
            solver='OPENSEESPY_INPROC',
            cases=_cases(),
            response_ids=['max_girder_end_displacement'],
            selected_layout_id='TWO_PER_TOWER',
            load_kind='OPERATION',
            max_concurrent_cases=2,
        )
