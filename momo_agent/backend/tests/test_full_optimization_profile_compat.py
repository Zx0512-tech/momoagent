from __future__ import annotations

import json
from types import SimpleNamespace

# Importing the router installs the same compatibility layer used by production API startup.
from app.api.v1 import agent_router as _agent_router  # noqa: F401
from app.services import agent_llm
from app.services.agent_conversation import AgentConversationMixin
from app.services.agent_engineering import EngineeringIntent, resolve_optimization_profile
from app.services.agent_harness import WorkflowStartInput
from app.services.agent_optimization_compat import (
    CANONICAL_OPTIMIZATION_TASK,
    LEGACY_FULL_RESPONSE_IDS,
    LEGACY_FULL_TASK,
    normalize_legacy_full_workflow_start,
)
from app.services.agent_task_handlers import DamperOptimizationTaskHandler


def _legacy_workflow_start() -> dict:
    return {
        'taskType': LEGACY_FULL_TASK,
        'fullOptimizationIntent': {
            'taskType': LEGACY_FULL_TASK,
            'solver': 'ANSYS',
            'scenario': 'EARTHQUAKE',
            'useVerifiedTemplateLoads': True,
            'requiresRealFem': True,
            'summary': '执行完整优化',
        },
    }


def test_legacy_full_workflow_start_normalizes_to_damper_full_profile() -> None:
    normalized = normalize_legacy_full_workflow_start(_legacy_workflow_start())

    assert normalized['taskType'] == CANONICAL_OPTIMIZATION_TASK
    intent = normalized['engineeringIntent']
    assert intent['taskType'] == CANONICAL_OPTIMIZATION_TASK
    assert intent['optimizationProfile'] == 'FULL'
    assert intent['solver'] == 'ANSYS'
    assert intent['loadKind'] == 'EARTHQUAKE'
    assert intent['damperType'] == 'VISCOUS'
    assert intent['selectedLayoutId'] == 'TWO_PER_TOWER'
    assert intent['responseIds'] == LEGACY_FULL_RESPONSE_IDS


def test_historical_full_workflow_payload_remains_readable_outside_new_start_scope() -> None:
    # PR2 只规范化新 workflow.start；历史 FULL run 的澄清/恢复仍依赖旧 schema。
    parsed = WorkflowStartInput.model_validate(_legacy_workflow_start())

    assert parsed.task_type == LEGACY_FULL_TASK
    assert parsed.full_optimization_intent is not None
    assert parsed.full_optimization_intent.solver == 'ANSYS'


def test_explicit_legacy_full_dispatch_bypasses_llm_and_creates_canonical_run() -> None:
    captured: dict = {}

    class Service(AgentConversationMixin):
        def _create_engineering_run(self, repository, session, content, now, **kwargs):
            captured.update(kwargs)
            captured['content'] = content
            return {'runId': 'run-canonical', 'taskType': CANONICAL_OPTIMIZATION_TASK}

        def _decorate_run(self, run):
            return run

    repository = SimpleNamespace(get_run=lambda _run_id: None)
    result = Service()._dispatch_message(
        repository,
        {'sessionId': 'session-1'},
        '执行完整阻尼优化',
        '2026-09-01T00:00:00Z',
        None,
        LEGACY_FULL_TASK,
    )

    assert result['taskType'] == CANONICAL_OPTIMIZATION_TASK
    assert captured['requested_task'] == CANONICAL_OPTIMIZATION_TASK
    assert captured['intent_override'].task_type == CANONICAL_OPTIMIZATION_TASK
    assert captured['intent_override'].optimization_profile == 'FULL'
    assert captured['route_evidence']['requestedTask'] == LEGACY_FULL_TASK
    assert captured['route_evidence']['resolvedTask'] == CANONICAL_OPTIMIZATION_TASK


def test_llm_engineering_parser_preserves_optimization_profile() -> None:
    payload = {
        'taskType': CANONICAL_OPTIMIZATION_TASK,
        'solver': 'ANSYS',
        'damperType': 'VISCOUS',
        'damperTypes': [],
        'loadKind': 'EARTHQUAKE',
        'selectedLayoutId': 'TWO_PER_TOWER',
        'responseIds': ['max_girder_end_displacement'],
        'budgetProfile': 'STANDARD',
        'optimizationProfile': 'FULL',
        'requiresRealFem': True,
        'missingFields': [],
        'summary': '使用完整 Profile 优化',
    }

    intent = agent_llm._parse_engineering_intent(json.dumps(payload, ensure_ascii=False))

    assert intent.task_type == CANONICAL_OPTIMIZATION_TASK
    assert intent.optimization_profile == 'FULL'


def test_damper_handler_freezes_profile_and_resolved_policy() -> None:
    intent = EngineeringIntent.model_validate({
        'taskType': CANONICAL_OPTIMIZATION_TASK,
        'solver': 'ANSYS',
        'damperType': 'VISCOUS',
        'loadKind': 'EARTHQUAKE',
        'selectedLayoutId': 'TWO_PER_TOWER',
        'responseIds': ['max_girder_end_displacement'],
        'optimizationProfile': 'FULL',
        'requiresRealFem': True,
        'missingFields': [],
        'summary': '使用完整 Profile 优化',
    })

    contract = DamperOptimizationTaskHandler().build_contract_from_intent(
        intent,
        load_import=None,
    )
    policy = resolve_optimization_profile('FULL')

    assert contract['taskType'] == CANONICAL_OPTIMIZATION_TASK
    assert contract['optimizationProfile'] == 'FULL'
    assert contract['optimizationPolicy'] == policy.model_dump(by_alias=True)
    assert contract['budget'] == policy.budget()


def test_standard_profile_remains_default_for_canonical_damper_optimization() -> None:
    intent = EngineeringIntent.model_validate({
        'taskType': CANONICAL_OPTIMIZATION_TASK,
        'solver': 'OPENSEESPY_INPROC',
        'damperType': 'VISCOUS',
        'loadKind': 'WIND',
        'selectedLayoutId': 'TWO_PER_TOWER',
        'responseIds': ['cumulative_displacement'],
        'requiresRealFem': True,
        'missingFields': [],
        'summary': '标准阻尼器优化',
    })

    contract = DamperOptimizationTaskHandler().build_contract_from_intent(
        intent,
        load_import=None,
    )

    assert intent.optimization_profile == 'STANDARD'
    assert contract['optimizationProfile'] == 'STANDARD'
    assert contract['optimizationPolicy']['profile'] == 'STANDARD'
