from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import Any

import pytest

from app.agents.engineering import EngineeringAgent, PreparedApproval, ReviewOutcome
from app.services.agent_service import AgentService


class _DummyAgent(EngineeringAgent):
    task_type = 'DUMMY'
    approval_action = 'DUMMY_ACTION'

    def plan(self, context: Any) -> Any:
        return context

    def prepare_approval(self, run: dict[str, Any], **kwargs: Any) -> PreparedApproval:
        return PreparedApproval(passed=False)

    def review(self, job: dict[str, Any], *, workflow_contract: dict[str, Any] | None = None) -> ReviewOutcome:
        return ReviewOutcome(False, 'FAILED', 'FAILED', {}, 'failed')

    def build_report(self, run: dict[str, Any], job: dict[str, Any], outcome: ReviewOutcome) -> dict[str, Any]:
        return {}

    def narrative_facts(self, report: dict[str, Any], job: dict[str, Any]) -> dict[str, Any]:
        return {}


def test_prepared_approval_is_frozen_and_has_isolated_mutable_defaults() -> None:
    first = PreparedApproval(passed=True)
    second = PreparedApproval(passed=True)

    first.plan.append('plan item')

    assert second.plan == []
    with pytest.raises(FrozenInstanceError):
        first.passed = False  # type: ignore[misc]


def test_engineering_agent_is_abstract_and_service_registry_aliases_full_optimization() -> None:
    assert EngineeringAgent.__abstractmethods__
    assert not _DummyAgent.__abstractmethods__

    service = AgentService()
    optimization = service._agent_for('DAMPER_OPTIMIZATION')
    full = service._agent_for('FULL_OPTIMIZATION')

    assert optimization is not None
    assert type(full) is type(optimization)
    assert full.task_type == 'DAMPER_OPTIMIZATION'
    with pytest.raises(Exception) as exc_info:
        service._agent_for('NOT_A_TASK')
    assert getattr(exc_info.value, 'status_code', None) == 422
