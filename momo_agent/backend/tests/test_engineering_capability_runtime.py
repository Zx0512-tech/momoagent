from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field
import pytest

from app.agents.tools import ToolExecutionError, ToolRisk
from app.capabilities.context import build_runtime_turn_payload
from app.capabilities.retention import build_compression_state_anchor, volatile_runtime_keys
from app.capabilities.runtime import (
    CapabilityDispatcher,
    CapabilityRegistry,
    EngineeringCapability,
    EvidencePolicy,
)
from app.services.agent_harness import harness_capability_registry, harness_step_tool_catalog


class DemoInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra='forbid', strict=True)
    run_id: str = Field(alias='runId')


def _demo_capability() -> EngineeringCapability:
    return EngineeringCapability(
        capability_id='result.demo',
        description='demo',
        input_model=DemoInput,
        risk=ToolRisk.READ_ONLY,
        requires_approval=False,
        prerequisites=(),
        evidence_policy=EvidencePolicy.NONE,
    )


def test_capability_registry_uses_input_model_as_tool_schema_truth() -> None:
    registry = CapabilityRegistry()
    registry.register(_demo_capability())
    schema = registry.tool_schemas(['result.demo'])[0]
    assert schema['inputSchema'] == DemoInput.model_json_schema(by_alias=True)
    assert schema['capability']['capabilityId'] == 'result.demo'
    assert schema['capability']['sideEffect'] == 'NONE'


def test_capability_registry_fails_closed_for_duplicates_and_unknown_ids() -> None:
    registry = CapabilityRegistry()
    capability = _demo_capability()
    registry.register(capability)
    with pytest.raises(ValueError):
        registry.register(capability)
    with pytest.raises(ToolExecutionError) as exc:
        registry.tool_schemas(['result.missing'])
    assert exc.value.code == 'CAPABILITY_NOT_REGISTERED'


def test_dispatcher_enforces_stage_before_validation() -> None:
    registry = CapabilityRegistry()
    registry.register(_demo_capability())
    dispatcher = CapabilityDispatcher(registry)
    with pytest.raises(ToolExecutionError) as exc:
        dispatcher.authorize_and_validate(
            'result.demo', {'runId': 'agr_1'}, allowed_capabilities=[],
        )
    assert exc.value.code == 'CAPABILITY_NOT_ALLOWED'


def test_harness_registry_carries_engineering_policies_for_migrated_samples() -> None:
    registry = harness_capability_registry()
    compare_runs = registry.require('result.compare_runs')
    analysis_run = registry.require('analysis.run')
    assert compare_runs.evidence_policy is EvidencePolicy.REGISTERED_ARTIFACT_ONLY
    assert 'PROJECT_BOUND' in compare_runs.prerequisites
    assert analysis_run.risk is ToolRisk.SOLVER_EXECUTION
    assert analysis_run.requires_approval is True
    assert analysis_run.evidence_policy is EvidencePolicy.REAL_FEM_REQUIRED


def test_dynamic_runtime_context_discloses_metadata_without_duplicating_schema() -> None:
    tools = harness_step_tool_catalog(['result.compare_runs'])
    payload = build_runtime_turn_payload(
        workflow_state={'currentStep': 'QUERY', 'allowedTools': ['result.compare_runs']},
        user_content='比较两次结果',
        tools=tools,
        turn_context={'engineeringProjectContext': {'project': {'projectId': 'prj_1'}}},
    )
    runtime = payload['runtimeContext']
    assert runtime['scope'] == 'TURN_SNAPSHOT'
    assert runtime['availableCapabilities'][0]['capabilityId'] == 'result.compare_runs'
    assert 'inputSchema' not in runtime['availableCapabilities'][0]
    assert runtime['engineeringProjectContext']['project']['projectId'] == 'prj_1'


def test_compression_state_anchor_keeps_refs_not_volatile_runtime_views() -> None:
    anchor = build_compression_state_anchor(
        workflow_state={
            'runId': 'agr_1',
            'taskType': 'ANALYSIS',
            'currentStep': 'EVIDENCE_REVIEW',
            'completedSteps': ['REQUIREMENTS', 'EXECUTION'],
            'allowedTools': ['analysis.review'],
        },
        run={
            'runId': 'agr_1',
            'taskType': 'ANALYSIS',
            'pendingApprovalId': 'appr_1',
            'artifactIds': ['art_1'],
            'reportArtifactId': 'art_report',
            'engineeringContract': {'contractHash': 'sha256:abc'},
        },
    )
    assert anchor['runId'] == 'agr_1'
    assert anchor['artifactIds'] == ['art_1']
    assert anchor['contractHash'] == 'sha256:abc'
    assert 'allowedTools' not in anchor
    assert 'availableCapabilities' in volatile_runtime_keys()
