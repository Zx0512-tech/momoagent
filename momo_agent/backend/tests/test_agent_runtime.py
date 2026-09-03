from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from app.agents.analysis import AnalysisAgent, AnalysisRuntimeTools
from app.agents.capabilities import ArtifactRef, FigureContract, FigurePanel
from app.agents.core import AgentContext, AgentState, AgentStateMachine, RepositorySessionMemory
from app.agents.tools import (
    ToolApprovalRequired,
    ToolExecutionError,
    ToolRisk,
    TypedAgentTool,
    TypedToolRegistry,
)
from app.services.agent_service import AgentService
from app.services.agent_engineering import EngineeringIntent


class _DoubleInput(BaseModel):
    value: int


class _DoubleOutput(BaseModel):
    doubled: int


def test_state_machine_allows_analysis_path_and_rejects_terminal_shortcut() -> None:
    machine = AgentStateMachine()

    assert machine.transition(AgentState.PLANNING, AgentState.PREFLIGHT) is AgentState.PREFLIGHT
    assert machine.transition(AgentState.PREFLIGHT, AgentState.WAITING_APPROVAL) is AgentState.WAITING_APPROVAL
    with pytest.raises(ValueError, match='PLANNING -> SUCCEEDED'):
        machine.transition(AgentState.PLANNING, AgentState.SUCCEEDED)


def test_repository_memory_is_session_scoped_normalized_and_bounded() -> None:
    messages = {
        'ags_1': [
            {'role': 'USER', 'content': '第一条', 'messageId': 'msg_1'},
            {'role': 'ASSISTANT', 'content': '第二条', 'messageId': 'msg_2'},
            {'role': 'USER', 'content': '第三条', 'messageId': 'msg_3'},
        ],
        'ags_2': [{'role': 'USER', 'content': '其他会话', 'messageId': 'msg_4'}],
    }
    memory = RepositorySessionMemory(lambda session_id: messages[session_id], max_messages=2)

    result = memory.load('ags_1')

    assert [message.content for message in result] == ['第二条', '第三条']
    assert [message.role for message in result] == ['assistant', 'user']
    assert all(message.metadata['messageId'] != 'msg_4' for message in result)


def test_typed_tool_requires_approval_and_idempotency_for_mutation() -> None:
    assert issubclass(ToolApprovalRequired, RuntimeError)
    registry = TypedToolRegistry()
    registry.register(TypedAgentTool(
        name='analysis.dispatch',
        description='创建真实分析任务',
        input_model=_DoubleInput,
        output_model=_DoubleOutput,
        risk=ToolRisk.MUTATING,
        requires_approval=True,
        handler=lambda payload: {'doubled': payload.value * 2},
    ))

    with pytest.raises(ToolApprovalRequired):
        registry.execute('analysis.dispatch', {'value': 2})
    with pytest.raises(ValueError, match='idempotency_key'):
        registry.execute('analysis.dispatch', {'value': 2}, approved=True)

    result = registry.execute(
        'analysis.dispatch',
        {'value': 2},
        approved=True,
        idempotency_key='agr_1:analysis:hash',
    )

    assert result == _DoubleOutput(doubled=4)


def test_typed_tool_registry_exposes_versioned_capability_metadata() -> None:
    registry = TypedToolRegistry()
    registry.register(TypedAgentTool(
        name='result.summarize',
        version='1.1.0',
        description='汇总标准求解结果',
        input_model=_DoubleInput,
        output_model=_DoubleOutput,
        risk=ToolRisk.READ_ONLY,
        requires_approval=False,
        timeout_seconds=30,
        artifact_kinds=('RESULT_SUMMARY',),
        handler=lambda payload: {'doubled': payload.value * 2},
    ))

    descriptor = registry.describe('result.summarize')
    listed = registry.list_tools()

    assert descriptor.name == 'result.summarize'
    assert descriptor.version == '1.1.0'
    assert descriptor.input_schema['properties']['value']['type'] == 'integer'
    assert descriptor.output_schema['properties']['doubled']['type'] == 'integer'
    assert descriptor.timeout_seconds == 30
    assert descriptor.artifact_kinds == ('RESULT_SUMMARY',)
    assert listed == [descriptor]


def test_typed_tool_registry_returns_structured_boundary_errors() -> None:
    registry = TypedToolRegistry()
    registry.register(TypedAgentTool(
        name='analysis.read',
        description='读取分析结果',
        input_model=_DoubleInput,
        output_model=_DoubleOutput,
        risk=ToolRisk.READ_ONLY,
        requires_approval=False,
        handler=lambda _payload: {'unexpected': True},
    ))

    with pytest.raises(ToolExecutionError) as unknown:
        registry.execute('missing.tool', {'value': 1})
    assert unknown.value.code == 'TOOL_NOT_FOUND'

    with pytest.raises(ToolExecutionError) as invalid_input:
        registry.execute('analysis.read', {'value': 'not-an-integer'})
    assert invalid_input.value.code == 'INPUT_VALIDATION_ERROR'
    assert 'not-an-integer' not in str(invalid_input.value)
    assert 'not-an-integer' not in repr(invalid_input.value.details)

    with pytest.raises(ToolExecutionError) as invalid_output:
        registry.execute('analysis.read', {'value': 1})
    assert invalid_output.value.code == 'OUTPUT_VALIDATION_ERROR'


def test_typed_tool_registry_rejects_silent_input_and_output_changes() -> None:
    coercing = TypedToolRegistry()
    coercing.register(TypedAgentTool(
        name='analysis.coerce',
        description='当需要验证整数输入时使用；不得修改输入类型。',
        input_model=_DoubleInput,
        output_model=_DoubleOutput,
        risk=ToolRisk.READ_ONLY,
        requires_approval=False,
        handler=lambda payload: {'doubled': payload.value * 2},
    ))

    with pytest.raises(ToolExecutionError) as coerced_input:
        coercing.execute('analysis.coerce', {'value': '2'})
    assert coerced_input.value.code == 'INPUT_VALIDATION_ERROR'
    assert coerced_input.value.details[0]['location'] == 'value'

    dropping = TypedToolRegistry()
    dropping.register(TypedAgentTool(
        name='analysis.drop-output',
        description='当需要验证输出合同完整性时使用；不得裁剪输出。',
        input_model=_DoubleInput,
        output_model=_DoubleOutput,
        risk=ToolRisk.READ_ONLY,
        requires_approval=False,
        handler=lambda payload: {'doubled': payload.value * 2, 'unexpected': True},
    ))

    with pytest.raises(ToolExecutionError) as changed_output:
        dropping.execute('analysis.drop-output', {'value': 2})
    assert changed_output.value.code == 'OUTPUT_VALIDATION_ERROR'
    assert changed_output.value.details[0]['location'] == 'unexpected'


def test_analysis_agent_builds_existing_contract_from_structured_planner() -> None:
    intent = EngineeringIntent(
        taskType='ANALYSIS',
        solver='ANSYS',
        damperType=None,
        loadKind='EARTHQUAKE',
        responseIds=['max_tower_base_shear'],
        missingFields=[],
        summary='执行真实地震响应分析。',
    )
    planner = SimpleNamespace(plan_engineering=lambda *args, **kwargs: SimpleNamespace(
        planner_mode='LLM',
        intent=intent,
    ))
    memory = RepositorySessionMemory(
        lambda _session_id: [{'role': 'USER', 'content': '此前消息', 'messageId': 'msg_1'}]
    )
    agent = AnalysisAgent(planner=planner, memory=memory)

    result = agent.plan(AgentContext(
        session_id='ags_1',
        goal='使用 ANSYS 提取塔底剪力',
        requested_task='ANALYSIS',
    ))

    assert result.intent is intent
    assert result.workflow_contract['taskType'] == 'ANALYSIS'
    assert result.workflow_contract['solver'] == 'ANSYS'
    assert result.workflow_contract['responseIds'] == ['max_tower_base_shear']
    assert result.planner_mode == 'LLM'
    assert result.recent_message_count == 1


def test_analysis_agent_short_circuits_contract_for_clarification() -> None:
    intent = EngineeringIntent(
        taskType='CLARIFICATION',
        solver='ANSYS',
        damperType=None,
        loadKind='EARTHQUAKE',
        responseIds=[],
        missingFields=['responseIds'],
        summary='请补充需要提取的响应指标。',
    )
    planner = SimpleNamespace(plan_engineering=lambda *args, **kwargs: SimpleNamespace(
        planner_mode='LLM',
        intent=intent,
    ))
    agent = AnalysisAgent(
        planner=planner,
        memory=RepositorySessionMemory(lambda _session_id: []),
    )

    result = agent.plan(AgentContext(
        session_id='ags_1',
        goal='分析地震响应',
        requested_task='ANALYSIS',
    ))

    assert result.intent is intent
    assert result.workflow_contract == {}


def test_analysis_runtime_tools_enforce_template_allowlist_approval_and_idempotency() -> None:
    dispatched: list[dict] = []
    tools = AnalysisRuntimeTools(
        preflight_handler=lambda payload: {
            'passed': True,
            'workflow_path': 'docs/examples/templates/openseespy_inproc_run_earthquake_baseline_template.json',
            'readiness': {'status': 'READY'},
            'config': {'kind': 'undamped_baseline'},
            'solver_version_profile': {'schemaVersion': '1.0'},
        },
        dispatch_handler=lambda payload: dispatched.append(payload.model_dump()) or {'job_id': 'job_1'},
        review_handler=lambda _payload: {
            'run_status': 'SUCCEEDED',
            'evidence_mode': 'REAL_FEM',
            'accepted': True,
            'checks': {'jobSucceeded': True},
            'message': '真实 FEM 分析及输入证据校验通过。',
        },
    )
    frozen_action = {
        'solver': 'OPENSEESPY_INPROC',
        'runMode': 'REAL_AGENT_ANALYSIS',
        'workflowConfigPath': 'docs/examples/templates/openseespy_inproc_run_earthquake_baseline_template.json',
    }

    assert tools.preflight({'solver': 'OPENSEESPY_INPROC'}).passed is True
    with pytest.raises(ToolApprovalRequired):
        tools.dispatch({'run_id': 'agr_1', 'frozen_action': frozen_action})
    with pytest.raises(ValueError, match='idempotency_key'):
        tools.dispatch({'run_id': 'agr_1', 'frozen_action': frozen_action}, approved=True)
    with pytest.raises(ValueError, match='workflowConfigPath'):
        tools.dispatch({
            'run_id': 'agr_1',
            'frozen_action': {**frozen_action, 'workflowConfigPath': 'D:/unsafe/custom.json'},
        }, approved=True, idempotency_key='agr_1:analysis:unsafe')
    with pytest.raises(ValueError, match='solver.*workflowConfigPath'):
        tools.dispatch({
            'run_id': 'agr_1',
            'frozen_action': {
                **frozen_action,
                'workflowConfigPath': 'docs/examples/templates/ansys_run_earthquake_baseline_template.json',
            },
        }, approved=True, idempotency_key='agr_1:analysis:mismatch')
    with pytest.raises(ValueError, match='未登记字段'):
        tools.dispatch({
            'run_id': 'agr_1',
            'frozen_action': {**frozen_action, 'shell': 'unsafe command'},
        }, approved=True, idempotency_key='agr_1:analysis:shell')

    result = tools.dispatch(
        {'run_id': 'agr_1', 'frozen_action': frozen_action},
        approved=True,
        idempotency_key='agr_1:analysis:hash',
    )

    assert result.job_id == 'job_1'
    assert dispatched[0]['run_id'] == 'agr_1'


def test_analysis_runtime_exposes_prepare_run_review_visualize_high_level_tools() -> None:
    tools = AnalysisRuntimeTools(
        preflight_handler=lambda _payload: {
            'passed': True,
            'workflow_path': 'docs/examples/templates/ansys_run_earthquake_baseline_template.json',
            'readiness': {},
            'config': {},
            'solver_version_profile': {},
        },
        dispatch_handler=lambda _payload: {'job_id': 'job_1'},
        review_handler=lambda _payload: {
            'run_status': 'COMPLETED_DIAGNOSTIC',
            'evidence_mode': 'DIAGNOSTIC_ONLY',
            'accepted': False,
            'checks': {},
            'message': '证据不足。',
        },
        visualize_handler=lambda _payload: {
            'figure_artifacts': [
                {'artifact_id': 'art_plot_1', 'kind': 'PLOT', 'sha256': 'b' * 64},
            ],
            'source_data_artifact': {
                'artifact_id': 'art_source_1',
                'kind': 'CSV_TIMESERIES',
                'sha256': 'a' * 64,
            },
            'contract_sha256': 'c' * 64,
        },
    )
    descriptors = tools.registry.list_tools()
    names = {descriptor.name for descriptor in descriptors}
    figure_contract = FigureContract(
        claim='展示真实分析位移。',
        source_artifacts=(ArtifactRef(
            artifact_id='art_source_1',
            kind='CSV_TIMESERIES',
            sha256='a' * 64,
        ),),
        panels=(FigurePanel(
            panel_id='a',
            evidence_role='位移时程',
            x_field='time',
            y_fields=('displacement',),
            x_label='时间（s）',
            y_label='位移（m）',
        ),),
        export_formats=('SVG',),
    )

    assert names == {'analysis.prepare', 'analysis.run', 'analysis.review', 'analysis.visualize'}
    assert all('当' in descriptor.description for descriptor in descriptors)
    assert tools.preflight({'solver': 'ANSYS'}).passed is True
    assert tools.dispatch(
        {
            'run_id': 'agr_1',
            'frozen_action': {
                'solver': 'ANSYS',
                'runMode': 'REAL_AGENT_ANALYSIS',
                'workflowConfigPath': (
                    'docs/examples/templates/ansys_run_earthquake_baseline_template.json'
                ),
            },
        },
        approved=True,
        idempotency_key='agr_1:run:hash',
    ).job_id == 'job_1'
    rendered = tools.visualize(
        {'run_id': 'agr_1', 'contract': figure_contract},
        idempotency_key='agr_1:figure:hash',
    )
    assert rendered.figure_artifacts[0].artifact_id == 'art_plot_1'


def test_agent_service_wires_controlled_visualization_handler() -> None:
    names = {
        descriptor.name
        for descriptor in AgentService()._analysis_runtime_tools().registry.list_tools()
    }

    assert names == {'analysis.prepare', 'analysis.run', 'analysis.review', 'analysis.visualize'}


def test_display_result_values_prefers_accepted_fem_review_over_surrogate_prediction(monkeypatch) -> None:
    previews = {
        'art_optimization': {
            'optimization': {
                'objective_names': ['earthquake:max_tower_base_shear'],
                'best_objectives': [48_144_942.0],
                'parameter_names': ['c', 'alpha'],
                'best_design': [7800.0, 0.8],
                'topsis': {'best_index': 0},
            },
            'review_records': [{
                'candidate': {'pareto_index': 0},
                'accepted': True,
                'verified_execution': True,
                'analysis_results': [{
                    'status': 'completed',
                    'load_case': {'name': 'earthquake'},
                    'objectives': {'max_tower_base_shear': 48_150_312.0},
                }],
            }],
        },
        'art_baseline': {'objectives': {'max_tower_base_shear': 52_299_849.0}},
        'art_overview': {'scenario': 'EARTHQUAKE'},
    }
    monkeypatch.setattr(
        'app.services.agent_service.platform_store.get_artifact',
        lambda artifact_id: SimpleNamespace(preview=previews[artifact_id]),
    )

    result = AgentService._display_result_values({
        'result': {'recommendedObjectives': {'max_tower_base_shear': 48_144_942.0}},
        'artifacts': [
            {'artifactId': 'art_optimization', 'name': 'real_optimization_summary.json'},
            {'artifactId': 'art_baseline', 'name': 'real_baseline_summary.json'},
            {'artifactId': 'art_overview', 'name': 'real_earthquake_workflow_overview.json'},
        ],
    })

    assert result['recommendedObjectives'] == {'max_tower_base_shear': 48_150_312.0}
    assert result['recommendedObjectiveEvidence']['source'] == 'ACCEPTED_FEM_REVIEW'
