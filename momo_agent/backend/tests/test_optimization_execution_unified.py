from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.agents.core import AgentContext
from app.agents.damper_optimization import DamperOptimizationAgent
from app.agents.task_registry import engineering_task_spec
from app.agents.tools import ToolExecutionError
from app.agents.workflows import workflow_definition
from app.api.v1.agent_schemas import AgentMessageCreateRequest
from app.services.agent_engineering import EngineeringIntent, resolve_optimization_profile
from app.services.agent_harness import WorkflowStartInput
from app.services.agent_task_handlers import DamperOptimizationTaskHandler, orchestration_handler


def _intent(profile: str = "FULL", solver: str = "OPENSEESPY_INPROC", load_kind: str = "WIND") -> EngineeringIntent:
    return EngineeringIntent.model_validate({
        "taskType": "DAMPER_OPTIMIZATION",
        "solver": solver,
        "damperType": "VISCOUS",
        "loadKind": load_kind,
        "selectedLayoutId": "TWO_PER_TOWER",
        "responseIds": ["cumulative_displacement"],
        "optimizationProfile": profile,
        "requiresRealFem": True,
        "missingFields": [],
        "summary": "统一优化执行",
    })


def test_optimization_agent_has_one_prepare_branch() -> None:
    assert hasattr(DamperOptimizationAgent, "_prepare_optimization")
    assert not hasattr(DamperOptimizationAgent, "_prepare_full_optimization")
    assert not hasattr(DamperOptimizationAgent, "_prepare_engineering_optimization")


def test_full_profile_uses_canonical_task_and_preserves_solver_and_load() -> None:
    intent = _intent()
    contract = DamperOptimizationTaskHandler().build_contract_from_intent(intent, load_import=None)
    policy = resolve_optimization_profile("FULL")
    assert contract["taskType"] == "DAMPER_OPTIMIZATION"
    assert contract["optimizationProfile"] == "FULL"
    assert contract["optimizationPolicy"] == policy.model_dump(by_alias=True)
    assert contract["solver"] == "OPENSEESPY_INPROC"
    assert contract["loadKind"] == "WIND"


def test_full_task_is_not_registered_in_new_system() -> None:
    assert engineering_task_spec("FULL_OPTIMIZATION") is None
    assert orchestration_handler("FULL_OPTIMIZATION") is None
    assert workflow_definition("DAMPER_OPTIMIZATION").workflow_id == "damper_optimization"
    with pytest.raises(ToolExecutionError):
        workflow_definition("FULL_OPTIMIZATION")


def test_new_api_and_harness_reject_retired_full_task_type() -> None:
    with pytest.raises(ValidationError):
        AgentMessageCreateRequest(content="完整优化", taskType="FULL_OPTIMIZATION")
    with pytest.raises(ValidationError):
        WorkflowStartInput.model_validate({"taskType": "FULL_OPTIMIZATION"})


def test_agent_context_only_accepts_canonical_optimization_task() -> None:
    context = AgentContext(session_id="s1", goal="完整优化", requested_task="DAMPER_OPTIMIZATION")
    assert context.requested_task == "DAMPER_OPTIMIZATION"
    with pytest.raises(ValidationError):
        AgentContext(session_id="s1", goal="完整优化", requested_task="FULL_OPTIMIZATION")
