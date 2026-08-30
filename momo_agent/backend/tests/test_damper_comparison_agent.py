from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.agents.damper_comparison import ANALYSIS_WORKFLOW_PATHS, DamperComparisonAgent
from app.services.agent_engineering import build_damper_comparison_contract


def _profile() -> dict:
    return {
        'schemaVersion': '1.0',
        'solver': {'name': 'ANSYS_MAPDL', 'version': '2024 R1'},
        'responseContract': {'id': 'ANSYS_RESPONSE_R1'},
        'userElement': {'name': 'USER300', 'calibrationHashVerified': True},
    }


def _agent() -> DamperComparisonAgent:
    return DamperComparisonAgent(
        planner=SimpleNamespace(),
        store=SimpleNamespace(),
        dispatcher=SimpleNamespace(),
        readiness_builder=lambda *_args, **_kwargs: {'status': 'READY'},
        preflight_runner=lambda path: {
            'kind': 'undamped_baseline',
            # 预检返回的求解器由模板决定；比选门会核对它与审批求解器一致。
            'solver': 'openseespy_inproc' if 'openseespy' in Path(path).name else 'ansys',
            'execution_mode': 'run',
            'path_checks': {'template': {'exists': True}},
        },
        solver_profile_builder=lambda *_args, **_kwargs: _profile(),
        calibration_builder=lambda types, **_kwargs: [
            {'damperType': item, 'status': 'VERIFIED', 'sha256': str(index) * 64}
            for index, item in enumerate(types, start=1)
        ],
    )


def test_prepare_accepts_openseespy_with_registered_template() -> None:
    """OpenSeesPy 比选已登记：两侧都有逐型 USER300 标定证据。

    证据类别不同（ANSYS 是单元验收，OpenSeesPy 是运行时力法则一致性），
    但都逐型登记且哈希可校验，因此放行口径按注册表判定而非写死求解器名。
    """
    agent = _agent()
    contract = build_damper_comparison_contract(
        solver='OPENSEESPY_INPROC',
        damper_types=['VISCOUS', 'EDDY_CURRENT'],
        response_ids=['max_tower_base_shear'],
    )

    prepared = agent.prepare_approval(
        {
            'runId': 'comparison-1',
            'intent': {'loadKind': 'EARTHQUAKE'},
            'workflowContract': contract,
        },
        mapping={'loadKind': 'EARTHQUAKE', 'channels': []},
        standard_artifact_id='load-1',
        standard_sha256='a' * 64,
    )

    assert prepared.passed
    assert prepared.frozen_action['solver'] == 'OPENSEESPY_INPROC'
    assert prepared.frozen_action['workflowConfigPath'] == ANALYSIS_WORKFLOW_PATHS['OPENSEESPY_INPROC']


def test_prepare_rejects_unregistered_solver_before_preflight() -> None:
    """注册表是唯一事实来源：未登记模板的求解器必须失败关闭。"""
    agent = _agent()
    contract = build_damper_comparison_contract(
        solver='ANSYS',
        damper_types=['VISCOUS', 'EDDY_CURRENT'],
        response_ids=['max_tower_base_shear'],
    )
    contract['solver'] = 'ABAQUS'

    prepared = agent.prepare_approval(
        {
            'runId': 'comparison-1',
            'intent': {'loadKind': 'EARTHQUAKE'},
            'workflowContract': contract,
        },
        mapping={'loadKind': 'EARTHQUAKE', 'channels': []},
        standard_artifact_id=None,
        standard_sha256=None,
    )

    assert not prepared.passed
    assert prepared.failure_status == 'UNSUPPORTED'
    assert prepared.frozen_action is None


def test_prepare_returns_two_text_command_streams_without_artifact_writes() -> None:
    agent = _agent()
    contract = build_damper_comparison_contract(
        solver='ANSYS',
        damper_types=['VISCOUS', 'EDDY_CURRENT'],
        response_ids=['max_tower_base_shear'],
    )

    prepared = agent.prepare_approval(
        {
            'runId': 'comparison-2',
            'intent': {'loadKind': 'EARTHQUAKE'},
            'workflowContract': contract,
        },
        mapping={'loadKind': 'EARTHQUAKE', 'channels': []},
        standard_artifact_id='load-2',
        standard_sha256='e' * 64,
    )

    assert prepared.passed
    assert {item['damperType'] for item in prepared.pending_command_streams} == {'VISCOUS', 'EDDY_CURRENT'}
    assert all(item['commandText'].strip() for item in prepared.pending_command_streams)
    assert 'artifactId' not in prepared.pending_command_streams[0]


def test_comparison_command_keeps_force_derived_viscous_c_in_solver_units() -> None:
    """等峰值对比由 N 制力上限反算 C，不能再按界面工程单位重复换算。"""
    contract = build_damper_comparison_contract(
        solver='ANSYS',
        damper_types=['VISCOUS', 'EDDY_CURRENT'],
        response_ids=['max_tower_base_shear'],
    )

    streams = DamperComparisonAgent._build_command_streams('comparison-units', contract)

    viscous_case = next(case for case in contract['cases'] if case['damperType'] == 'VISCOUS')
    viscous_stream = next(item for item in streams if item['damperType'] == 'VISCOUS')
    assert f"ansys_C={viscous_case['parameters']['c']}" in viscous_stream['commandText']


def test_review_rejects_a_single_completed_case() -> None:
    outcome = _agent().review(
        {
            'jobId': 'comparison-3',
            'status': 'SUCCEEDED',
            'result': {
                'mode': 'real_damper_comparison',
                'caseResults': [{'damperType': 'VISCOUS', 'status': 'completed', 'isVerifiedSolverOutput': True}],
            },
            'artifacts': [],
        }
    )

    assert not outcome.accepted
    assert outcome.checks['twoDistinctCases'] is False


def test_review_rejects_missing_response_comparison_metrics() -> None:
    """没有可对比的共有数值指标就不构成对比结论。"""
    outcome = _agent().review(
        {
            'jobId': 'comparison-4',
            'status': 'SUCCEEDED',
            'result': {
                'mode': 'real_damper_comparison',
                'caseResults': [
                    {'damperType': 'VISCOUS', 'status': 'completed', 'isVerifiedSolverOutput': True},
                    {'damperType': 'EDDY_CURRENT', 'status': 'completed', 'isVerifiedSolverOutput': True},
                ],
                'responseComparison': {'metrics': {}},
            },
            'artifacts': [],
        }
    )

    assert not outcome.accepted
    assert outcome.checks['responseComparison'] is False
