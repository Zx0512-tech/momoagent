from __future__ import annotations

import json
from contextlib import nullcontext
from hashlib import sha256
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.agents.inquiry import InquiryTools
from app.agents.tools import ToolExecutionError, ToolRisk
from app.services.agent_harness import (
    HarnessApprovalDecisionInput,
    HarnessJobInput,
    bootstrap_workflow_state,
    harness_capability_registry,
    harness_step_tool_catalog,
    _status_from_workflow_cursor,
)
from app.agents.workflows import (
    WorkflowGuard,
    WorkflowToolCall,
    freeze_workflow,
    workflow_definition,
)
from app.services.agent_llm import HarnessModelTurn, HarnessToolCall
from app.services.agent_llm import OpenAICompatiblePlanner
from app.services.agent_service import AgentService
from app.core.exceptions import LLMUnavailableError


class _Repository:
    def __init__(self) -> None:
        self.session = {
            'sessionId': 'ags_harness',
            'title': 'Harness',
            'status': 'ACTIVE',
            'createdAt': '2026-08-08T00:00:00Z',
            'updatedAt': '2026-08-08T00:00:00Z',
        }
        self.runs: dict[str, dict] = {}
        self.messages: list[dict] = []
        self.tool_calls: list[dict] = []

    def get_session(self, _session_id: str) -> dict:
        return self.session

    def save_session(self, session: dict) -> None:
        self.session = session

    def add_message(self, message: dict) -> None:
        self.messages.append(message)

    def save_message(self, message: dict) -> None:
        for index, existing in enumerate(self.messages):
            if existing.get('messageId') == message.get('messageId'):
                self.messages[index] = message
                return

    def list_messages(self, _session_id: str) -> list[dict]:
        return list(self.messages)

    def find_import_by_file(self, _file_id: str) -> None:
        return None

    def find_pending_approval_run(self, _session_id: str) -> None:
        return None

    def find_pending_clarification_run(self, _session_id: str) -> None:
        return None

    def list_runs(self, session_id: str) -> list[dict]:
        return [
            run for run in self.runs.values()
            if run.get('sessionId') in {None, session_id}
        ]

    def list_all_runs(self) -> list[dict]:
        return list(self.runs.values())

    def save_run(self, run: dict) -> None:
        self.runs[run['runId']] = run

    def get_run(self, run_id: str) -> dict | None:
        return self.runs.get(run_id)

    def save_tool_call(self, tool_call: dict) -> None:
        self.tool_calls.append(tool_call)

    def list_tool_calls(self, run_id: str) -> list[dict]:
        return [item for item in self.tool_calls if item['runId'] == run_id]

    def get_tool_call(self, tool_call_id: str) -> dict | None:
        return next(
            (item for item in reversed(self.tool_calls) if item['toolCallId'] == tool_call_id),
            None,
        )

    def get_approval(self, _approval_id: str) -> None:
        return None

    def list_steps(self, _run_id: str) -> list[dict]:
        return []


def test_capability_registry_is_single_truth_and_keeps_compare_contracts_distinct() -> None:
    registry = harness_capability_registry()
    names = list(registry.list_ids())

    assert names == sorted(names)
    assert 'workflow.start' in names
    assert 'comparison.compare' in names
    assert 'result.compare' in names
    assert 'result.compare_runs' in names
    assert 'result.peak' in names
    assert 'result.delta' not in names
    assert 'result.ratio' not in names
    assert 'solver.capabilities' not in names

    workflow_start = registry.tool_schemas(['workflow.start'])[0]
    inquiry_compare = registry.tool_schemas(['result.compare'])[0]
    cross_run_compare = registry.tool_schemas(['result.compare_runs'])[0]
    engineering_compare = registry.require('comparison.compare')

    assert workflow_start['inputSchema'].get('additionalProperties') is False
    assert workflow_start['idempotencyKeySource'] == 'SERVER_DERIVED'
    intent_properties = workflow_start['inputSchema']['$defs']['EngineeringIntent']['properties']
    assert 'OpenSees' in intent_properties['solver']['description']
    assert '塔底内力' in intent_properties['responseIds']['description']
    start_properties = workflow_start['inputSchema']['properties']
    assert 'FullOptimizationStartIntent' not in workflow_start['inputSchema']['$defs']
    assert 'optimizationProfile' in intent_properties
    assert 'FULL' in str(intent_properties['optimizationProfile'])
    assert 'DAMPER_OPTIMIZATION' in str(start_properties['taskType'])
    assert 'FULL_OPTIMIZATION' not in str(start_properties['taskType'])

    assert engineering_compare.idempotency_key_source == 'SERVER_DERIVED'
    assert set(inquiry_compare['inputSchema']['properties']) == {'artifactId', 'columns'}
    assert set(inquiry_compare['inputSchema']['required']) == {'artifactId', 'columns'}
    assert 'CSV' in inquiry_compare['description']
    assert set(cross_run_compare['inputSchema']['properties']) == {'targets', 'baselineRunId', 'metricIds'}
    assert cross_run_compare['inputSchema']['properties']['targets']['maxItems'] == 8
    assert 'Project' in cross_run_compare['description']
    engineering_schema = engineering_compare.input_model.model_json_schema(by_alias=True)
    assert set(engineering_schema['properties']) == {'runId', 'jobId'}
    assert set(engineering_schema['required']) == {'runId', 'jobId'}
    assert '阻尼器' in engineering_compare.description

    for capability_id in names:
        descriptor = registry.require(capability_id).runtime_descriptor()
        assert descriptor['capabilityId'] == capability_id
        assert descriptor['sideEffect'] in {'NONE', 'ARTIFACT_WRITE', 'EXTERNAL_COMPUTE', 'STATE_MUTATION'}
        assert descriptor['approvalPolicy'] in {'NONE', 'REQUIRED'}
        assert isinstance(descriptor['prerequisites'], list)
        assert descriptor['evidencePolicy'] in {'NONE', 'REGISTERED_ARTIFACT_ONLY', 'REAL_FEM_REQUIRED'}

    expected = {'workflow.start', 'workflow.observe'}
    for task_type in (
        'ANALYSIS', 'DAMPER_COMPARISON', 'DAMPER_PARAMETER_SWEEP',
        'DAMPER_OPTIMIZATION', 'RESULT_INQUIRY',
    ):
        for step in workflow_definition(task_type).steps:
            expected.update(step.allowed_tools)
    assert set(names) == expected


def test_step_tool_catalog_exposes_only_current_frozen_step_tools() -> None:
    catalog = harness_step_tool_catalog(['workflow.complete', 'analysis.visualize'])

    assert [item['name'] for item in catalog] == ['analysis.visualize', 'workflow.complete']
    assert all(item['inputSchema'].get('additionalProperties') is False for item in catalog)


def test_persistent_loop_job_completion_stops_after_real_execution_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv('MOMO_AGENT_PERSISTENT_LOOP', 'true')
    repository = _Repository()
    run = {
        'runId': 'agr_persistent_job',
        'sessionId': 'ags_harness',
        'taskType': 'DAMPER_OPTIMIZATION',
        'runtimeMode': 'WORKFLOW_HARNESS',
        'status': 'WAITING_JOB',
        'currentStep': 'BASELINE',
        'completedSteps': ['REQUIREMENTS', 'PREFLIGHT', 'WAITING_APPROVAL'],
        'stepAttempt': 1,
        'jobId': 'job_persistent',
        **freeze_workflow(workflow_definition('DAMPER_OPTIMIZATION')),
    }
    repository.save_run(run)
    AgentService._authorize_approved_execution(
        repository,
        run,
        idempotency_key='agr_persistent_job:frozen',
    )
    AgentService._finish_execution_tool_call(
        repository,
        run,
        call_id=run.get('activeToolCallId'),
        job_id='job_persistent',
    )

    AgentService._complete_job_tool_calls(
        repository,
        run,
        job_status='SUCCEEDED',
        artifact_ids=['art_result'],
    )

    assert run['currentStep'] == 'DOE'
    assert run['completedSteps'][-1] == 'BASELINE'
    assert run['harnessLoop']['status'] == 'READY'
    assert run['harnessLoop']['wakeReason'] == 'JOB_SUCCEEDED'
    assert run['harnessLoop']['externalJobId'] == 'job_persistent'
    assert {item['stepId'] for item in repository.list_tool_calls(run['runId'])} == {'BASELINE'}


def test_persistent_loop_asks_model_for_one_current_step_tool_and_persists_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv('MOMO_AGENT_PERSISTENT_LOOP', 'true')
    repository = _Repository()
    run = {
        'runId': 'agr_persistent_turn',
        'sessionId': 'ags_harness',
        'taskType': 'DAMPER_OPTIMIZATION',
        'runtimeMode': 'WORKFLOW_HARNESS',
        'status': 'WAITING_JOB',
        'currentStep': 'DOE',
        'completedSteps': ['REQUIREMENTS', 'PREFLIGHT', 'WAITING_APPROVAL', 'BASELINE'],
        'stepAttempt': 1,
        'jobId': 'job_persistent_turn',
        'artifactIds': ['art_result'],
        'harnessLoop': {
            'version': 1,
            'status': 'READY',
            'revision': 1,
            'wakeReason': 'JOB_SUCCEEDED',
            'currentStep': 'DOE',
            'externalJobId': 'job_persistent_turn',
            'lastToolCallId': 'call_baseline',
            'updatedAt': '2026-08-11T00:00:00Z',
        },
        **freeze_workflow(workflow_definition('DAMPER_OPTIMIZATION')),
    }
    repository.save_run(run)

    class _Planner:
        def __init__(self) -> None:
            self.tools: list[dict] = []

        def run_harness_turn(self, **kwargs: object) -> HarnessModelTurn:
            tools = kwargs['tools']
            assert isinstance(tools, list)
            self.tools = list(tools)
            return HarnessModelTurn(
                content='继续执行冻结 DOE 阶段。',
                toolCalls=[HarnessToolCall(
                    toolCallId='model_call_doe',
                    name='optimization.run_doe',
                    arguments={'runId': 'agr_persistent_turn'},
                )],
            )

    planner = _Planner()
    service = AgentService()
    service.planner = planner

    progressed = service._resume_persistent_job_stage(
        repository,
        run,
        artifact_ids=['art_result'],
    )

    assert progressed is True
    assert [item['name'] for item in planner.tools] == ['optimization.run_doe']
    assert run['currentStep'] == 'SURROGATE'
    assert run['completedSteps'][-1] == 'DOE'
    assert run['harnessLoop']['status'] == 'READY'
    assert run['harnessLoop']['wakeReason'] == 'TOOL_SUCCEEDED'
    trace = repository.get_tool_call('agr_persistent_turn:loop:DOE')
    assert trace is not None
    assert trace['modelToolCallId'] == 'model_call_doe'
    assert trace['executionSource'] == 'LLM_PERSISTENT_LOOP'
    assert trace['arguments'] == trace['effectiveArguments'] == {'runId': 'agr_persistent_turn'}
    assert 'SURROGATE' in service._active_status_reply(
        'WAITING_JOB',
        current_step=run['currentStep'],
        harness_loop=run['harnessLoop'],
    )


def test_persistent_loop_stops_after_three_model_turn_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv('MOMO_AGENT_PERSISTENT_LOOP', 'true')
    repository = _Repository()
    run = {
        'runId': 'agr_persistent_failure',
        'sessionId': 'ags_harness',
        'taskType': 'DAMPER_OPTIMIZATION',
        'runtimeMode': 'WORKFLOW_HARNESS',
        'status': 'WAITING_JOB',
        'currentStep': 'DOE',
        'completedSteps': ['REQUIREMENTS', 'PREFLIGHT', 'WAITING_APPROVAL', 'BASELINE'],
        'stepAttempt': 1,
        'jobId': 'job_persistent_failure',
        'artifactIds': ['art_result'],
        'harnessLoop': {
            'version': 1,
            'status': 'READY',
            'revision': 1,
            'wakeReason': 'JOB_SUCCEEDED',
            'currentStep': 'DOE',
            'externalJobId': 'job_persistent_failure',
            'updatedAt': '2026-08-12T00:00:00Z',
        },
        **freeze_workflow(workflow_definition('DAMPER_OPTIMIZATION')),
    }
    repository.save_run(run)
    calls = 0

    def invalid_turn(**_kwargs: object) -> HarnessModelTurn:
        nonlocal calls
        calls += 1
        return HarnessModelTurn(content='没有调用当前步骤工具。', toolCalls=[])

    service = AgentService()
    service.planner = SimpleNamespace(run_harness_turn=invalid_turn)

    assert service._resume_persistent_job_stage(repository, run, artifact_ids=['art_result']) is False
    assert run['harnessLoop']['status'] == 'READY'
    assert run['harnessLoop']['modelFailureCount'] == 1
    assert service._resume_persistent_job_stage(repository, run, artifact_ids=['art_result']) is False
    assert run['harnessLoop']['modelFailureCount'] == 2
    assert service._resume_persistent_job_stage(repository, run, artifact_ids=['art_result']) is False

    assert calls == 3
    assert run['status'] == 'FAILED'
    assert run['currentStep'] == 'FAILED'
    assert run['harnessLoop']['status'] == 'FAILED'
    assert run['harnessLoop']['wakeReason'] == 'MODEL_TURN_EXHAUSTED'
    assert run['workflowGateError']['code'] == 'PERSISTENT_LOOP_RETRY_EXHAUSTED'

    assert service._resume_persistent_job_stage(repository, run, artifact_ids=['art_result']) is False
    assert calls == 3


def test_persistent_loop_runs_deterministic_review_only_after_model_selects_review_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv('MOMO_AGENT_PERSISTENT_LOOP', 'true')
    repository = _Repository()
    run = {
        'runId': 'agr_persistent_review',
        'sessionId': 'ags_harness',
        'taskType': 'ANALYSIS',
        'runtimeMode': 'WORKFLOW_HARNESS',
        'status': 'WAITING_JOB',
        'currentStep': 'EVIDENCE_REVIEW',
        'completedSteps': [
            'REQUIREMENTS', 'LOAD_PREPARATION', 'PREFLIGHT', 'WAITING_APPROVAL',
            'EXECUTION', 'RESULT_EXTRACTION',
        ],
        'stepAttempt': 1,
        'jobId': 'job_persistent_review',
        'harnessLoop': {'status': 'READY', 'revision': 2},
        **freeze_workflow(workflow_definition('ANALYSIS')),
    }
    repository.save_run(run)
    service = AgentService()
    service.planner = SimpleNamespace(
        run_harness_turn=lambda **_kwargs: HarnessModelTurn(
            toolCalls=[HarnessToolCall(
                toolCallId='model_call_review',
                name='analysis.review',
                arguments={'runId': 'agr_persistent_review'},
            )],
        ),
    )
    reviews: list[dict] = []

    def review(payload: dict, **_kwargs: object) -> SimpleNamespace:
        reviews.append(payload)
        return SimpleNamespace(
            accepted=False,
            run_status='COMPLETED_DIAGNOSTIC',
            evidence_mode='DIAGNOSTIC',
            checks={'resultCatalogPresent': False},
            message='证据不足，仅生成诊断报告。',
            extra={},
        )

    monkeypatch.setattr(service, '_agent_for', lambda _task_type: SimpleNamespace(review=review))

    progressed = service._resume_persistent_job_stage(
        repository,
        run,
        artifact_ids=['art_result'],
        job_payload={'status': 'SUCCEEDED', 'artifacts': []},
    )

    assert progressed is True
    assert len(reviews) == 1
    assert run['currentStep'] == 'REPORT'
    assert 'EVIDENCE_REVIEW' not in run['completedSteps']
    assert run['pendingReviewOutcome']['accepted'] is False
    trace = repository.get_tool_call('agr_persistent_review:loop:EVIDENCE_REVIEW')
    assert trace is not None
    assert trace['toolName'] == 'analysis.review'
    assert trace['compactResult']['runStatus'] == 'COMPLETED_DIAGNOSTIC'

    # 模拟工具轨迹已落盘、游标尚未落盘时进程退出；恢复不得重复审查。
    run['currentStep'] = 'EVIDENCE_REVIEW'
    run['harnessLoop']['status'] = 'RUNNING'
    run.pop('pendingReviewOutcome', None)
    service.planner = SimpleNamespace(
        run_harness_turn=lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError('恢复已提交工具时不得再次调用模型'),
        ),
    )
    monkeypatch.setattr(
        service,
        '_agent_for',
        lambda _task_type: (_ for _ in ()).throw(AssertionError('不得重复证据审查')),
    )

    recovered = service._resume_persistent_job_stage(
        repository,
        run,
        artifact_ids=['art_result'],
        job_payload={'status': 'SUCCEEDED'},
    )

    assert recovered is True
    assert run['currentStep'] == 'REPORT'
    assert run['pendingReviewOutcome']['accepted'] is False
    assert run['harnessLoop']['wakeReason'] == 'RECOVERED_COMMITTED_TOOL'


def test_comparison_stage_uses_its_own_job_contract() -> None:
    snapshot = freeze_workflow(workflow_definition('DAMPER_COMPARISON'))['workflowSnapshot']
    comparison_step = next(item for item in snapshot['steps'] if item['stepId'] == 'COMPARISON')
    arguments = {'runId': 'agr_compare', 'jobId': 'job_compare'}

    authorization = WorkflowGuard().authorize(
        workflow_snapshot=snapshot,
        current_step='COMPARISON',
        completed_steps=(
            'REQUIREMENTS',
            'CALIBRATION',
            'PREFLIGHT',
            'WAITING_APPROVAL',
            'EXECUTION',
        ),
        tool_call=WorkflowToolCall(
            name='comparison.compare',
            arguments=arguments,
            risk=ToolRisk.ARTIFACT_WRITE,
            idempotencyKey='agr_compare:job:COMPARISON',
        ),
    )
    validated = HarnessJobInput.model_validate(arguments)

    assert comparison_step['allowedTools'] == ['comparison.compare']
    assert authorization.allowed is True
    assert validated.model_dump(by_alias=True, mode='json') == arguments


def test_approval_tool_rejects_lowercase_decision_instead_of_normalizing_it() -> None:
    with pytest.raises(ValidationError):
        HarnessApprovalDecisionInput.model_validate({'decision': 'approve'})


def test_bootstrap_state_only_allows_start_and_exposes_result_context() -> None:
    state = bootstrap_workflow_state(inquirable_run_id='agr_result')

    assert state['currentStep'] == 'ROUTING'
    assert state['allowedTools'] == ['workflow.start']
    assert state['inquirableRunId'] == 'agr_result'


def test_read_only_observation_can_be_authorized_without_changing_frozen_step_tools() -> None:
    snapshot = freeze_workflow(workflow_definition('ANALYSIS'))['workflowSnapshot']

    authorization = WorkflowGuard().authorize(
        workflow_snapshot=snapshot,
        current_step='EXECUTION',
        completed_steps=('REQUIREMENTS', 'LOAD_PREPARATION', 'PREFLIGHT', 'WAITING_APPROVAL'),
        step_attempt=99,
        observational_tools=('workflow.observe',),
        tool_call=WorkflowToolCall(
            name='workflow.observe',
            arguments={},
            risk=ToolRisk.READ_ONLY,
        ),
    )

    execution = next(item for item in snapshot['steps'] if item['stepId'] == 'EXECUTION')
    assert authorization.tool_name == 'workflow.observe'
    assert execution['allowedTools'] == ['analysis.run']
    with pytest.raises(ToolExecutionError) as error:
        WorkflowGuard().authorize(
            workflow_snapshot=snapshot,
            current_step='EXECUTION',
            completed_steps=('REQUIREMENTS', 'LOAD_PREPARATION', 'PREFLIGHT', 'WAITING_APPROVAL'),
            tool_call=WorkflowToolCall(
                name='workflow.observe',
                arguments={},
                risk=ToolRisk.READ_ONLY,
            ),
        )
    assert error.value.code == 'WORKFLOW_STEP_VIOLATION'


def test_harness_payload_preserves_native_tool_message_fields() -> None:
    payload = OpenAICompatiblePlanner(base_url='http://x/v1', model='m')._harness_payload(
        messages=[
            {
                'role': 'assistant',
                'content': None,
                'tool_calls': [{'id': 'call_1', 'type': 'function'}],
            },
            {'role': 'tool', 'tool_call_id': 'call_1', 'content': '{"ok":true}'},
        ],
        user_content='继续',
        workflow_state={'currentStep': 'QUERY'},
        tools=harness_step_tool_catalog(['result.peak']),
    )

    history = payload['messages'][1:3]
    assert history[0]['tool_calls'][0]['id'] == 'call_1'
    assert history[1]['tool_call_id'] == 'call_1'
    assert payload['parallel_tool_calls'] is False


def test_history_budget_never_emits_orphan_tool_message() -> None:
    # assistant 的参数比 tool 结果大得多时，按单条裁剪会切出孤立 tool 消息。
    messages: list[dict] = []
    for index in range(12):
        messages.append({
            'role': 'USER',
            'content': f'u{index}',
            'harnessContent': json.dumps({'workflowState': {}, 'userContent': 'u' * 100}),
        })
        messages.append({
            'role': 'ASSISTANT',
            'content': None,
            'tool_calls': [{
                'id': f'call_{index}',
                'type': 'function',
                'function': {
                    'name': 'optimization.run_doe',
                    'arguments': json.dumps({'design': ['p' * 40] * 70}),
                },
            }],
        })
        messages.append({
            'role': 'TOOL',
            'tool_call_id': f'call_{index}',
            'name': 'optimization.run_doe',
            'content': json.dumps({'ok': True}),
        })

    kept = AgentService._bounded_harness_history(messages)

    assert len(kept) < len(messages)
    declared: set[str] = set()
    for message in kept:
        for call in message.get('tool_calls') or []:
            declared.add(call['id'])
        if message['role'] == 'tool':
            assert message['tool_call_id'] in declared


def test_history_projection_falls_back_to_original_user_text() -> None:
    kept = AgentService._bounded_harness_history([
        {'role': 'USER', 'content': '帮我做一次分析'},
        {'role': 'ASSISTANT', 'content': '好的'},
    ])

    assert [message['content'] for message in kept] == ['帮我做一次分析', '好的']


def test_history_drops_leading_orphan_tool_message() -> None:
    kept = AgentService._bounded_harness_history([
        {'role': 'TOOL', 'tool_call_id': 'orphan', 'name': 'analysis.run', 'content': '{"ok":true}'},
        {'role': 'USER', 'content': '继续', 'harnessContent': '{"userContent":"继续"}'},
    ])

    assert all(message['role'] != 'tool' for message in kept)


def test_stage_tool_catalog_is_cached_and_mutation_safe() -> None:
    first = harness_step_tool_catalog(['workflow.observe', 'result.peak'])
    second = harness_step_tool_catalog(['result.peak', 'workflow.observe'])
    assert first == second
    # 出口深拷贝：调用方原地改写不得污染阶段缓存。
    first[0]['inputSchema']['properties']['hacked'] = True
    again = harness_step_tool_catalog(['workflow.observe', 'result.peak'])
    assert all('hacked' not in item['inputSchema'].get('properties', {}) for item in again)
    assert [item['name'] for item in again] == ['result.peak', 'workflow.observe']


def _compression_history_items(count: int = 6) -> list[dict]:
    items: list[dict] = []
    for index in range(count):
        items.append({
            'messageId': f'msg_u{index}',
            'role': 'USER',
            'content': f'第{index}轮问题：' + 'x' * 220,
        })
        items.append({
            'messageId': f'msg_a{index}',
            'role': 'ASSISTANT',
            'content': f'第{index}轮回答：塔底剪力峰值 {index}.5 kN。' + 'y' * 220,
        })
    return items


def test_context_compression_folds_old_history_with_query_aware_summary(monkeypatch) -> None:
    monkeypatch.setenv('MOMO_AGENT_CONTEXT_WINDOW_TOKENS', '200')
    service = AgentService()
    repository = _Repository()
    captured: dict[str, object] = {}

    def compress_context(*, query, workflow_state, context, target_chars):
        captured['query'] = query
        captured['workflowState'] = workflow_state
        captured['context'] = context
        captured['targetChars'] = target_chars
        return '摘要：历史峰值最大 5.5 kN（runId=agr_1）。'

    service.planner = SimpleNamespace(compress_context=compress_context)
    items = _compression_history_items()

    kept = service._contextual_harness_history(
        repository,
        'ags_harness',
        items,
        workflow_state={'taskType': 'ANALYSIS', 'currentStep': 'EVIDENCE_REVIEW'},
        query='给出塔底剪力峰值',
    )

    # 压缩提示必须携带当前查询意图与待折叠内容。
    assert captured['query'] == '给出塔底剪力峰值'
    assert '塔底剪力峰值 0.5' in str(captured['context'])
    # 首条消息是定向摘要，其后是逐字保留的尾部。
    head = json.loads(kept[0]['content'])
    assert '5.5 kN' in head['contextCompression']['summary']
    assert all('contextCompression' not in str(message.get('content')) for message in kept[1:])
    # 覆盖位置持久化到会话，供下一轮复用。
    state = repository.session['harnessCompression']
    assert state['coveredThroughMessageId'].startswith('msg_')
    assert state['coveredMessageCount'] > 0


def test_context_compression_reuses_persisted_summary_without_new_llm_call() -> None:
    service = AgentService()
    repository = _Repository()
    items = _compression_history_items(count=2)
    repository.session['harnessCompression'] = {
        'version': 1,
        'summary': '既有摘要：runId=agr_1 已完成分析。',
        'coveredThroughMessageId': 'msg_a0',
        'coveredMessageCount': 2,
        'sourceChars': 500,
        'updatedAt': '2026-08-13T00:00:00Z',
    }

    def compress_context(**_kwargs):
        raise AssertionError('未超预算不应重新压缩')

    service.planner = SimpleNamespace(compress_context=compress_context)

    kept = service._contextual_harness_history(
        repository,
        'ags_harness',
        items,
        workflow_state={'taskType': 'ANALYSIS'},
        query='继续',
    )

    head = json.loads(kept[0]['content'])
    assert head['contextCompression']['summary'] == '既有摘要：runId=agr_1 已完成分析。'
    # 已覆盖的消息不再出现，未覆盖的尾部逐字保留。
    contents = [message.get('content') for message in kept[1:]]
    assert any('第1轮问题' in str(content) for content in contents)
    assert all('第0轮' not in str(content) for content in contents)


def test_context_compression_llm_failure_falls_back_to_truncation(monkeypatch) -> None:
    monkeypatch.setenv('MOMO_AGENT_CONTEXT_WINDOW_TOKENS', '200')
    service = AgentService()
    repository = _Repository()

    def compress_context(**_kwargs):
        raise LLMUnavailableError('CONTEXT_COMPRESSION', 'LLM_CONNECTION_FAILED')

    service.planner = SimpleNamespace(compress_context=compress_context)
    items = _compression_history_items()

    kept = service._contextual_harness_history(
        repository,
        'ags_harness',
        items,
        workflow_state=None,
        query='给出峰值',
    )

    assert kept == AgentService._bounded_harness_history(items)
    assert 'harnessCompression' not in repository.session


def test_planner_without_compress_context_keeps_legacy_truncation() -> None:
    service = AgentService()
    repository = _Repository()
    service.planner = SimpleNamespace()
    items = _compression_history_items(count=2)

    kept = service._contextual_harness_history(
        repository,
        'ags_harness',
        items,
        workflow_state=None,
        query='继续',
    )

    assert kept == AgentService._bounded_harness_history(items)


def test_inquiry_registry_returns_structured_handler_failure_and_accepts_camel_case() -> None:
    from app.services.result_inquiry import ResultInquiryError

    def broken_columns(_artifact_id: str) -> list[str]:
        raise ResultInquiryError('制品已经过期')

    tools = InquiryTools(service=SimpleNamespace(columns=broken_columns))
    with pytest.raises(ToolExecutionError) as error:
        tools.call('result.columns', {'artifactId': 'art_csv'})

    assert error.value.code == 'TOOL_HANDLER_FAILED'
    assert '制品已经过期' in error.value.message


def test_harness_text_response_creates_conversation_without_legacy_classifier(monkeypatch) -> None:
    service = AgentService()
    repository = _Repository()
    monkeypatch.setenv('MOMO_AGENT_RUNTIME', 'WORKFLOW_HARNESS')
    monkeypatch.setattr(service, 'repository', lambda: repository)
    service.planner = SimpleNamespace(
        run_harness_turn=lambda **_kwargs: HarnessModelTurn(
            content='你好，我可以协助桥梁分析、对比、优化和结果追问。',
            cachedTokens=42,
        ),
        classify_task=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError('Harness 不应调用旧 classify_task')
        ),
    )

    run = service.create_message('ags_harness', '你好', None)

    assert run['taskType'] == 'CONVERSATION'
    assert run['runtimeMode'] == 'WORKFLOW_HARNESS'
    assert run['resultSummary']['cachedTokens'] == 42
    assert repository.messages[-1]['content'].startswith('你好')
    assert repository.messages[0]['harnessContent'].startswith('{"runtimeContext"')


def test_message_during_active_job_observes_same_run_instead_of_starting_another(monkeypatch) -> None:
    service = AgentService()
    repository = _Repository()
    monkeypatch.setenv('MOMO_AGENT_RUNTIME', 'WORKFLOW_HARNESS')
    monkeypatch.setattr(service, 'repository', lambda: repository)
    active_run = {
        'runId': 'agr_active',
        'sessionId': 'ags_harness',
        'goal': '用 OpenSees 计算地震响应',
        'taskType': 'ANALYSIS',
        'status': 'WAITING_JOB',
        'currentStage': 'WAITING_JOB',
        'runtimeMode': 'WORKFLOW_HARNESS',
        **freeze_workflow(workflow_definition('ANALYSIS')),
        'currentStep': 'EXECUTION',
        'completedSteps': ['REQUIREMENTS', 'LOAD_PREPARATION', 'PREFLIGHT', 'WAITING_APPROVAL'],
        'stepAttempt': 1,
        'jobId': 'job_active',
        'artifactIds': [],
        'createdAt': '2026-08-08T00:00:00Z',
        'updatedAt': '2026-08-08T00:01:00Z',
    }
    repository.save_run(active_run)
    monkeypatch.setattr(service, '_refresh_agent_run', lambda _repository, run: run)
    turns: list[dict] = []

    def run_harness_turn(**kwargs):
        turns.append(kwargs)
        if len(turns) == 1:
            assert kwargs['workflow_state']['runId'] == 'agr_active'
            assert kwargs['workflow_state']['allowedTools'] == ['workflow.observe']
            return HarnessModelTurn(
                finishReason='tool_calls',
                toolCalls=[HarnessToolCall(
                    toolCallId='call_observe',
                    name='workflow.observe',
                    arguments={},
                )],
            )
        tool_messages = [message for message in kwargs['messages'] if message.get('role') == 'tool']
        assert tool_messages
        observation = json.loads(tool_messages[-1]['content'])['result']
        assert observation['runId'] == 'agr_active'
        assert observation['runStatus'] == 'WAITING_JOB'
        return HarnessModelTurn(content='任务已经在执行队列中，当前仍在真实求解，尚未生成最终报告。')

    service.planner = SimpleNamespace(run_harness_turn=run_harness_turn)

    result = service.create_message('ags_harness', '计算完成了吗？给我展示结果', None)

    assert result['runId'] == 'agr_active'
    assert set(repository.runs) == {'agr_active'}
    assert len(turns) == 2
    assert repository.tool_calls[-1]['toolName'] == 'workflow.observe'
    assert repository.tool_calls[-1]['arguments'] == {}
    assert repository.tool_calls[-1]['effectiveArguments'] == {}
    assert repository.messages[-1]['runId'] == 'agr_active'
    assert '当前仍在真实求解' in repository.messages[-1]['content']


def test_message_refreshing_active_job_to_success_routes_to_result_inquiry(monkeypatch) -> None:
    service = AgentService()
    repository = _Repository()
    monkeypatch.setenv('MOMO_AGENT_RUNTIME', 'WORKFLOW_HARNESS')
    monkeypatch.setattr(service, 'repository', lambda: repository)
    active_run = {
        'runId': 'agr_finished_on_refresh',
        'sessionId': 'ags_harness',
        'goal': '用 OpenSees 计算地震响应',
        'taskType': 'ANALYSIS',
        'status': 'WAITING_JOB',
        'currentStage': 'WAITING_JOB',
        'runtimeMode': 'WORKFLOW_HARNESS',
        **freeze_workflow(workflow_definition('ANALYSIS')),
        'currentStep': 'EXECUTION',
        'completedSteps': ['REQUIREMENTS', 'LOAD_PREPARATION', 'PREFLIGHT', 'WAITING_APPROVAL'],
        'stepAttempt': 1,
        'jobId': 'job_finished',
        'artifactIds': [],
        'createdAt': '2026-08-08T00:00:00Z',
        'updatedAt': '2026-08-08T00:01:00Z',
    }
    repository.save_run(active_run)

    def refresh(_repository, run):
        run.update({
            'status': 'SUCCEEDED',
            'currentStage': 'COMPLETED',
            'currentStep': 'COMPLETED',
            'reportArtifactId': 'art_report',
            'artifactIds': ['art_report', 'art_timeseries'],
            'updatedAt': '2026-08-08T00:02:00Z',
        })
        repository.save_run(run)
        return run

    captured: dict[str, str | None] = {}

    def create_native(_repository, _session, source_run, _content, _now, **_kwargs):
        captured['runId'] = source_run.get('runId')
        return source_run

    monkeypatch.setattr(service, '_refresh_agent_run', refresh)
    monkeypatch.setattr(service, '_create_native_inquiry_run', create_native)
    monkeypatch.setattr(
        service,
        '_dispatch_harness_message',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError('刷新完成的结果不得重新进入 bootstrap')
        ),
    )

    result = service.create_message('ags_harness', '计算完成了吗？给我展示结果', None)

    assert captured['runId'] == 'agr_finished_on_refresh'
    assert result['runId'] == 'agr_finished_on_refresh'


def test_report_failure_with_persisted_report_recovers_original_run(monkeypatch) -> None:
    service = AgentService()
    repository = _Repository()
    run = {
        'runId': 'agr_orphan_report',
        'sessionId': 'ags_harness',
        'goal': '执行一次无阻尼分析',
        'taskType': 'ANALYSIS',
        'status': 'FAILED',
        'currentStage': 'FAILED',
        'runtimeMode': 'WORKFLOW_HARNESS',
        **freeze_workflow(workflow_definition('ANALYSIS')),
        'currentStep': 'FAILED',
        'completedSteps': [
            'REQUIREMENTS', 'LOAD_PREPARATION', 'PREFLIGHT',
            'WAITING_APPROVAL', 'EXECUTION', 'RESULT_EXTRACTION',
        ],
        'stepAttempt': 1,
        'jobId': 'job_orphan_report',
        'artifactIds': ['art_input'],
        'workflowGateError': {
            'code': 'REPORT_GENERATION_ERROR',
            'message': '结果验收或报告生成失败，运行已安全终止。',
            'details': {'stage': 'REPORT_GENERATION', 'exceptionType': 'HTTPException'},
        },
        'createdAt': '2026-08-11T00:00:00Z',
        'updatedAt': '2026-08-11T00:01:00Z',
    }
    repository.save_run(run)
    report_artifact = SimpleNamespace(
        artifact=SimpleNamespace(
            artifact_id='art_orphan_report',
            name='agr_orphan_report_analysis_evidence_report.json',
            run_id='agr_orphan_report',
        ),
        preview={
            'agentRunId': 'agr_orphan_report',
            'taskType': 'ANALYSIS',
            'jobId': 'job_orphan_report',
            'jobStatus': 'SUCCEEDED',
            'evidenceMode': 'REAL_FEM',
            'isFinalResult': True,
            'conclusion': '真实 FEM 分析及输入证据校验通过。',
            'checks': {'jobSucceeded': True, 'outputManifest': True},
            'narrativeSummary': '计算完成，报告可以查询。',
            'outputManifestArtifactId': 'art_manifest',
        },
    )
    job = SimpleNamespace(
        status='SUCCEEDED',
        model_dump=lambda **_kwargs: {
            'jobId': 'job_orphan_report',
            'status': 'SUCCEEDED',
            'result': {'outputManifestArtifactId': 'art_manifest'},
            'artifacts': [{'artifactId': 'art_timeseries'}],
        },
    )
    store = SimpleNamespace(
        state_transaction=lambda: nullcontext(),
        refresh=lambda: None,
        find_artifact_for_run=lambda _run_id, _name: report_artifact,
        get_artifact=lambda artifact_id: (
            report_artifact
            if artifact_id == 'art_orphan_report'
            else SimpleNamespace(artifact=SimpleNamespace(artifact_id=artifact_id, run_id='agr_orphan_report'))
        ),
        get_job=lambda _job_id: job,
    )
    monkeypatch.setattr('app.services.agent_service.platform_store', store)

    recovered = service._refresh_agent_run(repository, run)

    assert recovered['status'] == 'SUCCEEDED'
    assert recovered['currentStep'] == 'COMPLETED'
    assert recovered['reportArtifactId'] == 'art_orphan_report'
    assert recovered['artifactIds'] == ['art_input', 'art_timeseries', 'art_orphan_report']
    assert recovered['resultSummary']['narrativeSummary'] == '计算完成，报告可以查询。'
    assert 'workflowGateError' not in recovered


def test_message_after_report_failure_refreshes_original_run_before_bootstrap(monkeypatch) -> None:
    service = AgentService()
    repository = _Repository()
    monkeypatch.setenv('MOMO_AGENT_RUNTIME', 'WORKFLOW_HARNESS')
    monkeypatch.setattr(service, 'repository', lambda: repository)
    failed_run = {
        'runId': 'agr_failed_report_followup',
        'sessionId': 'ags_harness',
        'goal': '用 OpenSees 计算地震响应',
        'taskType': 'ANALYSIS',
        'status': 'FAILED',
        'currentStage': 'FAILED',
        'runtimeMode': 'WORKFLOW_HARNESS',
        **freeze_workflow(workflow_definition('ANALYSIS')),
        'currentStep': 'FAILED',
        'completedSteps': ['REQUIREMENTS', 'LOAD_PREPARATION', 'PREFLIGHT', 'WAITING_APPROVAL'],
        'stepAttempt': 1,
        'jobId': 'job_failed_report_followup',
        'artifactIds': [],
        'workflowGateError': {
            'code': 'REPORT_GENERATION_ERROR',
            'details': {'stage': 'REPORT_GENERATION', 'exceptionType': 'HTTPException'},
        },
        'createdAt': '2026-08-11T00:00:00Z',
        'updatedAt': '2026-08-11T00:01:00Z',
    }
    repository.save_run(failed_run)

    def refresh(_repository, current_run):
        current_run.update({
            'status': 'SUCCEEDED',
            'currentStage': 'SUCCEEDED',
            'currentStep': 'COMPLETED',
            'reportArtifactId': 'art_recovered_report',
            'artifactIds': ['art_recovered_report', 'art_timeseries'],
        })
        repository.save_run(current_run)
        return current_run

    captured: dict[str, str | None] = {}

    def create_native(_repository, _session, source_run, _content, _now, **_kwargs):
        captured['runId'] = source_run.get('runId')
        return source_run

    monkeypatch.setattr(service, '_refresh_agent_run', refresh)
    monkeypatch.setattr(service, '_create_native_inquiry_run', create_native)
    monkeypatch.setattr(
        service,
        '_dispatch_harness_message',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError('恢复报告后的结果不得重新进入 bootstrap')
        ),
    )

    result = service.create_message('ags_harness', '计算完成了，结果呢？', None)

    assert captured['runId'] == 'agr_failed_report_followup'
    assert result['runId'] == 'agr_failed_report_followup'
    assert set(repository.runs) == {'agr_failed_report_followup'}


def test_harness_start_freezes_workflow_and_records_tool_call(monkeypatch) -> None:
    service = AgentService()
    repository = _Repository()
    monkeypatch.setenv('MOMO_AGENT_RUNTIME', 'WORKFLOW_HARNESS')
    monkeypatch.setattr(service, 'repository', lambda: repository)
    service.planner = SimpleNamespace(
        run_harness_turn=lambda **_kwargs: HarnessModelTurn(
            finishReason='tool_calls',
            cachedTokens=7,
            toolCalls=[HarnessToolCall(
                toolCallId='call_start',
                name='workflow.start',
                arguments={
                    'taskType': 'ANALYSIS',
                    'engineeringIntent': {
                        'taskType': 'ANALYSIS',
                        'solver': 'OPENSEESPY_INPROC',
                        'damperType': None,
                        'loadKind': 'EARTHQUAKE',
                        'responseIds': ['max_tower_base_shear'],
                        'missingFields': [],
                        'summary': '执行一次无阻尼地震分析。',
                    },
                },
            )],
        ),
    )

    def create_engineering(_repository, _session, content, now, **_kwargs):
        run = {
            'runId': 'agr_analysis',
            'sessionId': 'ags_harness',
            'goal': content,
            'taskType': 'ANALYSIS',
            'status': 'WAITING_APPROVAL',
            'currentStage': 'SOLVER_APPROVAL',
            'createdAt': now,
            'updatedAt': now,
        }
        repository.save_run(run)
        return run

    monkeypatch.setattr(service, '_create_engineering_run', create_engineering)

    run = service.create_message('ags_harness', '做一次无阻尼地震分析', None)

    stored = repository.get_run('agr_analysis')
    assert run['runtimeMode'] == 'WORKFLOW_HARNESS'
    assert stored is not None
    assert stored['workflowId'] == 'analysis'
    assert stored['workflowVersion'] == '1.1.0'
    assert stored['workflowSha256']
    assert stored['workflowSnapshot']['limits']['realSolveCount'] == 1
    assert stored['currentStep'] == 'WAITING_APPROVAL'
    assert repository.tool_calls[0]['toolName'] == 'workflow.start'
    assert repository.tool_calls[0]['cachedTokens'] == 7
    native_call = next(item for item in repository.messages if item.get('messageType') == 'HARNESS_TOOL_CALL')
    native_result = next(item for item in repository.messages if item.get('messageType') == 'HARNESS_TOOL_RESULT')
    assert native_call['tool_calls'][0]['id'] == 'call_start'
    assert native_result['tool_call_id'] == 'call_start'
    assert native_result['role'] == 'TOOL'

def test_harness_rejects_solver_rewrite_and_repairs_to_opensees_optimization(monkeypatch) -> None:
    service = AgentService()
    repository = _Repository()
    monkeypatch.setenv('MOMO_AGENT_RUNTIME', 'WORKFLOW_HARNESS')
    monkeypatch.setattr(service, 'repository', lambda: repository)
    calls: list[dict] = []

    def run_harness_turn(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            arguments = {
                'taskType': 'DAMPER_OPTIMIZATION',
                'engineeringIntent': {
                    'taskType': 'DAMPER_OPTIMIZATION',
                    'solver': 'ANSYS',
                    'damperType': 'VISCOUS',
                    'loadKind': 'EARTHQUAKE',
                    'selectedLayoutId': 'TWO_PER_TOWER',
                    'responseIds': [
                        'max_girder_end_displacement',
                        'max_tower_base_shear',
                        'max_tower_base_moment',
                    ],
                    'optimizationProfile': 'FULL',
                    'missingFields': [],
                    'summary': '使用 ANSYS 执行完整优化。',
                },
            }
        else:
            arguments = {
                'taskType': 'DAMPER_OPTIMIZATION',
                'engineeringIntent': {
                    'taskType': 'DAMPER_OPTIMIZATION',
                    'solver': 'OPENSEESPY_INPROC',
                    'damperType': 'VISCOUS',
                    'loadKind': 'EARTHQUAKE',
                    'selectedLayoutId': 'TWO_PER_TOWER',
                    'responseIds': [
                        'max_girder_end_displacement',
                        'max_tower_base_shear',
                        'max_tower_base_moment',
                    ],
                    'missingFields': [],
                    'summary': '使用 OpenSeesPy 执行黏滞阻尼器 baseline-first 真实优化。',
                },
            }
        return HarnessModelTurn(
            finishReason='tool_calls',
            toolCalls=[HarnessToolCall(
                toolCallId=f'call_solver_repair_{len(calls)}',
                name='workflow.start',
                arguments=arguments,
            )],
        )

    service.planner = SimpleNamespace(run_harness_turn=run_harness_turn)
    captured: dict[str, object] = {}

    def create_engineering(_repository, _session, content, now, **kwargs):
        captured['intent'] = kwargs['intent_override']
        run = {
            'runId': 'agr_opensees_optimization',
            'sessionId': 'ags_harness',
            'goal': content,
            'taskType': 'DAMPER_OPTIMIZATION',
            'status': 'WAITING_APPROVAL',
            'currentStage': 'SOLVER_APPROVAL',
            'createdAt': now,
            'updatedAt': now,
        }
        repository.save_run(run)
        return run

    monkeypatch.setattr(service, '_create_engineering_run', create_engineering)

    run = service.create_message(
        'ags_harness',
        '使用 OpenSeesPy 对已登记地震工况执行黏滞阻尼器 baseline-first 真实优化。',
        None,
    )

    assert run['runId'] == 'agr_opensees_optimization'
    assert len(calls) == 2
    correction = json.loads(calls[1]['messages'][-1]['content'])
    assert correction['error']['code'] == 'INPUT_VALIDATION_ERROR'
    assert '不得改写为 ANSYS' in correction['error']['message']
    assert captured['intent'].solver == 'OPENSEESPY_INPROC'

def test_pending_clarification_uses_native_harness_intent(monkeypatch) -> None:
    service = AgentService()
    repository = _Repository()
    monkeypatch.setenv('MOMO_AGENT_RUNTIME', 'WORKFLOW_HARNESS')
    monkeypatch.setattr(service, 'repository', lambda: repository)
    pending = {
        'runId': 'agr_clarification',
        'sessionId': 'ags_harness',
        'goal': '做一次分析',
        'taskType': 'ANALYSIS',
        'status': 'NEEDS_CLARIFICATION',
        'runtimeMode': 'WORKFLOW_HARNESS',
        **freeze_workflow(workflow_definition('ANALYSIS')),
        'currentStep': 'REQUIREMENTS',
        'completedSteps': [],
    }
    repository.save_run(pending)
    repository.find_pending_clarification_run = lambda _session_id: pending
    service.planner = SimpleNamespace(
        run_harness_turn=lambda **_kwargs: HarnessModelTurn(
            finishReason='tool_calls',
            toolCalls=[HarnessToolCall(
                toolCallId='call_clarification',
                name='workflow.start',
                arguments={
                    'taskType': 'ANALYSIS',
                    'engineeringIntent': {
                        'taskType': 'ANALYSIS',
                        'solver': 'OPENSEESPY_INPROC',
                        'loadKind': 'EARTHQUAKE',
                        'responseIds': [],
                        'missingFields': ['responseIds'],
                        'summary': '还需要确认响应指标。',
                    },
                },
            )],
        ),
    )
    captured: dict[str, object] = {}

    def resolve(*_args, **kwargs):
        captured['intent'] = kwargs['intent_override']
        captured['mode'] = kwargs['planner_mode_override']
        return pending

    monkeypatch.setattr(service, '_resolve_clarification', resolve)
    result = service.create_message('ags_harness', '补充响应指标', None)

    assert result['runId'] == 'agr_clarification'
    assert captured['mode'] == 'LLM_TOOL_CALL'
    assert captured['intent'].missing_fields == ['responseIds']


def test_pending_clarification_allows_model_to_repair_typed_intent(monkeypatch) -> None:
    service = AgentService()
    repository = _Repository()
    monkeypatch.setenv('MOMO_AGENT_RUNTIME', 'WORKFLOW_HARNESS')
    monkeypatch.setattr(service, 'repository', lambda: repository)
    pending = {
        'runId': 'agr_clarification_repair',
        'sessionId': 'ags_harness',
        'goal': '使用 OpenSees 做地震分析',
        'taskType': 'ANALYSIS',
        'status': 'NEEDS_CLARIFICATION',
        'runtimeMode': 'WORKFLOW_HARNESS',
        **freeze_workflow(workflow_definition('ANALYSIS')),
        'currentStep': 'REQUIREMENTS',
        'completedSteps': [],
    }
    repository.save_run(pending)
    repository.find_pending_clarification_run = lambda _session_id: pending
    calls: list[dict] = []

    def run_harness_turn(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            arguments = {'taskType': 'ANALYSIS'}
        else:
            arguments = {
                'taskType': 'ANALYSIS',
                'engineeringIntent': {
                    'taskType': 'ANALYSIS',
                    'solver': 'OPENSEESPY_INPROC',
                    'loadKind': 'EARTHQUAKE',
                    'responseIds': [
                        'max_girder_end_displacement',
                        'max_tower_base_shear',
                        'max_tower_base_moment',
                    ],
                    'missingFields': [],
                    'summary': '使用 OpenSees 计算梁端位移和塔底内力。',
                },
            }
        return HarnessModelTurn(
            finishReason='tool_calls',
            toolCalls=[HarnessToolCall(
                toolCallId=f'call_clarification_{len(calls)}',
                name='workflow.start',
                arguments=arguments,
            )],
        )

    service.planner = SimpleNamespace(run_harness_turn=run_harness_turn)
    captured: dict[str, object] = {}

    def resolve(*_args, **kwargs):
        captured['intent'] = kwargs['intent_override']
        return pending

    monkeypatch.setattr(service, '_resolve_clarification', resolve)

    result = service.create_message('ags_harness', '输出梁端位移和塔底内力', None)

    assert result['runId'] == 'agr_clarification_repair'
    assert len(calls) == 2
    assert calls[1]['messages'][-2]['role'] == 'assistant'
    correction = json.loads(calls[1]['messages'][-1]['content'])
    assert correction['error']['code'] == 'INPUT_VALIDATION_ERROR'
    assert captured['intent'].solver == 'OPENSEESPY_INPROC'
    assert captured['intent'].response_ids == [
        'max_girder_end_displacement',
        'max_tower_base_shear',
        'max_tower_base_moment',
    ]


def test_legacy_pending_clarification_bypasses_harness_without_snapshot(monkeypatch) -> None:
    service = AgentService()
    repository = _Repository()
    monkeypatch.setenv('MOMO_AGENT_RUNTIME', 'WORKFLOW_HARNESS')
    monkeypatch.setattr(service, 'repository', lambda: repository)
    pending = {
        'runId': 'agr_legacy_clarification',
        'sessionId': 'ags_harness',
        'goal': '导入荷载',
        'taskType': 'LOAD_IMPORT',
        'status': 'NEEDS_CLARIFICATION',
    }
    repository.save_run(pending)
    repository.find_pending_clarification_run = lambda _session_id: pending
    called = {'value': False}

    def resolve(*_args, **_kwargs):
        called['value'] = True
        return pending

    monkeypatch.setattr(service, '_resolve_clarification', resolve)

    result = service.create_message('ags_harness', '补充荷载信息', None)

    assert called['value'] is True
    assert result['runId'] == 'agr_legacy_clarification'


def test_legacy_pending_approval_bypasses_harness_without_snapshot(monkeypatch) -> None:
    service = AgentService()
    repository = _Repository()
    monkeypatch.setenv('MOMO_AGENT_RUNTIME', 'WORKFLOW_HARNESS')
    monkeypatch.setattr(service, 'repository', lambda: repository)
    pending = {
        'runId': 'agr_legacy_approval',
        'sessionId': 'ags_harness',
        'goal': '标准化荷载',
        'taskType': 'LOAD_IMPORT',
        'status': 'WAITING_APPROVAL',
        'pendingApprovalId': 'approval_legacy',
    }
    repository.save_run(pending)
    repository.find_pending_approval_run = lambda _session_id: pending
    repository.get_approval = lambda _approval_id: {'approvalId': 'approval_legacy', 'status': 'PENDING'}
    called = {'value': False}

    def resolve(*_args, **_kwargs):
        called['value'] = True
        return pending

    monkeypatch.setattr(service, '_resolve_approval_reply', resolve)

    result = service.create_message('ags_harness', '批准', None)

    assert called['value'] is True
    assert result['runId'] == 'agr_legacy_approval'


def test_harness_rejects_tool_that_skips_bootstrap_step(monkeypatch) -> None:
    service = AgentService()
    repository = _Repository()
    monkeypatch.setenv('MOMO_AGENT_RUNTIME', 'WORKFLOW_HARNESS')
    monkeypatch.setattr(service, 'repository', lambda: repository)
    service.planner = SimpleNamespace(
        run_harness_turn=lambda **_kwargs: HarnessModelTurn(
            finishReason='tool_calls',
            toolCalls=[HarnessToolCall(
                toolCallId='call_illegal',
                name='solver.execute',
                arguments={},
            )],
        ),
    )

    run = service.create_message('ags_harness', '直接求解', None)

    assert run['status'] == 'FAILED'
    assert run['resultSummary']['code'] == 'WORKFLOW_STEP_VIOLATION'
    assert run['resultSummary']['details']['allowedTools'] == ['workflow.start']
    assert repository.messages[-1]['role'] == 'ASSISTANT'


def test_decorating_persisted_harness_run_preserves_guard_cursor_after_restart(monkeypatch) -> None:
    service = AgentService()
    repository = _Repository()
    monkeypatch.setattr(service, 'repository', lambda: repository)
    run = {
        'runId': 'agr_restart',
        'sessionId': 'ags_harness',
        'goal': '单次分析',
        'taskType': 'ANALYSIS',
        'status': 'WAITING_JOB',
        'currentStage': 'WAITING_JOB',
        'runtimeMode': 'WORKFLOW_HARNESS',
        **freeze_workflow(workflow_definition('ANALYSIS')),
        'currentStep': 'EXECUTION',
        'completedSteps': ['REQUIREMENTS', 'LOAD_PREPARATION', 'PREFLIGHT', 'WAITING_APPROVAL'],
        'artifactIds': [],
    }
    repository.save_run(run)

    decorated = service._decorate_run(run)

    assert decorated['currentStep'] == 'EXECUTION'
    assert repository.get_run('agr_restart')['currentStep'] == 'EXECUTION'
    assert decorated['completedSteps'] == ['REQUIREMENTS', 'LOAD_PREPARATION', 'PREFLIGHT', 'WAITING_APPROVAL']


def test_workflow_state_invalid_cursor_is_structured_not_stop_iteration() -> None:
    run = {
        'workflowId': 'analysis',
        'workflowSnapshot': freeze_workflow(workflow_definition('ANALYSIS'))['workflowSnapshot'],
        'currentStep': 'INQUIRY',
        'completedSteps': [],
        'stepAttempt': 1,
    }

    state = AgentService._workflow_state_from_run(run)

    assert state['allowedTools'] == []
    assert state['error']['code'] == 'WORKFLOW_STEP_NOT_FOUND'


def test_workflow_state_missing_snapshot_is_structured_not_key_error() -> None:
    state = AgentService._workflow_state_from_run({
        'workflowId': 'analysis',
        'currentStep': 'PREFLIGHT',
        'completedSteps': [],
    })

    assert state['allowedTools'] == []
    assert state['error']['code'] == 'WORKFLOW_STEP_NOT_FOUND'


def test_cancelled_cursor_is_terminal_and_survives_runtime_sync() -> None:
    repository = _Repository()
    run = {
        'runId': 'agr_cancelled',
        'taskType': 'ANALYSIS',
        'runtimeMode': 'WORKFLOW_HARNESS',
        'status': 'WAITING_JOB',
        'currentStep': 'EXECUTION',
        'completedSteps': ['REQUIREMENTS', 'LOAD_PREPARATION', 'PREFLIGHT', 'WAITING_APPROVAL'],
        **freeze_workflow(workflow_definition('ANALYSIS')),
    }
    repository.save_run(run)

    AgentService._cancel_workflow_cursor(repository, run)
    decorated = _status_from_workflow_cursor(run)

    assert run['currentStep'] == 'CANCELLED'
    assert run['status'] == 'CANCELLED'
    assert decorated == 'CANCELLED'


def test_approval_rejection_cancels_harness_cursor(monkeypatch) -> None:
    service = AgentService()
    repository = _Repository()
    run = {
        'runId': 'agr_reject',
        'sessionId': 'ags_harness',
        'taskType': 'ANALYSIS',
        'runtimeMode': 'WORKFLOW_HARNESS',
        'status': 'WAITING_APPROVAL',
        'currentStep': 'WAITING_APPROVAL',
        'completedSteps': ['REQUIREMENTS', 'LOAD_PREPARATION', 'PREFLIGHT'],
        'pendingApprovalId': 'approval_reject',
        **freeze_workflow(workflow_definition('ANALYSIS')),
    }
    approval = {
        'approvalId': 'approval_reject',
        'runId': run['runId'],
        'action': 'RUN_ENGINEERING_WORKFLOW',
        'status': 'PENDING',
    }
    repository.save_run(run)
    repository.get_approval = lambda _approval_id: approval
    repository.save_approval = lambda payload: approval.update(payload)
    monkeypatch.setattr(service, 'repository', lambda: repository)
    monkeypatch.setattr(service, '_decorate_run', lambda value: value)

    result = service.decide_approval('approval_reject', approved=False)

    assert result['approval']['status'] == 'REJECTED'
    assert result['run']['status'] == 'CANCELLED'
    assert result['run']['currentStep'] == 'CANCELLED'
    assert result['run']['pendingApprovalId'] is None


def test_decorating_invalid_persisted_cursor_migrates_to_snapshot_initial_step(monkeypatch) -> None:
    service = AgentService()
    repository = _Repository()
    monkeypatch.setattr(service, 'repository', lambda: repository)
    run = {
        'runId': 'agr_invalid_cursor',
        'sessionId': 'ags_harness',
        'goal': '历史运行',
        'taskType': 'FULL_OPTIMIZATION',
        'status': 'WAITING_JOB',
        'runtimeMode': 'WORKFLOW_HARNESS',
        **freeze_workflow(workflow_definition('DAMPER_OPTIMIZATION')),
        'currentStep': 'EXECUTION',
        'completedSteps': ['REQUIREMENTS'],
    }
    repository.save_run(run)

    decorated = service._decorate_run(run)

    assert decorated['currentStep'] == 'REQUIREMENTS'
    assert decorated['completedSteps'] == []
    assert decorated['workflowCursorError'] == 'INVALID_CURSOR_MIGRATED_TO_INITIAL'


def test_all_legacy_statuses_attach_to_valid_snapshot_cursor() -> None:
    task_types = ['ANALYSIS', 'DAMPER_COMPARISON', 'DAMPER_OPTIMIZATION', 'FULL_OPTIMIZATION']
    statuses = [
        'NEEDS_CLARIFICATION', 'WAITING_MAPPING', 'PLANNING', 'PREFLIGHT',
        'WAITING_APPROVAL', 'WAITING_JOB', 'REVIEWING', 'SUCCEEDED',
        'COMPLETED_DIAGNOSTIC', 'FAILED', 'CANCELLED',
    ]
    for task_type in task_types:
        for status in statuses:
            repository = _Repository()
            run = {'runId': f'{task_type}_{status}', 'status': status, 'taskType': task_type}
            AgentService._attach_workflow_runtime(repository, run, task_type)
            snapshot = run['workflowSnapshot']
            steps = {item['stepId']: item for item in snapshot['steps']}
            assert run['currentStep'] in steps
            assert set(run['completedSteps']) <= set(steps)
            assert set(steps[run['currentStep']]['prerequisites']) <= set(run['completedSteps'])


def test_approved_execution_is_guarded_and_trace_is_bound_to_job() -> None:
    repository = _Repository()
    run = {
        'runId': 'agr_guarded',
        'taskType': 'ANALYSIS',
        'runtimeMode': 'WORKFLOW_HARNESS',
        'status': 'WAITING_APPROVAL',
        'currentStep': 'WAITING_APPROVAL',
        'completedSteps': ['REQUIREMENTS', 'LOAD_PREPARATION', 'PREFLIGHT'],
        'stepAttempt': 1,
        **freeze_workflow(workflow_definition('ANALYSIS')),
    }
    repository.save_run(run)

    call_id = AgentService._authorize_approved_execution(
        repository,
        run,
        idempotency_key='agr_guarded:solver:frozen',
    )
    AgentService._finish_execution_tool_call(
        repository,
        run,
        call_id=call_id,
        job_id='job_1',
    )

    assert run['currentStep'] == 'EXECUTION'
    assert 'WAITING_APPROVAL' in run['completedSteps']
    assert repository.get_tool_call(str(call_id))['jobId'] == 'job_1'
    assert repository.get_tool_call(str(call_id))['status'] == 'WAITING_JOB'


def test_approved_execution_is_idempotent_and_resets_step_attempt() -> None:
    repository = _Repository()
    run = {
        'runId': 'agr_idempotent',
        'taskType': 'ANALYSIS',
        'runtimeMode': 'WORKFLOW_HARNESS',
        'status': 'WAITING_APPROVAL',
        'currentStep': 'WAITING_APPROVAL',
        'completedSteps': ['REQUIREMENTS', 'LOAD_PREPARATION', 'PREFLIGHT'],
        'stepAttempt': 1,
        **freeze_workflow(workflow_definition('ANALYSIS')),
    }
    repository.save_run(run)
    key = 'agr_idempotent:solver:frozen'
    effective_arguments = {
        'runId': run['runId'],
        'frozenAction': {'solver': 'ANSYS', 'responseIds': ['max_tower_base_shear']},
    }

    first = AgentService._authorize_approved_execution(
        repository,
        run,
        idempotency_key=key,
        effective_arguments=effective_arguments,
    )
    second = AgentService._authorize_approved_execution(
        repository,
        run,
        idempotency_key=key,
        effective_arguments=effective_arguments,
    )

    assert first == second
    assert run['stepAttempt'] == 1
    assert len(repository.list_tool_calls(run['runId'])) == 1
    trace = repository.list_tool_calls(run['runId'])[0]
    assert trace['arguments'] == {'runId': run['runId']}
    assert trace['effectiveArguments'] == effective_arguments
    assert trace['idempotencyKeySource'] == 'SERVER_DERIVED'
    canonical = json.dumps(effective_arguments, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    assert trace['effectiveArgumentsSha256'] == sha256(canonical.encode('utf-8')).hexdigest()


def test_failed_solver_trace_still_exhausts_single_attempt_step() -> None:
    repository = _Repository()
    run = {
        'runId': 'agr_retry',
        'taskType': 'ANALYSIS',
        'runtimeMode': 'WORKFLOW_HARNESS',
        'status': 'WAITING_APPROVAL',
        'currentStep': 'WAITING_APPROVAL',
        'completedSteps': ['REQUIREMENTS', 'LOAD_PREPARATION', 'PREFLIGHT'],
        'stepAttempt': 1,
        **freeze_workflow(workflow_definition('ANALYSIS')),
    }
    repository.save_run(run)
    AgentService._authorize_approved_execution(repository, run, idempotency_key='agr_retry:solver:v1')
    for item in repository.list_tool_calls(run['runId']):
        repository.save_tool_call({**item, 'status': 'FAILED'})
    # 审批重放会把 stepAttempt 写回 1；计数必须来自已登记轨迹而不是这个字段。
    run['stepAttempt'] = 1

    with pytest.raises(ToolExecutionError) as excinfo:
        AgentService._authorize_approved_execution(repository, run, idempotency_key='agr_retry:solver:v2')

    assert excinfo.value.code == 'WORKFLOW_RETRY_EXHAUSTED'


def test_job_completion_does_not_fast_forward_without_artifact_evidence() -> None:
    repository = _Repository()
    run = {
        'runId': 'agr_gate_evidence',
        'taskType': 'DAMPER_OPTIMIZATION',
        'runtimeMode': 'WORKFLOW_HARNESS',
        'status': 'WAITING_JOB',
        'currentStep': 'BASELINE',
        'completedSteps': ['REQUIREMENTS', 'PREFLIGHT', 'WAITING_APPROVAL'],
        'stepAttempt': 1,
        'jobId': 'job_without_artifact',
        **freeze_workflow(workflow_definition('DAMPER_OPTIMIZATION')),
    }
    repository.save_run(run)

    AgentService._complete_job_tool_calls(
        repository,
        run,
        job_status='SUCCEEDED',
        artifact_ids=[],
    )

    assert run['currentStep'] == 'BASELINE'
    assert run['workflowGateError']['code'] == 'WORKFLOW_GATE_FAILED'


def test_approved_execution_gate_accepts_all_engineering_workflows() -> None:
    cases = {
        'ANALYSIS': ('EXECUTION', ['REQUIREMENTS', 'LOAD_PREPARATION', 'PREFLIGHT']),
        'DAMPER_COMPARISON': ('EXECUTION', ['REQUIREMENTS', 'CALIBRATION', 'PREFLIGHT']),
        'DAMPER_OPTIMIZATION': ('BASELINE', ['REQUIREMENTS', 'PREFLIGHT']),
    }
    for task_type, (expected_step, completed) in cases.items():
        repository = _Repository()
        run = {
            'runId': f'agr_gate_{task_type}',
            'taskType': task_type,
            'runtimeMode': 'WORKFLOW_HARNESS',
            'status': 'WAITING_APPROVAL',
            'currentStep': 'WAITING_APPROVAL',
            'completedSteps': completed,
            'stepAttempt': 1,
            **freeze_workflow(workflow_definition(task_type)),
        }
        repository.save_run(run)

        AgentService._authorize_approved_execution(
            repository,
            run,
            idempotency_key=f'{run["runId"]}:frozen',
        )

        assert run['currentStep'] == expected_step
        assert 'WAITING_APPROVAL' in run['completedSteps']


@pytest.mark.parametrize('doe_count', [4, 25, True, 15.0])
def test_approved_optimization_rejects_invalid_frozen_doe_budget(doe_count: object) -> None:
    repository = _Repository()
    run = {
        'runId': f'agr_invalid_doe_{doe_count}',
        'taskType': 'DAMPER_OPTIMIZATION',
        'runtimeMode': 'WORKFLOW_HARNESS',
        'status': 'WAITING_APPROVAL',
        'currentStep': 'WAITING_APPROVAL',
        'completedSteps': ['REQUIREMENTS', 'PREFLIGHT'],
        'stepAttempt': 1,
        **freeze_workflow(workflow_definition('DAMPER_OPTIMIZATION')),
    }
    repository.save_run(run)

    with pytest.raises(ToolExecutionError) as excinfo:
        AgentService._authorize_approved_execution(
            repository,
            run,
            idempotency_key=f'{run["runId"]}:frozen',
            effective_arguments={
                'runId': run['runId'],
                'frozenAction': {'budget': {'doeDesignCount': doe_count}},
            },
        )

    assert excinfo.value.code == 'WORKFLOW_BUDGET_INVALID'
    assert repository.list_tool_calls(run['runId']) == []


@pytest.mark.parametrize('doe_count', [5, 15, 24])
def test_approved_optimization_accepts_valid_frozen_doe_budget(doe_count: int) -> None:
    repository = _Repository()
    run = {
        'runId': f'agr_valid_doe_{doe_count}',
        'taskType': 'DAMPER_OPTIMIZATION',
        'runtimeMode': 'WORKFLOW_HARNESS',
        'status': 'WAITING_APPROVAL',
        'currentStep': 'WAITING_APPROVAL',
        'completedSteps': ['REQUIREMENTS', 'PREFLIGHT'],
        'stepAttempt': 1,
        **freeze_workflow(workflow_definition('DAMPER_OPTIMIZATION')),
    }
    repository.save_run(run)

    AgentService._authorize_approved_execution(
        repository,
        run,
        idempotency_key=f'{run["runId"]}:frozen',
        effective_arguments={
            'runId': run['runId'],
            'frozenAction': {'budget': {'doeDesignCount': doe_count}},
        },
    )

    trace = repository.list_tool_calls(run['runId'])[0]
    assert trace['status'] == 'RUNNING'
    assert trace['executionSource'] == 'WORKFLOW_HARNESS'


def test_status_is_derived_only_when_cursor_has_a_valid_display_mapping() -> None:
    assert _status_from_workflow_cursor({'currentStep': 'COMPLETED', 'status': 'COMPLETED_DIAGNOSTIC'}) == 'COMPLETED_DIAGNOSTIC'
    assert _status_from_workflow_cursor({'currentStep': 'COMPLETED', 'status': 'SUCCEEDED'}) == 'SUCCEEDED'
    assert _status_from_workflow_cursor({'currentStep': 'DOE', 'status': 'WAITING_JOB'}) == 'WAITING_JOB'
    assert _status_from_workflow_cursor({'currentStep': 'FEM_VALIDATION', 'status': 'REVIEWING'}) == 'REVIEWING'
    assert _status_from_workflow_cursor({'currentStep': 'DOE', 'status': 'PLANNING'}) is None


def test_python_job_completion_records_internal_optimization_stage_traces() -> None:
    repository = _Repository()
    run = {
        'runId': 'agr_optimization_job',
        'taskType': 'DAMPER_OPTIMIZATION',
        'runtimeMode': 'WORKFLOW_HARNESS',
        'status': 'WAITING_JOB',
        'currentStep': 'BASELINE',
        'completedSteps': ['REQUIREMENTS', 'PREFLIGHT', 'WAITING_APPROVAL'],
        'stepAttempt': 1,
        'jobId': 'job_optimization',
        **freeze_workflow(workflow_definition('DAMPER_OPTIMIZATION')),
    }
    repository.save_run(run)

    AgentService._complete_job_tool_calls(
        repository,
        run,
        job_status='SUCCEEDED',
        artifact_ids=['art_result'],
    )

    traces = repository.list_tool_calls(run['runId'])
    traced_steps = {item['stepId'] for item in traces}
    assert {'DOE', 'SURROGATE', 'ACTIVE_LEARNING', 'CANDIDATES', 'RECOMMENDATION', 'FEM_VALIDATION'} <= traced_steps
    assert all(item['executionSource'] == 'PYTHON_JOB' for item in traces)
    assert run['currentStep'] == 'REVIEW'


def test_python_job_stage_traces_follow_registered_contracts_and_risks() -> None:
    repository = _Repository()
    run = {
        'runId': 'agr_optimization_trace_contract',
        'taskType': 'DAMPER_OPTIMIZATION',
        'runtimeMode': 'WORKFLOW_HARNESS',
        'status': 'WAITING_JOB',
        'currentStep': 'BASELINE',
        'completedSteps': ['REQUIREMENTS', 'PREFLIGHT', 'WAITING_APPROVAL'],
        'stepAttempt': 1,
        'jobId': 'job_optimization_trace_contract',
        **freeze_workflow(workflow_definition('DAMPER_OPTIMIZATION')),
    }
    repository.save_run(run)

    AgentService._complete_job_tool_calls(
        repository,
        run,
        job_status='SUCCEEDED',
        artifact_ids=['art_result'],
    )

    traces = repository.list_tool_calls(run['runId'])
    by_name = {item['toolName']: item for item in traces}
    for name, trace in by_name.items():
        validated = harness_capability_registry().require(name).input_model.model_validate(
            trace['arguments'],
        )
        assert validated.model_dump(by_alias=True, mode='json') == trace['arguments']
        assert trace['arguments'] == trace['effectiveArguments']
        assert trace['argumentsSha256'] == trace['effectiveArgumentsSha256']
        assert trace['idempotencyKeySource'] == 'SERVER_DERIVED'
        assert trace['executionSource'] == 'PYTHON_JOB'
        assert trace['jobId'] == run['jobId']
    assert by_name['optimization.run_doe']['risk'] == 'SOLVER_EXECUTION'
    assert by_name['optimization.run_doe']['approved'] is True
    assert by_name['optimization.active_learning']['risk'] == 'SOLVER_EXECUTION'
    assert by_name['optimization.active_learning']['approved'] is True
    assert by_name['optimization.validate_candidates']['risk'] == 'SOLVER_EXECUTION'
    assert by_name['optimization.validate_candidates']['approved'] is True
    assert by_name['optimization.fit_surrogate']['risk'] == 'ARTIFACT_WRITE'
    assert by_name['optimization.fit_surrogate']['approved'] is False


def test_terminal_advancement_distinguishes_clean_and_diagnostic_completion() -> None:
    base = {
        'taskType': 'ANALYSIS',
        'runtimeMode': 'WORKFLOW_HARNESS',
        'currentStep': 'EVIDENCE_REVIEW',
        'completedSteps': [
            'REQUIREMENTS',
            'LOAD_PREPARATION',
            'PREFLIGHT',
            'WAITING_APPROVAL',
            'EXECUTION',
            'RESULT_EXTRACTION',
        ],
        'jobId': 'job_terminal',
        **freeze_workflow(workflow_definition('ANALYSIS')),
    }

    clean_repository = _Repository()
    clean = {**base, 'runId': 'agr_terminal_clean', 'status': 'SUCCEEDED'}
    clean_repository.save_run(clean)
    AgentService._advance_workflow_terminal(
        clean_repository,
        clean,
        evidence_accepted=True,
        report_persisted=True,
    )
    assert clean['currentStep'] == 'COMPLETED'
    assert {'EVIDENCE_REVIEW', 'REPORT'} <= set(clean['completedSteps'])

    diagnostic_repository = _Repository()
    diagnostic = {
        **base,
        'runId': 'agr_terminal_diagnostic',
        'status': 'COMPLETED_DIAGNOSTIC',
    }
    diagnostic_repository.save_run(diagnostic)
    AgentService._advance_workflow_terminal(
        diagnostic_repository,
        diagnostic,
        evidence_accepted=False,
        report_persisted=True,
    )
    assert diagnostic['currentStep'] == 'COMPLETED'
    assert 'EVIDENCE_REVIEW' not in diagnostic['completedSteps']
    assert 'REPORT' in diagnostic['completedSteps']


def test_terminal_advancement_stops_when_report_is_not_persisted() -> None:
    repository = _Repository()
    run = {
        'runId': 'agr_terminal_missing_report',
        'taskType': 'ANALYSIS',
        'runtimeMode': 'WORKFLOW_HARNESS',
        'status': 'SUCCEEDED',
        'currentStep': 'EVIDENCE_REVIEW',
        'completedSteps': [
            'REQUIREMENTS',
            'LOAD_PREPARATION',
            'PREFLIGHT',
            'WAITING_APPROVAL',
            'EXECUTION',
            'RESULT_EXTRACTION',
        ],
        'jobId': 'job_terminal_missing_report',
        **freeze_workflow(workflow_definition('ANALYSIS')),
    }
    repository.save_run(run)

    AgentService._advance_workflow_terminal(
        repository,
        run,
        evidence_accepted=True,
        report_persisted=False,
    )

    assert run['currentStep'] == 'REPORT'
    assert 'EVIDENCE_REVIEW' in run['completedSteps']
    assert 'REPORT' not in run['completedSteps']
    assert run['workflowGateError']['code'] == 'WORKFLOW_GATE_FAILED'


def test_historical_workflow_without_evidence_failure_route_fails_closed() -> None:
    repository = _Repository()
    definition = workflow_definition('ANALYSIS')
    historical_steps = tuple(
        step.model_copy(update={'failure_routes': {}})
        if step.step_id == 'EVIDENCE_REVIEW'
        else step
        for step in definition.steps
    )
    historical = definition.model_copy(update={'version': '1.0.0', 'steps': historical_steps})
    run = {
        'runId': 'agr_historical_evidence_failure',
        'taskType': 'ANALYSIS',
        'runtimeMode': 'WORKFLOW_HARNESS',
        'status': 'COMPLETED_DIAGNOSTIC',
        'currentStep': 'EVIDENCE_REVIEW',
        'completedSteps': [
            'REQUIREMENTS',
            'LOAD_PREPARATION',
            'PREFLIGHT',
            'WAITING_APPROVAL',
            'EXECUTION',
            'RESULT_EXTRACTION',
        ],
        'jobId': 'job_historical',
        **freeze_workflow(historical),
    }
    repository.save_run(run)

    AgentService._advance_workflow_terminal(
        repository,
        run,
        evidence_accepted=False,
        report_persisted=True,
    )

    assert run['workflowSnapshot']['version'] == '1.0.0'
    assert run['currentStep'] == 'EVIDENCE_REVIEW'
    assert 'EVIDENCE_REVIEW' not in run['completedSteps']
    assert run['workflowGateError']['code'] == 'WORKFLOW_GATE_FAILED'


@pytest.mark.parametrize('status', ['FAILED', 'CANCELLED'])
def test_terminal_advancement_does_not_invent_completed_steps_for_failures(status: str) -> None:
    repository = _Repository()
    run = {
        'runId': f'agr_terminal_{status.lower()}',
        'taskType': 'ANALYSIS',
        'runtimeMode': 'WORKFLOW_HARNESS',
        'status': status,
        'currentStep': 'EXECUTION',
        'completedSteps': ['REQUIREMENTS', 'LOAD_PREPARATION', 'PREFLIGHT', 'WAITING_APPROVAL'],
        **freeze_workflow(workflow_definition('ANALYSIS')),
    }
    repository.save_run(run)

    AgentService._advance_workflow_terminal(
        repository,
        run,
        evidence_accepted=False,
        report_persisted=False,
    )

    assert run['currentStep'] == status
    assert run['completedSteps'] == ['REQUIREMENTS', 'LOAD_PREPARATION', 'PREFLIGHT', 'WAITING_APPROVAL']


def test_terminal_result_followup_enters_native_query_without_bootstrap(monkeypatch) -> None:
    service = AgentService()
    repository = _Repository()
    monkeypatch.setenv('MOMO_AGENT_RUNTIME', 'WORKFLOW_HARNESS')
    monkeypatch.setattr(service, 'repository', lambda: repository)
    source = {
        'runId': 'agr_source',
        'sessionId': 'ags_harness',
        'goal': '分析',
        'taskType': 'ANALYSIS',
        'status': 'SUCCEEDED',
        'reportArtifactId': 'art_report',
        'artifactIds': ['art_csv'],
        'updatedAt': '2026-08-08T00:00:00Z',
    }
    repository.save_run(source)
    service.planner = SimpleNamespace(
        run_harness_turn=lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError('终态结果追问不应先调用 bootstrap 模型路由')
        ),
        plan_inquiry=lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError('Harness 追问不应调用旧 plan_inquiry')
        ),
    )
    captured: dict[str, str] = {}

    def create_native(_repository, _session, source_run, _content, now, **_kwargs):
        captured['sourceRunId'] = source_run['runId']
        run = {
            'runId': 'agr_inquiry',
            'sessionId': 'ags_harness',
            'goal': '为什么降低不多',
            'taskType': 'INQUIRY',
            'status': 'SUCCEEDED',
            'currentStage': 'COMPLETED',
            'artifactIds': [],
            'createdAt': now,
            'updatedAt': now,
        }
        repository.save_run(run)
        return run

    monkeypatch.setattr(service, '_create_native_inquiry_run', create_native)
    monkeypatch.setattr(
        service,
        '_dispatch_harness_message',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError('终态结果追问不应进入 ROUTING/bootstrap')
        ),
    )
    monkeypatch.setattr(
        service,
        '_create_inquiry_run',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError('Harness 追问不应走旧追问链')
        ),
    )

    run = service.create_message('ags_harness', '为什么塔底剪力降低不多', None)

    assert run['taskType'] == 'INQUIRY'
    assert captured['sourceRunId'] == 'agr_source'
    assert repository.tool_calls == []


def test_explicit_engineering_task_uses_solver_workflow_even_with_terminal_result(monkeypatch) -> None:
    service = AgentService()
    repository = _Repository()
    monkeypatch.setenv('MOMO_AGENT_RUNTIME', 'WORKFLOW_HARNESS')
    monkeypatch.setattr(service, 'repository', lambda: repository)
    repository.save_run({
        'runId': 'agr_source',
        'sessionId': 'ags_harness',
        'goal': '旧分析',
        'taskType': 'ANALYSIS',
        'status': 'SUCCEEDED',
        'reportArtifactId': 'art_report',
        'artifactIds': ['art_csv'],
        'updatedAt': '2026-08-08T00:00:00Z',
    })
    captured: dict[str, object] = {}

    def dispatch(_repository, _session, _content, _now, _load_import, inquirable, **kwargs):
        captured['sourceRunId'] = inquirable['runId']
        captured['requestedTask'] = kwargs['requested_task']
        return {'runId': 'agr_new', 'taskType': 'ANALYSIS', 'status': 'WAITING_APPROVAL'}

    monkeypatch.setattr(service, '_dispatch_harness_message', dispatch)
    monkeypatch.setattr(
        service,
        '_create_native_inquiry_run',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError('显式工程任务不得被终态结果查询截获')
        ),
    )

    run = service.create_message(
        'ags_harness',
        '使用另一条地震记录重新计算',
        None,
        task_type='ANALYSIS',
    )

    assert run['taskType'] == 'ANALYSIS'
    assert captured == {'sourceRunId': 'agr_source', 'requestedTask': 'ANALYSIS'}


def test_uploaded_auto_message_is_not_intercepted_by_terminal_result(monkeypatch) -> None:
    service = AgentService()
    repository = _Repository()
    monkeypatch.setenv('MOMO_AGENT_RUNTIME', 'WORKFLOW_HARNESS')
    monkeypatch.setattr(service, 'repository', lambda: repository)
    monkeypatch.setattr(
        repository,
        'find_import_by_file',
        lambda _file_id: {'importId': 'loadimp_new', 'fileId': 'file_new'},
    )
    repository.save_run({
        'runId': 'agr_source',
        'sessionId': 'ags_harness',
        'goal': '旧分析',
        'taskType': 'ANALYSIS',
        'status': 'SUCCEEDED',
        'reportArtifactId': 'art_report',
        'artifactIds': ['art_csv'],
        'updatedAt': '2026-08-08T00:00:00Z',
    })
    captured: dict[str, object] = {}

    def dispatch(_repository, _session, _content, _now, load_import, inquirable, **kwargs):
        captured['loadImportId'] = load_import['importId']
        captured['sourceRunId'] = inquirable['runId']
        captured['requestedTask'] = kwargs['requested_task']
        return {'runId': 'agr_new', 'taskType': 'ANALYSIS', 'status': 'PLANNING'}

    monkeypatch.setattr(service, '_dispatch_harness_message', dispatch)
    monkeypatch.setattr(
        service,
        '_create_native_inquiry_run',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError('带上传的 AUTO 消息不得被旧结果查询截获')
        ),
    )

    run = service.create_message(
        'ags_harness',
        '使用这份新荷载重新分析',
        'file_new',
    )

    assert run['taskType'] == 'ANALYSIS'
    assert captured == {
        'loadImportId': 'loadimp_new',
        'sourceRunId': 'agr_source',
        'requestedTask': None,
    }


def test_native_result_inquiry_exposes_only_query_step_tools(monkeypatch) -> None:
    service = AgentService()
    repository = _Repository()
    source = {
        'runId': 'agr_source',
        'sessionId': 'ags_harness',
        'goal': '分析',
        'taskType': 'ANALYSIS',
        'status': 'SUCCEEDED',
        'reportArtifactId': 'art_report',
        'artifactIds': ['art_csv'],
    }
    captured_tools: list[str] = []
    captured_messages: list[dict] = []
    monkeypatch.setattr(service, '_inquiry_artifacts', lambda _run: {'response.csv': 'art_csv'})
    monkeypatch.setattr(
        service,
        '_build_inquiry_catalog',
        lambda _run, _result_service, _artifacts: {'artifacts': {}},
    )

    def run_harness_turn(**kwargs):
        captured_tools.extend(item['name'] for item in kwargs['tools'])
        captured_messages.extend(kwargs['messages'])
        captured_messages.append(kwargs['turn_context'])
        return HarnessModelTurn(finishReason='stop', content='尚未查询。')

    service.planner = SimpleNamespace(run_harness_turn=run_harness_turn)

    run = service._create_native_inquiry_run(
        repository,
        repository.session,
        source,
        '分别给出所有统计量的峰值',
        '2026-08-12T00:00:00Z',
        prior_messages=[],
    )

    assert captured_tools == [
        'result.at_time',
        'result.columns',
        'result.compare',
        'result.compare_runs',
        'result.correlate',
        'result.peak',
        'result.sweep_cases',
        'result.topsis',
    ]
    assert not any(
        call.get('function', {}).get('name') == 'workflow.start'
        for message in captured_messages
        for call in (message.get('tool_calls') or [])
    )
    inquiry_context = captured_messages[-1]['resultInquiryContext']
    assert inquiry_context['sourceRunId'] == 'agr_source'
    assert inquiry_context['registeredArtifacts'] == {'response.csv': 'art_csv'}
    assert run['resultSummary']['code'] == 'INQUIRY_EVIDENCE_REQUIRED'


def test_native_topsis_inquiry_uses_query_result_for_final_model_answer(monkeypatch) -> None:
    service = AgentService()
    repository = _Repository()
    source = {
        'runId': 'agr_source',
        'sessionId': 'ags_harness',
        'goal': '优化',
        'taskType': 'DAMPER_OPTIMIZATION',
        'status': 'SUCCEEDED',
        'reportArtifactId': 'art_report',
        'artifactIds': ['art_summary'],
    }
    monkeypatch.setattr(
        service,
        '_inquiry_artifacts',
        lambda _run: {'optimization_summary.json': 'art_summary'},
    )
    monkeypatch.setattr(
        service,
        '_build_inquiry_catalog',
        lambda _run, _result_service, _artifacts: {
            'topsis': {
                'artifact': 'optimization_summary.json',
                'availableCount': 15,
                'objectiveNames': ['最大位移', '最大剪力'],
            },
        },
    )
    monkeypatch.setattr(
        InquiryTools,
        'call',
        lambda _self, _name, _arguments: SimpleNamespace(
            model_dump=lambda **_kwargs: {
                'objectiveNames': ['最大位移', '最大剪力'],
                'rows': [{
                    'rank': 1,
                    'paretoIndex': 4,
                    'score': 0.91,
                    'parameters': {'c': 7600, 'alpha': 0.8},
                    'objectives': {'最大位移': 0.13, '最大剪力': 48.1},
                }],
                'availableCount': 15,
                'weights': [0.6, 0.4],
            },
        ),
    )
    turns = {'count': 0}

    def run_harness_turn(**_kwargs):
        turns['count'] += 1
        if turns['count'] == 1:
            return HarnessModelTurn(
                finishReason='tool_calls',
                toolCalls=[HarnessToolCall(
                    toolCallId='call_topsis',
                    name='result.topsis',
                    arguments={'artifactId': 'art_summary', 'limit': 10},
                )],
            )
        return HarnessModelTurn(
            finishReason='stop',
            content='TOPSIS 第 1 名的阻尼参数为 c=7600、alpha=0.8，得分为 0.91。',
        )

    service.planner = SimpleNamespace(run_harness_turn=run_harness_turn)
    run = service._create_native_inquiry_run(
        repository,
        repository.session,
        source,
        '给出topsis排序前十的解',
        '2026-08-12T00:00:00Z',
        prior_messages=[],
    )

    assert turns['count'] == 2
    assert run['status'] == 'SUCCEEDED'
    assert run['resultSummary']['narrativeMode'] == 'LLM'
    assert run['resultSummary']['narrativeSummary'] == 'TOPSIS 第 1 名的阻尼参数为 c=7600、alpha=0.8，得分为 0.91。'
    assert run['resultSummary']['inquiryTopsis'][0]['rank'] == 1
    assert run['resultSummary']['inquiryTopsisWeights'] == {
        'objectiveNames': ['最大位移', '最大剪力'],
        'weights': [0.6, 0.4],
    }


def test_native_topsis_inquiry_recovers_when_model_omits_the_unique_tool_call(monkeypatch) -> None:
    service = AgentService()
    repository = _Repository()
    source = {
        'runId': 'agr_source',
        'sessionId': 'ags_harness',
        'goal': '优化',
        'taskType': 'DAMPER_OPTIMIZATION',
        'status': 'SUCCEEDED',
        'reportArtifactId': 'art_report',
        'artifactIds': ['art_summary'],
    }
    monkeypatch.setattr(
        service,
        '_inquiry_artifacts',
        lambda _run: {'optimization_summary.json': 'art_summary'},
    )
    monkeypatch.setattr(
        service,
        '_build_inquiry_catalog',
        lambda _run, _result_service, _artifacts: {
            'topsis': {'artifact': 'optimization_summary.json'},
        },
    )
    monkeypatch.setattr(
        InquiryTools,
        'call',
        lambda _self, _name, _arguments: SimpleNamespace(
            model_dump=lambda **_kwargs: {
                'objectiveNames': ['最大位移'],
                'rows': [{'rank': 1, 'score': 0.91, 'parameters': {'c': 7600}}],
                'availableCount': 1,
                'weights': [1.0],
            },
        ),
    )
    service.planner = SimpleNamespace(
        run_harness_turn=lambda **_kwargs: HarnessModelTurn(
            finishReason='stop', content='排名结果如下。',
        ),
    )

    run = service._create_native_inquiry_run(
        repository,
        repository.session,
        source,
        '给出 TOPSIS 排序前十的解',
        '2026-08-12T00:00:00Z',
        prior_messages=[],
    )

    assert run['status'] == 'SUCCEEDED'
    assert run['resultSummary']['narrativeMode'] == 'DETERMINISTIC'
    assert run['resultSummary']['inquiryTopsis'][0]['rank'] == 1
    assert repository.tool_calls[0]['toolName'] == 'result.topsis'
    assert repository.tool_calls[0]['status'] == 'SUCCEEDED'
    assert not any(
        item.get('resultSummary', {}).get('code') == 'INQUIRY_EVIDENCE_REQUIRED'
        for item in repository.runs.values()
    )


def test_result_inquiry_catalog_includes_completed_runs_from_other_sessions(monkeypatch) -> None:
    service = AgentService()
    repository = _Repository()
    source = {
        'runId': 'agr_source',
        'sessionId': 'ags_harness',
        'goal': '当前结果',
        'taskType': 'ANALYSIS',
        'status': 'SUCCEEDED',
        'reportArtifactId': 'art_report_source',
        'artifactIds': ['art_source'],
        'intent': {'solver': 'OPENSEESPY_INPROC', 'loadKind': 'EARTHQUAKE'},
        'workflowContract': {'model': 'STbridge', 'damper': None},
        'updatedAt': '2026-08-12T00:00:00Z',
    }
    other = {
        'runId': 'agr_other',
        'sessionId': 'ags_other',
        'goal': '黏滞阻尼工况',
        'taskType': 'ANALYSIS',
        'status': 'SUCCEEDED',
        'reportArtifactId': 'art_report_other',
        'artifactIds': ['art_other'],
        'intent': {'solver': 'ANSYS', 'loadKind': 'WIND'},
        'workflowContract': {
            'model': 'STbridge',
            'damper': {'type': 'VISCOUS'},
            'dampingCoefficient': 7600,
            'velocityExponent': 0.8,
        },
        'updatedAt': '2026-08-11T00:00:00Z',
    }
    repository.save_run(source)
    repository.save_run(other)
    monkeypatch.setattr(
        service,
        '_inquiry_artifacts',
        lambda run: {'response.csv': f'art_{run["runId"].removeprefix("agr_")}'},
    )
    monkeypatch.setattr(
        service,
        '_build_inquiry_catalog',
        lambda run, _result_service, _artifacts: {'runId': run['runId']},
    )
    captured: dict[str, object] = {}

    def run_harness_turn(**kwargs):
        captured['turnContext'] = kwargs['turn_context']
        return HarnessModelTurn(finishReason='stop', content='尚未查询。')

    service.planner = SimpleNamespace(run_harness_turn=run_harness_turn)

    service._create_native_inquiry_run(
        repository,
        repository.session,
        source,
        '查询历史结果',
        '2026-08-12T00:00:00Z',
        prior_messages=[],
    )

    context = captured['turnContext']['resultInquiryContext']
    assert context['registeredArtifacts'] == {
        'response.csv': 'art_source',
        'agr_other/response.csv': 'art_other',
    }
    assert context['catalogsByRunId']['agr_other']['artifactBindings'] == {
        'response.csv': {
            'registeredName': 'agr_other/response.csv',
            'artifactId': 'art_other',
        },
    }
    indexed = {item['runId']: item for item in context['availableResults']}
    assert indexed['agr_source']['condition'] == 'EARTHQUAKE'
    assert indexed['agr_source']['model'] == 'STbridge'
    assert indexed['agr_source']['hasDamper'] is False
    assert indexed['agr_other'] == expect_result_metadata(
        run_id='agr_other',
        session_id='ags_other',
        condition='WIND',
        model='STbridge',
        solver='ANSYS',
        damper_type='VISCOUS',
        parameters={'dampingCoefficient': 7600, 'velocityExponent': 0.8},
    )


def test_new_session_bootstrap_can_see_global_result_when_request_is_ambiguous(monkeypatch) -> None:
    service = AgentService()
    repository = _Repository()
    repository.save_run({
        'runId': 'agr_other',
        'sessionId': 'ags_other',
        'taskType': 'ANALYSIS',
        'status': 'SUCCEEDED',
        'reportArtifactId': 'art_report',
        'updatedAt': '2026-08-12T00:00:00Z',
    })
    captured: dict[str, object] = {}

    def dispatch(*_args, **kwargs):
        captured['inquirableRunId'] = _args[5]['runId'] if _args[5] else None
        captured['requestedTask'] = kwargs['requested_task']
        return {'runId': 'agr_inquiry', 'taskType': 'INQUIRY', 'status': 'PLANNING'}

    monkeypatch.setattr(service, '_dispatch_harness_message', dispatch)

    result = service._dispatch_message(
        repository,
        repository.session,
        '用 OpenSeesPy 分析当前桥梁',
        '2026-08-12T00:00:01Z',
        None,
        'AUTO',
    )

    assert result['taskType'] == 'INQUIRY'
    assert captured == {'inquirableRunId': 'agr_other', 'requestedTask': None}


def test_new_session_result_query_routes_through_llm(monkeypatch) -> None:
    """结果查询不再被关键词启发式短路，统一交给 LLM 路由（workflow.start）。"""
    service = AgentService()
    repository = _Repository()
    repository.save_run({
        'runId': 'agr_other',
        'sessionId': 'ags_other',
        'taskType': 'ANALYSIS',
        'status': 'SUCCEEDED',
        'reportArtifactId': 'art_report',
        'updatedAt': '2026-08-12T00:00:00Z',
    })
    captured: dict[str, object] = {}

    def dispatch(*_args, **kwargs):
        captured['inquirableRunId'] = _args[5]['runId'] if _args[5] else None
        captured['requestedTask'] = kwargs['requested_task']
        return {'runId': 'agr_inquiry', 'taskType': 'INQUIRY', 'status': 'SUCCEEDED'}

    monkeypatch.setattr(service, '_dispatch_harness_message', dispatch)

    result = service._dispatch_message(
        repository,
        repository.session,
        '分别给出所有统计量的峰值',
        '2026-08-12T00:00:01Z',
        None,
        'AUTO',
    )

    assert result['taskType'] == 'INQUIRY'
    assert captured == {'inquirableRunId': 'agr_other', 'requestedTask': None}


def expect_result_metadata(
    *,
    run_id: str,
    session_id: str,
    condition: str,
    model: str,
    solver: str,
    damper_type: str,
    parameters: dict[str, object],
) -> dict[str, object]:
    return {
        'runId': run_id,
        'sessionId': session_id,
        'taskType': 'ANALYSIS',
        'status': 'SUCCEEDED',
        'condition': condition,
        'model': model,
        'solver': solver,
        'hasDamper': True,
        'damperTypes': [damper_type],
        'damperParameters': parameters,
        'updatedAt': '2026-08-11T00:00:00Z',
    }


def test_native_result_inquiry_allows_six_peak_queries_before_answer(monkeypatch) -> None:
    service = AgentService()
    repository = _Repository()
    source = {
        'runId': 'agr_source',
        'sessionId': 'ags_harness',
        'goal': '分析',
        'taskType': 'ANALYSIS',
        'status': 'SUCCEEDED',
        'reportArtifactId': 'art_report',
        'artifactIds': ['art_csv'],
    }
    monkeypatch.setattr(service, '_inquiry_artifacts', lambda _run: {'response.csv': 'art_csv'})
    monkeypatch.setattr(
        service,
        '_build_inquiry_catalog',
        lambda _run, _result_service, _artifacts: {'artifacts': {}},
    )
    monkeypatch.setattr(
        InquiryTools,
        'call',
        lambda _self, _name, arguments: SimpleNamespace(
            model_dump=lambda **_kwargs: {
                'column': arguments['column'],
                'peakAbsolute': 1.0,
                'peakSigned': 1.0,
                'peakTime': 0.5,
            },
        ),
    )
    turns = {'count': 0}

    def run_harness_turn(**_kwargs):
        turns['count'] += 1
        if turns['count'] <= 6:
            return HarnessModelTurn(
                finishReason='tool_calls',
                toolCalls=[HarnessToolCall(
                    toolCallId=f'call_peak_{turns["count"]}',
                    name='result.peak',
                    arguments={
                        'artifactId': 'art_csv',
                        'column': f'metric_{turns["count"]}',
                    },
                )],
            )
        return HarnessModelTurn(finishReason='stop', content='六项指标峰值均已查询。')

    service.planner = SimpleNamespace(run_harness_turn=run_harness_turn)

    run = service._create_native_inquiry_run(
        repository,
        repository.session,
        source,
        '分别给出所有统计量的峰值',
        '2026-08-12T00:00:00Z',
        prior_messages=[],
    )

    assert turns['count'] == 7
    assert run['status'] == 'SUCCEEDED'
    assert len(run['inquiryFacts']['queries']) == 6


def test_native_result_inquiry_uses_grounded_fallback_after_two_ungrounded_answers(monkeypatch) -> None:
    service = AgentService()
    repository = _Repository()
    source = {
        'runId': 'agr_source',
        'sessionId': 'ags_harness',
        'goal': '分析',
        'taskType': 'ANALYSIS',
        'status': 'SUCCEEDED',
        'reportArtifactId': 'art_report',
        'artifactIds': ['art_csv'],
    }
    monkeypatch.setattr(service, '_inquiry_artifacts', lambda _run: {'response.csv': 'art_csv'})
    monkeypatch.setattr(
        service,
        '_build_inquiry_catalog',
        lambda _run, _result_service, _artifacts: {'artifacts': {}},
    )
    monkeypatch.setattr(
        InquiryTools,
        'call',
        lambda _self, _name, _arguments: SimpleNamespace(
            model_dump=lambda **_kwargs: {
                'column': 'displacement',
                'peakAbsolute': 8.0,
                'peakSigned': -8.0,
                'peakTime': 3.5,
                'sampleCount': 4001,
            },
        ),
    )
    turns = {'count': 0}

    def run_harness_turn(**_kwargs):
        turns['count'] += 1
        if turns['count'] == 1:
            return HarnessModelTurn(
                finishReason='tool_calls',
                toolCalls=[HarnessToolCall(
                    toolCallId='call_peak',
                    name='result.peak',
                    arguments={
                        'artifactId': 'art_csv',
                        'column': 'displacement',
                    },
                )],
            )
        return HarnessModelTurn(finishReason='stop', content='未经证据支持的峰值为 9。')

    service.planner = SimpleNamespace(run_harness_turn=run_harness_turn)

    run = service._create_native_inquiry_run(
        repository,
        repository.session,
        source,
        '给出位移峰值',
        '2026-08-12T00:00:00Z',
        prior_messages=[],
    )

    assert turns['count'] == 3
    assert run['status'] == 'SUCCEEDED'
    assert run['resultSummary']['narrativeMode'] == 'DETERMINISTIC'
    assert run['resultSummary']['message'] == '已读取 1 项结果指标，数值均来自已登记的只读结果文件。'
    assert run['resultSummary']['inquiryMetrics'] == [{
        'metricId': 'max_girder_end_displacement',
        'label': '最大梁端位移',
        'sourceColumn': 'displacement',
        'peakAbsolute': 8.0,
        'peakSigned': -8.0,
        'unit': 'm',
        'peakTimeS': 3.5,
        'sampleCount': 4001,
    }]
    assert '校验' not in run['resultSummary']['message']


def test_native_result_inquiry_emits_structured_progress_after_each_peak(monkeypatch) -> None:
    service = AgentService()
    repository = _Repository()
    source = {
        'runId': 'agr_source',
        'sessionId': 'ags_harness',
        'goal': '分析',
        'taskType': 'ANALYSIS',
        'status': 'SUCCEEDED',
        'reportArtifactId': 'art_report',
        'artifactIds': ['art_csv'],
    }
    monkeypatch.setattr(service, '_inquiry_artifacts', lambda _run: {'response.csv': 'art_csv'})
    monkeypatch.setattr(
        service,
        '_build_inquiry_catalog',
        lambda _run, _result_service, _artifacts: {'artifacts': {}},
    )
    monkeypatch.setattr(
        InquiryTools,
        'call',
        lambda _self, _name, _arguments: SimpleNamespace(
            model_dump=lambda **_kwargs: {
                'column': 'tower_base_shear',
                'peakAbsolute': 52_299_849.637,
                'peakSigned': 52_299_849.637,
                'peakTime': 22.4,
                'sampleCount': 4001,
            },
        ),
    )
    turns = {'count': 0}

    def run_harness_turn(**_kwargs):
        turns['count'] += 1
        if turns['count'] == 1:
            return HarnessModelTurn(
                finishReason='tool_calls',
                toolCalls=[HarnessToolCall(
                    toolCallId='call_peak',
                    name='result.peak',
                    arguments={'artifactId': 'art_csv', 'column': 'tower_base_shear'},
                )],
            )
        return HarnessModelTurn(finishReason='stop', content='塔底剪力峰值为 52299849.637。')

    service.planner = SimpleNamespace(run_harness_turn=run_harness_turn)
    events: list[dict[str, object]] = []

    run = service._create_native_inquiry_run(
        repository,
        repository.session,
        source,
        '给出塔底剪力峰值',
        '2026-08-12T00:00:00Z',
        prior_messages=[],
        event_sink=events.append,
    )

    assert run['status'] == 'SUCCEEDED'
    assert [event['type'] for event in events] == ['run', 'progress']
    progress_run = events[-1]['run']
    assert progress_run['resultSummary']['queryProgress'] == {
        'completed': 1,
        'message': '已读取 1 项结果指标',
    }
    assert progress_run['resultSummary']['inquiryMetrics'][0]['unit'] == 'N'


def test_legacy_inquiry_markdown_is_projected_to_structured_metrics_on_read() -> None:
    service = AgentService()
    legacy = {
        'runId': 'agr_legacy_inquiry',
        'sessionId': 'ags_legacy',
        'taskType': 'INQUIRY',
        'status': 'SUCCEEDED',
        'artifactIds': [],
        'resultSummary': {
            'message': '| displacement | 0.3919301166619712 |',
            'narrativeSummary': '| displacement | 0.3919301166619712 |',
            'narrativeMode': 'DETERMINISTIC',
        },
        'inquiryFacts': {
            'queries': [{
                'tool': 'result.peak',
                'effectiveArguments': {'artifactId': 'art_csv', 'column': 'displacement'},
                'output': {
                    'column': 'displacement',
                    'peakAbsolute': 0.3919301166619712,
                    'peakSigned': 0.3919301166619712,
                    'peakTime': 20.05,
                    'sampleCount': 4001,
                },
            }],
        },
    }

    projected = service._with_inquiry_projection(legacy)

    assert projected['resultSummary']['message'] == '已读取 1 项结果指标，数值均来自已登记的只读结果文件。'
    assert projected['resultSummary']['inquiryMetrics'][0]['label'] == '最大梁端位移'
    assert legacy['resultSummary']['message'].startswith('| displacement')


def test_inquiry_summary_counts_peak_metrics_not_auxiliary_queries() -> None:
    queries = [
        {'tool': 'result.columns', 'output': {'columns': ['time', 'displacement']}},
        {
            'tool': 'result.peak',
            'effectiveArguments': {'column': 'displacement'},
            'output': {
                'column': 'displacement',
                'peakAbsolute': 0.4,
                'peakSigned': -0.4,
                'peakTime': 2.0,
                'sampleCount': 401,
            },
        },
    ]

    assert AgentService._deterministic_inquiry_answer(queries) == (
        '已读取 1 项结果指标，数值均来自已登记的只读结果文件。'
    )


def test_inquiry_number_grounding_rejects_values_missing_from_evidence() -> None:
    evidence = {'output': {'peakAbsolute': 8.0, 'peakTime': 3.5}}

    assert AgentService._numbers_are_grounded('峰值为 8，发生在 3.5 秒。', evidence)
    assert AgentService._numbers_are_grounded('峰值约为 8.000，发生在 3.50 秒。', evidence)
    assert not AgentService._numbers_are_grounded('峰值为 9。', evidence)


def test_workflow_start_allows_two_model_parameter_corrections(monkeypatch) -> None:
    service = AgentService()
    repository = _Repository()
    monkeypatch.setenv('MOMO_AGENT_RUNTIME', 'WORKFLOW_HARNESS')
    monkeypatch.setattr(service, 'repository', lambda: repository)
    calls = {'count': 0}

    def harness_turn(**_kwargs):
        calls['count'] += 1
        arguments = {'taskType': 'ANALYSIS'} if calls['count'] == 1 else {
            'taskType': 'ANALYSIS',
            'engineeringIntent': {
                'taskType': 'ANALYSIS',
                'solver': 'OPENSEESPY_INPROC',
                'loadKind': 'EARTHQUAKE',
                'responseIds': [],
                'missingFields': [],
                'summary': '执行一次无阻尼分析。',
            },
        }
        return HarnessModelTurn(
            finishReason='tool_calls',
            toolCalls=[HarnessToolCall(
                toolCallId=f'call_{calls["count"]}',
                name='workflow.start',
                arguments=arguments,
            )],
        )

    service.planner = SimpleNamespace(run_harness_turn=harness_turn)

    def create_engineering(_repository, _session, content, now, **_kwargs):
        run = {
            'runId': 'agr_corrected',
            'sessionId': 'ags_harness',
            'goal': content,
            'taskType': 'ANALYSIS',
            'status': 'WAITING_APPROVAL',
            'currentStage': 'SOLVER_APPROVAL',
            'createdAt': now,
            'updatedAt': now,
        }
        repository.save_run(run)
        return run

    monkeypatch.setattr(service, '_create_engineering_run', create_engineering)

    run = service.create_message('ags_harness', '做一次无阻尼分析', None)

    assert run['runId'] == 'agr_corrected'
    assert calls['count'] == 2
