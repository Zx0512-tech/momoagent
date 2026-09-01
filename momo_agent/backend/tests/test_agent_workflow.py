from __future__ import annotations

import pytest
from app.main import app

from app.agents.tools import ToolExecutionError, ToolRisk
from app.agents.workflows import (
    WorkflowGuard,
    WorkflowToolCall,
    freeze_workflow,
    workflow_definition,
)
from app.services.agent_engineering import build_engineering_contract


def test_workflow_snapshot_is_canonical_and_stable() -> None:
    definition = workflow_definition('ANALYSIS')

    first = freeze_workflow(definition)
    second = freeze_workflow(definition.model_copy(deep=True))

    assert first['workflowId'] == 'analysis'
    assert first['workflowVersion'] == '1.1.0'
    assert first['workflowSha256'] == second['workflowSha256']
    assert len(first['workflowSha256']) == 64
    assert first['workflowSnapshot']['initialStep'] == 'REQUIREMENTS'


def test_guard_rejects_tool_outside_current_step_before_execution() -> None:
    snapshot = freeze_workflow(workflow_definition('ANALYSIS'))['workflowSnapshot']
    guard = WorkflowGuard()

    with pytest.raises(ToolExecutionError) as error:
        guard.authorize(
            workflow_snapshot=snapshot,
            current_step='PREFLIGHT',
            completed_steps=('REQUIREMENTS', 'LOAD_PREPARATION'),
            tool_call=WorkflowToolCall(name='analysis.run'),
        )

    assert error.value.code == 'WORKFLOW_STEP_VIOLATION'
    assert error.value.details['currentStep'] == 'PREFLIGHT'
    assert error.value.details['allowedTools'] == ['analysis.prepare']


def test_guard_enforces_prerequisites_attempts_approval_and_idempotency() -> None:
    snapshot = freeze_workflow(workflow_definition('ANALYSIS'))['workflowSnapshot']
    guard = WorkflowGuard()

    with pytest.raises(ToolExecutionError) as missing_prerequisite:
        guard.authorize(
            workflow_snapshot=snapshot,
            current_step='EXECUTION',
            completed_steps=('REQUIREMENTS',),
            tool_call=WorkflowToolCall(
                name='analysis.run',
                risk=ToolRisk.SOLVER_EXECUTION,
                requires_approval=True,
            ),
        )
    assert missing_prerequisite.value.code == 'WORKFLOW_PREREQUISITE_MISSING'

    completed = ('REQUIREMENTS', 'LOAD_PREPARATION', 'PREFLIGHT', 'WAITING_APPROVAL')
    with pytest.raises(ToolExecutionError) as approval:
        guard.authorize(
            workflow_snapshot=snapshot,
            current_step='EXECUTION',
            completed_steps=completed,
            tool_call=WorkflowToolCall(
                name='analysis.run',
                risk=ToolRisk.SOLVER_EXECUTION,
                requires_approval=True,
            ),
        )
    assert approval.value.code == 'APPROVAL_REQUIRED'

    with pytest.raises(ToolExecutionError) as idempotency:
        guard.authorize(
            workflow_snapshot=snapshot,
            current_step='EXECUTION',
            completed_steps=completed,
            tool_call=WorkflowToolCall(
                name='analysis.run',
                risk=ToolRisk.SOLVER_EXECUTION,
                requires_approval=True,
                approved=True,
            ),
        )
    assert idempotency.value.code == 'IDEMPOTENCY_KEY_REQUIRED'

    decision = guard.authorize(
        workflow_snapshot=snapshot,
        current_step='EXECUTION',
        completed_steps=completed,
        tool_call=WorkflowToolCall(
            name='analysis.run',
            risk=ToolRisk.SOLVER_EXECUTION,
            requires_approval=True,
            approved=True,
            idempotency_key='agr_1:analysis:run',
        ),
    )
    assert decision.allowed is True


def test_guard_expands_transitive_prerequisites_for_comparison() -> None:
    snapshot = freeze_workflow(workflow_definition('DAMPER_COMPARISON'))['workflowSnapshot']
    approval_step = next(item for item in snapshot['steps'] if item['stepId'] == 'WAITING_APPROVAL')

    assert set(approval_step['prerequisites']) == {'REQUIREMENTS', 'CALIBRATION', 'PREFLIGHT'}
    with pytest.raises(ToolExecutionError) as error:
        WorkflowGuard().authorize(
            workflow_snapshot=snapshot,
            current_step='WAITING_APPROVAL',
            completed_steps=('PREFLIGHT',),
            tool_call=WorkflowToolCall(name='approval.request'),
        )
    assert error.value.code == 'WORKFLOW_PREREQUISITE_MISSING'
    assert set(error.value.details['missingSteps']) == {'REQUIREMENTS', 'CALIBRATION'}


def test_guard_enforces_analysis_real_solve_limit() -> None:
    snapshot = freeze_workflow(workflow_definition('ANALYSIS'))['workflowSnapshot']
    with pytest.raises(ToolExecutionError) as error:
        WorkflowGuard().authorize(
            workflow_snapshot=snapshot,
            current_step='EXECUTION',
            completed_steps=('REQUIREMENTS', 'LOAD_PREPARATION', 'PREFLIGHT', 'WAITING_APPROVAL'),
            tool_call=WorkflowToolCall(
                name='analysis.run',
                risk=ToolRisk.SOLVER_EXECUTION,
                requiresApproval=True,
                approved=True,
                idempotencyKey='agr:solve:1',
                usage={'realSolveCount': 2},
            ),
        )
    assert error.value.code == 'WORKFLOW_LIMIT_EXCEEDED'


def test_guard_reads_success_gate_when_transition_context_is_provided() -> None:
    snapshot = freeze_workflow(workflow_definition('ANALYSIS'))['workflowSnapshot']
    with pytest.raises(ToolExecutionError) as error:
        WorkflowGuard().advance(
            workflow_snapshot=snapshot,
            current_step='PREFLIGHT',
            completed_steps=('REQUIREMENTS', 'LOAD_PREPARATION'),
            gate_passed=True,
            gate_context={'preflight': {'passed': False}},
        )
    assert error.value.code == 'WORKFLOW_GATE_FAILED'


def test_guard_stops_repeated_no_progress_and_step_attempt_overflow() -> None:
    snapshot = freeze_workflow(workflow_definition('ANALYSIS'))['workflowSnapshot']
    guard = WorkflowGuard()

    with pytest.raises(ToolExecutionError) as loop:
        guard.authorize(
            workflow_snapshot=snapshot,
            current_step='PREFLIGHT',
            completed_steps=('REQUIREMENTS', 'LOAD_PREPARATION'),
            step_attempt=1,
            repeated_no_progress=2,
            tool_call=WorkflowToolCall(name='analysis.prepare'),
        )
    assert loop.value.code == 'HARNESS_LOOP_DETECTED'

    with pytest.raises(ToolExecutionError) as attempts:
        guard.authorize(
            workflow_snapshot=snapshot,
            current_step='PREFLIGHT',
            completed_steps=('REQUIREMENTS', 'LOAD_PREPARATION'),
            step_attempt=3,
            tool_call=WorkflowToolCall(name='analysis.prepare'),
        )
    assert attempts.value.code == 'WORKFLOW_RETRY_EXHAUSTED'


def test_optimization_definition_preserves_python_stage_order_and_limits() -> None:
    definition = workflow_definition('DAMPER_OPTIMIZATION')

    assert [step.step_id for step in definition.steps] == [
        'REQUIREMENTS', 'PREFLIGHT', 'WAITING_APPROVAL', 'BASELINE', 'DOE',
        'SURROGATE', 'ACTIVE_LEARNING', 'CANDIDATES', 'RECOMMENDATION',
        'FEM_VALIDATION', 'REVIEW', 'REPORT', 'COMPLETED', 'FAILED', 'CANCELLED',
    ]
    assert definition.limits == {
        'doeDesignCount': 24,
        'surrogateCv': 10,
        'maxActiveLearningIterations': 2,
        'candidateCount': 728,
        'maxReviewIterations': 1,
    }


def test_undamped_analysis_contract_has_single_solve_without_optimization_budget() -> None:
    contract = build_engineering_contract(
        task_type='ANALYSIS',
        solver='OPENSEESPY_INPROC',
        damper_type=None,
        response_ids=['max_tower_base_shear'],
        selected_layout_id=None,
    )

    assert contract['budget'] == {'realSolveCount': 1, 'executionTimeoutS': 7200}
    assert contract['executionEstimate'] == {'mode': 'SINGLE_ANALYSIS', 'realSolveCount': 1}
    assert 'doeDesignCount' not in contract['budget']
    assert 'candidateCount' not in contract['executionEstimate']


def test_tool_execution_error_has_global_v1_handler() -> None:
    assert ToolExecutionError in app.exception_handlers
