"""车流工况阻尼器参数批量计算的契约、审批门与平台放行门测试。

车流与风的差异点集中在荷载形状上：风是单列总力（NODAL_FORCE），车流是 163 列
稠密矩阵（NODAL_FORCE_MATRIX），并且必须与逐节点 mapping 制品配对冻结——只有
矩阵时求解侧无法知道哪一列对应哪个节点。这些差异在这里逐条守住。
"""
from __future__ import annotations

import pytest

from app.agents.analysis import (
    ANALYSIS_TRAFFIC_TARGET_SET_ID,
    ANALYSIS_TRAFFIC_WORKFLOW_PATHS,
)
from app.agents.damper_parameter_sweep import (
    PARAMETER_SWEEP_WORKFLOW_PATHS_BY_LOAD_KIND,
    DamperParameterSweepAgent,
)
from app.services.agent_engineering import build_parameter_sweep_contract
from app.services.platform_store import PlatformStore
from types import SimpleNamespace


TRAFFIC_TEMPLATE = ANALYSIS_TRAFFIC_WORKFLOW_PATHS['ANSYS']
OPENSEES_TRAFFIC_TEMPLATE = ANALYSIS_TRAFFIC_WORKFLOW_PATHS['OPENSEESPY_INPROC']
MATRIX_ARTIFACT_ID = 'load-traffic-matrix-1'
MATRIX_SHA256 = 'c' * 64
MAPPING_ARTIFACT_ID = 'load-traffic-mapping-1'
MAPPING_SHA256 = 'd' * 64
VISCOUS_CASES = [
    {'caseId': 'traffic_c1000', 'damperType': 'VISCOUS', 'parameters': {'c': 1000.0, 'alpha': 0.5, 'vfloor': 1e-6}},
    {'caseId': 'traffic_c2000', 'damperType': 'VISCOUS', 'parameters': {'c': 2000.0, 'alpha': 0.5, 'vfloor': 1e-6}},
]


# ---------------------------------------------------------------------------
# 契约层：loadKind=TRAFFIC 在两个求解器上都产出正确的阻尼器模块
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ('solver', 'expected_module'),
    [
        ('ANSYS', 'damper_user300_viscous'),
        ('OPENSEESPY_INPROC', 'damper_viscous'),
    ],
)
def test_traffic_contract_carries_traffic_load_kind_per_solver(solver: str, expected_module: str) -> None:
    contract = build_parameter_sweep_contract(
        solver=solver,
        cases=VISCOUS_CASES,
        response_ids=['max_girder_end_displacement'],
        load_kind='TRAFFIC',
    )

    assert contract['loadKind'] == 'TRAFFIC'
    assert contract['scenario'] == 'TRAFFIC'
    assert contract['solver'] == solver
    # 黏滞阻尼器的求解器模块必须分流：ANSYS 走 USER300，OpenSees 走原生 viscous。
    # 混用会让执行侧加载另一求解器的模块名而失败，或更糟——按 1000 倍单位差解算。
    assert [case['solverModule'] for case in contract['cases']] == [expected_module, expected_module]


def test_traffic_sweep_workflow_paths_registered_for_both_solvers() -> None:
    registered = PARAMETER_SWEEP_WORKFLOW_PATHS_BY_LOAD_KIND['TRAFFIC']

    assert registered['ANSYS'] == TRAFFIC_TEMPLATE
    assert registered['OPENSEESPY_INPROC'] == OPENSEES_TRAFFIC_TEMPLATE


# ---------------------------------------------------------------------------
# 审批门
# ---------------------------------------------------------------------------

def _agent(*, readiness_status: str = 'READY', solver_name: str = 'ANSYS') -> DamperParameterSweepAgent:
    # 求解器版本画像必须跟随被测求解器：OpenSeesPy 没有 USER300 元素，
    # 画像里塞 USER300 会让门禁测出的是假通过。
    if solver_name == 'OPENSEESPY':
        profile = {
            'schemaVersion': '1.0',
            'solver': {'name': 'OPENSEESPY', 'version': '3.5.1'},
            'responseContract': {'id': 'OPENSEES_RESPONSE_R1'},
        }
    else:
        profile = {
            'schemaVersion': '1.0',
            'solver': {'name': 'ANSYS_MAPDL', 'version': '2024 R1'},
            'responseContract': {'id': 'ANSYS_RESPONSE_R1'},
            'userElement': {'name': 'USER300', 'calibrationHashVerified': True},
        }
    return DamperParameterSweepAgent(
        planner=SimpleNamespace(),
        store=SimpleNamespace(),
        dispatcher=SimpleNamespace(),
        readiness_builder=lambda *_args, **_kwargs: {'status': readiness_status},
        solver_profile_builder=lambda *_args, **_kwargs: profile,
    )


def _traffic_channel(**overrides) -> dict:
    # 单条通道描述整个 163 列稠密矩阵。component 必须是 UY：门按
    # AGENT_TRAFFIC_FORCE_COMPONENT 校验方向，与风同口径。
    # （analysis.py 的门参数注释称车流"不约束 component"，与代码不符。）
    return {
        'valueColumn': 'node_fy_N_matrix',
        'applicationType': 'NODAL_FORCE_MATRIX',
        'targetType': 'NODE_GROUP',
        'targetId': ANALYSIS_TRAFFIC_TARGET_SET_ID,
        'component': 'UY',
        'quantity': 'FORCE',
        'sourceUnit': 'N',
        'scale': 1.0,
        **overrides,
    }


def _traffic_mapping(channels: list[dict] | None = None, **overrides) -> dict:
    return {
        'version': 2,
        'loadKind': 'TRAFFIC',
        'time': {'column': 'time_s', 'unit': 's'},
        'channels': [_traffic_channel()] if channels is None else channels,
        'pointMappingArtifactId': MAPPING_ARTIFACT_ID,
        'pointMappingSha256': MAPPING_SHA256,
        **overrides,
    }


def _traffic_run(solver: str = 'ANSYS') -> dict:
    return {
        'runId': f'run-traffic-sweep-{solver.lower()}',
        'goal': '车流工况下批量计算阻尼器参数',
        'intent': {'loadKind': 'TRAFFIC'},
        'workflowContract': build_parameter_sweep_contract(
            solver=solver,
            cases=VISCOUS_CASES,
            response_ids=['max_girder_end_displacement'],
            load_kind='TRAFFIC',
        ),
    }


def _prepare(agent, run, mapping, *, artifact_id=MATRIX_ARTIFACT_ID, sha=MATRIX_SHA256):
    return agent.prepare_approval(
        run,
        mapping=mapping,
        standard_artifact_id=artifact_id,
        standard_sha256=sha,
    )


@pytest.mark.parametrize(
    ('solver', 'profile_solver', 'expected_template'),
    [
        ('ANSYS', 'ANSYS', TRAFFIC_TEMPLATE),
        ('OPENSEESPY_INPROC', 'OPENSEESPY', OPENSEES_TRAFFIC_TEMPLATE),
    ],
)
def test_traffic_sweep_freezes_registered_template_and_point_mapping(
    solver: str,
    profile_solver: str,
    expected_template: str,
) -> None:
    prepared = _prepare(
        _agent(solver_name=profile_solver),
        _traffic_run(solver),
        _traffic_mapping(),
    )

    assert prepared.passed is True
    frozen = prepared.frozen_action
    assert frozen['workflowConfigPath'] == expected_template
    assert frozen['scenario'] == 'TRAFFIC'
    assert frozen['loadKind'] == 'TRAFFIC'
    assert frozen['loadTargetSetId'] == ANALYSIS_TRAFFIC_TARGET_SET_ID
    assert frozen['runMode'] == 'REAL_DAMPER_PARAMETER_SWEEP'
    # 矩阵与逐节点 mapping 必须成对进入冻结动作，执行侧按 ID 分别取两个制品。
    assert frozen['loadDatasetArtifactId'] == MATRIX_ARTIFACT_ID
    assert frozen['loadDatasetSha256'] == MATRIX_SHA256
    assert frozen['loadPointMappingArtifactId'] == MAPPING_ARTIFACT_ID
    assert frozen['loadPointMappingSha256'] == MAPPING_SHA256
    assert [case['caseId'] for case in frozen['cases']] == ['traffic_c1000', 'traffic_c2000']


def test_traffic_sweep_plan_names_traffic_load() -> None:
    # 计划文案进入审批卡片：工况名说错会让用户以为冻结了别的荷载。
    prepared = _prepare(_agent(), _traffic_run(), _traffic_mapping())

    assert any('车流荷载' in line for line in prepared.plan)
    assert not any('风荷载' in line for line in prepared.plan)


def test_traffic_sweep_without_registered_load_artifact_fails_closed() -> None:
    prepared = _prepare(
        _agent(),
        _traffic_run(),
        _traffic_mapping(),
        artifact_id=None,
        sha=None,
    )

    assert prepared.passed is False
    assert prepared.preflight['reason'] == 'TRAFFIC_LOAD_ARTIFACT_REQUIRED'


@pytest.mark.parametrize('missing_field', ['pointMappingArtifactId', 'pointMappingSha256'])
def test_traffic_sweep_without_point_mapping_fails_closed(missing_field: str) -> None:
    # 矩阵制品单独存在是不可解释的：缺 mapping 必须失败关闭，
    # 不允许按列号顺序假定节点对应关系。
    mapping = _traffic_mapping(**{missing_field: None})

    prepared = _prepare(_agent(), _traffic_run(), mapping)

    assert prepared.passed is False
    assert prepared.preflight['reason'] == 'TRAFFIC_LOAD_POINT_MAPPING_REQUIRED'


@pytest.mark.parametrize(
    ('channel_overrides', 'reason'),
    [
        # 风的单列总力形状不能用在车流上：逐节点矩阵会被压成 1 列。
        ({'applicationType': 'NODAL_FORCE'}, 'TRAFFIC_LOAD_MAPPING_UNSUPPORTED'),
        ({'applicationType': 'UNIFORM_EXCITATION'}, 'TRAFFIC_LOAD_MAPPING_UNSUPPORTED'),
        ({'quantity': 'ACCELERATION'}, 'TRAFFIC_LOAD_MAPPING_UNSUPPORTED'),
        ({'sourceUnit': 'm/s2'}, 'TRAFFIC_LOAD_MAPPING_UNSUPPORTED'),
        ({'targetType': 'NODE'}, 'TRAFFIC_LOAD_TARGET_UNSUPPORTED'),
        ({'targetId': 'STBRIDGE_WIND_DECK_NODES'}, 'TRAFFIC_LOAD_TARGET_UNSUPPORTED'),
    ],
)
def test_traffic_sweep_rejects_mismatched_channel_contract(channel_overrides: dict, reason: str) -> None:
    mapping = _traffic_mapping([_traffic_channel(**channel_overrides)])

    prepared = _prepare(_agent(), _traffic_run(), mapping)

    assert prepared.passed is False
    assert prepared.preflight['reason'] == reason


def test_traffic_sweep_requires_exactly_one_matrix_channel() -> None:
    mapping = _traffic_mapping([_traffic_channel(), _traffic_channel(valueColumn='node_fy_N_matrix_2')])

    prepared = _prepare(_agent(), _traffic_run(), mapping)

    assert prepared.passed is False
    assert prepared.preflight['reason'] == 'TRAFFIC_LOAD_MAPPING_UNSUPPORTED'


def test_traffic_sweep_fails_closed_when_readiness_not_ready() -> None:
    prepared = _prepare(
        _agent(readiness_status='BLOCKED'),
        _traffic_run(),
        _traffic_mapping(),
    )

    assert prepared.passed is False
    assert prepared.frozen_action is None


# ---------------------------------------------------------------------------
# 平台侧请求门禁
# ---------------------------------------------------------------------------

def _sweep_params(**overrides) -> dict:
    return {
        'runMode': 'REAL_DAMPER_PARAMETER_SWEEP',
        'solver': 'ANSYS',
        'scenario': 'TRAFFIC',
        'cases': [
            {**case, 'solverModule': 'damper_user300_viscous'}
            for case in VISCOUS_CASES
        ],
        **overrides,
    }


def test_store_accepts_ansys_traffic_sweep_request() -> None:
    assert PlatformStore._is_real_damper_parameter_sweep_request(
        'SOLVER_BATCH',
        _sweep_params(),
    ) is True


def test_store_accepts_openseespy_traffic_sweep_request() -> None:
    assert PlatformStore._is_real_damper_parameter_sweep_request(
        'SOLVER_BATCH',
        _sweep_params(
            solver='OPENSEESPY_INPROC',
            cases=[{**case, 'solverModule': 'damper_viscous'} for case in VISCOUS_CASES],
        ),
    ) is True


def test_store_rejects_traffic_sweep_on_wrong_job_type() -> None:
    assert PlatformStore._is_real_damper_parameter_sweep_request(
        'MULTI_OBJECTIVE_OPTIMIZATION',
        _sweep_params(),
    ) is False


def test_store_rejects_traffic_sweep_with_duplicate_case_ids() -> None:
    duplicated = [
        {**VISCOUS_CASES[0], 'solverModule': 'damper_user300_viscous'},
        {**VISCOUS_CASES[0], 'solverModule': 'damper_user300_viscous'},
    ]

    assert PlatformStore._is_real_damper_parameter_sweep_request(
        'SOLVER_BATCH',
        _sweep_params(cases=duplicated),
    ) is False


@pytest.mark.parametrize(
    ('solver', 'foreign_module'),
    [
        ('ANSYS', 'damper_viscous'),
        ('OPENSEESPY_INPROC', 'damper_user300_viscous'),
    ],
)
def test_store_rejects_cross_solver_damper_module(solver: str, foreign_module: str) -> None:
    # 两个求解器的黏滞模块名不同，跨求解器复用必须在参数校验处拒掉。
    from fastapi import HTTPException

    params = _sweep_params(
        solver=solver,
        cases=[{**case, 'solverModule': foreign_module} for case in VISCOUS_CASES],
    )

    with pytest.raises(HTTPException) as error:
        PlatformStore._validate_real_damper_parameter_sweep_params(params)

    assert error.value.detail['code'] == 'INVALID_DAMPER_MODULE'
