from __future__ import annotations

from types import SimpleNamespace

import app.agents.damper_optimization as damper_optimization_module
from app.agents.core import AgentContext
from app.agents.damper_optimization import DamperOptimizationAgent
from app.agents.evidence_gates import engineering_preflight_passed, solver_version_profile_passed
from app.services.agent_engineering import build_engineering_contract


def _profile() -> dict:
    return {
        'schemaVersion': '1.0',
        'solver': {'name': 'ANSYS_MAPDL', 'version': '2024 R1'},
        'responseContract': {'id': 'ANSYS_RESPONSE_R1'},
        'userElement': {'name': 'USER300', 'calibrationHashVerified': True},
    }


def _agent() -> DamperOptimizationAgent:
    return DamperOptimizationAgent(
        planner=SimpleNamespace(),
        store=SimpleNamespace(),
        dispatcher=SimpleNamespace(),
        readiness_builder=lambda *_args, **_kwargs: {'status': 'READY'},
        preflight_runner=lambda _path: {
            'kind': 'baseline_optimization_workflow',
            'baseline': {
                'solver': 'ansys',
                'execution_mode': 'run',
                'path_checks': {'template': {'exists': True}},
            },
            'optimization': {
                'solver': 'ansys',
                'execution_mode': 'run',
                'path_checks': {'template': {'exists': True}},
            },
        },
        solver_profile_builder=lambda *_args, **_kwargs: _profile(),
    )


def test_plan_supports_the_shared_optimization_agent_context() -> None:
    intent = SimpleNamespace(
        task_type='DAMPER_OPTIMIZATION',
        solver='ANSYS',
        damper_type='VISCOUS',
        response_ids=['max_tower_base_shear'],
        missing_fields=[],
    )
    agent = DamperOptimizationAgent(
        planner=SimpleNamespace(plan_engineering=lambda *_args, **_kwargs: SimpleNamespace(
            planner_mode='LLM', intent=intent,
        )),
        store=SimpleNamespace(),
        dispatcher=SimpleNamespace(),
    )

    result = agent.plan(AgentContext(session_id='s1', goal='优化', requested_task='DAMPER_OPTIMIZATION'))

    assert result.workflow_contract['taskType'] == 'DAMPER_OPTIMIZATION'
    assert result.plan


def test_prepare_approval_returns_real_preflight_without_persisting() -> None:
    agent = _agent()
    assert engineering_preflight_passed({
        'kind': 'baseline_optimization_workflow',
        'baseline': {
            'solver': 'ansys',
            'execution_mode': 'run',
            'path_checks': {'template': {'exists': True}},
        },
        'optimization': {
            'solver': 'ansys',
            'execution_mode': 'run',
            'path_checks': {'template': {'exists': True}},
        },
    }, 'ANSYS')
    assert solver_version_profile_passed(_profile(), require_user300=True)
    run = {
        'runId': 'run-optimization',
        'intent': {'loadKind': 'EARTHQUAKE'},
        'workflowContract': build_engineering_contract(
            task_type='DAMPER_OPTIMIZATION',
            solver='ANSYS',
            damper_type='VISCOUS',
            response_ids=['max_tower_base_shear'],
        ),
    }

    prepared = agent.prepare_approval(
        run,
        mapping={'loadKind': 'EARTHQUAKE', 'channels': []},
        standard_artifact_id='load-1',
        standard_sha256='d' * 64,
    )

    assert prepared.passed, prepared.preflight
    assert prepared.approval_action == 'RUN_ENGINEERING_WORKFLOW'
    assert prepared.frozen_action['selectedLayoutId'] == 'TWO_PER_TOWER'
    assert prepared.preflight['passed'] is True
    assert 'pendingApprovalId' not in run


def test_prepare_approval_accepts_extracted_damper_evaluation_metrics() -> None:
    """真实时程已导出时，力、行程和耗能可作为冻结的评价指标。"""
    agent = _agent()
    run = {
        'runId': 'run-damper-metrics',
        'intent': {'loadKind': 'EARTHQUAKE'},
        'workflowContract': build_engineering_contract(
            task_type='DAMPER_OPTIMIZATION',
            solver='ANSYS',
            damper_type='VISCOUS',
            response_ids=[
                'max_damper_force',
                'max_damper_stroke',
                'dissipated_energy',
            ],
        ),
    }

    prepared = agent.prepare_approval(
        run,
        mapping={'loadKind': 'EARTHQUAKE', 'channels': []},
        standard_artifact_id='load-1',
        standard_sha256='d' * 64,
    )

    assert prepared.passed, prepared.preflight
    assert prepared.frozen_action['responseIds'] == [
        'max_damper_force',
        'max_damper_stroke',
        'dissipated_energy',
    ]


def test_review_keeps_damper_specific_load_and_layout_checks() -> None:
    agent = _agent()
    outcome = agent.review(
        {
            'jobId': 'job-1',
            'status': 'SUCCEEDED',
            'result': {
                'mode': 'real_baseline_optimization',
                'baselineStatus': 'completed',
                'validationStatus': {'all_verified_execution': True, 'all_accepted': True},
                'reviewStatus': {'all_verified_execution': True, 'all_accepted': True},
                'finalRecommendationStatus': 'ACCEPTED',
                'solverVersionProfile': _profile(),
                'inputProvenance': [
                    {'field': 'solver', 'source': 'USER_DECISION'},
                    {'field': 'workflowConfigPath', 'source': 'VERIFIED_TEMPLATE'},
                    {'field': 'loadCase', 'source': 'VERIFIED_TEMPLATE'},
                ],
            },
            'artifacts': [],
        },
        workflow_contract={
            'taskType': 'DAMPER_OPTIMIZATION',
            'loadArtifactId': 'load-1',
            'loadSha256': 'd' * 64,
            'selectedLayoutId': 'TWO_PER_TOWER',
            'selectedLayout': {'nodePairs': [[1, 2]]},
        },
    )

    assert not outcome.accepted
    assert outcome.evidence_mode == 'DIAGNOSTIC_ONLY'
    assert outcome.checks['approvedLoadArtifact'] is False
    assert outcome.checks['approvedDamperLayout'] is False


def test_openseespy_review_does_not_require_user300(monkeypatch) -> None:
    requirements: list[bool] = []

    def capture_requirement(_profile: dict, *, require_user300: bool) -> bool:
        requirements.append(require_user300)
        return True

    monkeypatch.setattr(
        damper_optimization_module,
        'solver_version_profile_passed',
        capture_requirement,
    )
    agent = _agent()

    agent.review(
        {
            'jobId': 'job-opensees',
            'status': 'SUCCEEDED',
            'result': {'solverVersionProfile': {'solver': {'name': 'OpenSeesPy', 'version': '3.7.1'}}},
            'artifacts': [],
        },
        workflow_contract={'solver': 'OPENSEESPY_INPROC'},
    )

    assert requirements == [False]
