from __future__ import annotations

import io
import json
from types import SimpleNamespace
from urllib.error import HTTPError, URLError

import pytest

from app.services.agent_engineering import EngineeringIntent, SLOT_SPECS
from app.services.agent_llm import (
    ApprovalReplyIntent,
    HarnessModelTurn,
    OpenAICompatiblePlanner,
    WORKFLOW_HARNESS_SYSTEM_PROMPT,
    _normalize_number,
    capability_claims_are_grounded,
    numbers_are_grounded,
)
from app.core.exceptions import LLMUnavailableError
from app.services.agent_service import build_capability_facts
from app.services.agent_service import AgentService


class _FakeRepository:
    def __init__(self) -> None:
        self.session = {
            'sessionId': 'ags_test',
            'title': '测试会话',
            'status': 'ACTIVE',
            'createdAt': '2026-01-01T00:00:00Z',
            'updatedAt': '2026-01-01T00:00:00Z',
        }
        self.runs: list[dict] = []
        self.messages: list[dict] = []
        self.steps: list[dict] = []
        self.pending_clarification: dict | None = None

    def get_session(self, session_id: str) -> dict | None:
        return self.session if session_id == self.session['sessionId'] else None

    def find_import_by_file(self, _file_id: str) -> None:
        return None

    def find_pending_clarification_run(self, _session_id: str) -> dict | None:
        return self.pending_clarification

    def add_message(self, message: dict) -> None:
        self.messages.append(message)

    def save_run(self, run: dict) -> None:
        self.runs.append(run)

    def save_step(self, step: dict) -> None:
        self.steps.append(step)

    def save_session(self, session: dict) -> None:
        self.session = session

    def list_steps(self, _run_id: str) -> list[dict]:
        return list(self.steps)

    def get_approval(self, _approval_id: str) -> None:
        return None


def test_llm_planner_returns_valid_structured_intent(monkeypatch) -> None:
    planner = OpenAICompatiblePlanner(base_url='http://127.0.0.1:11434/v1', model='momo-planner')
    monkeypatch.setattr(
        planner,
        '_request',
        lambda _payload, *, stage=None: {
            'choices': [{
                'message': {
                    'content': '{"taskType":"FULL_OPTIMIZATION","solver":"ANSYS",'
                    '"scenario":"EARTHQUAKE","useVerifiedTemplateLoads":true,'
                    '"requiresRealFem":true,"summary":"运行完整优化"}'
                }
            }]
        },
    )

    result = planner.plan('执行完整阻尼优化')

    assert result.planner_mode == 'LLM'
    assert result.intent.solver == 'ANSYS'
    assert result.intent.scenario == 'EARTHQUAKE'


def test_llm_planner_requires_configuration() -> None:
    with pytest.raises(LLMUnavailableError) as error:
        OpenAICompatiblePlanner(base_url='', model='').plan('执行完整阻尼优化')
    assert error.value.stage == 'INTENT'
    assert error.value.reason == 'LLM_NOT_CONFIGURED'


def test_llm_planner_raises_on_request_failure_or_invalid_json(monkeypatch) -> None:
    planner = OpenAICompatiblePlanner(base_url='http://127.0.0.1:11434/v1', model='momo-planner')
    monkeypatch.setattr(
        planner,
        '_request',
        lambda _payload, *, stage=None: (_ for _ in ()).throw(
            LLMUnavailableError(stage or 'INTENT', 'LLM_CONNECTION_FAILED')
        ),
    )
    with pytest.raises(LLMUnavailableError) as error:
        planner.plan('执行完整阻尼优化')
    assert error.value.reason == 'LLM_CONNECTION_FAILED'

    monkeypatch.setattr(
        planner,
        '_request',
        lambda _payload, *, stage=None: {'choices': [{'message': {'content': 'not-json'}}]},
    )
    with pytest.raises(LLMUnavailableError) as error:
        planner.plan('执行完整阻尼优化')
    assert error.value.reason == 'LLM_INVALID_RESPONSE'


def test_llm_planner_prompt_freezes_supported_intent_values() -> None:
    planner = OpenAICompatiblePlanner(base_url='http://127.0.0.1:11434/v1', model='momo-planner')

    system_prompt = planner._payload('执行完整阻尼优化')['messages'][0]['content']

    assert 'taskType 必须逐字返回 FULL_OPTIMIZATION' in system_prompt
    assert 'solver 必须逐字返回 ANSYS' in system_prompt
    assert 'scenario 必须逐字返回 EARTHQUAKE' in system_prompt


def test_harness_payload_keeps_static_prefix_and_sorted_tool_definitions() -> None:
    planner = OpenAICompatiblePlanner(base_url='http://127.0.0.1:11434/v1', model='momo-planner')
    tools = [
        {
            'name': 'solver.execute',
            'description': '执行求解',
            'inputSchema': {'type': 'object', 'properties': {'solver': {'type': 'string'}}},
        },
        {
            'name': 'analysis.prepare',
            'description': '执行预检',
            'inputSchema': {'type': 'object', 'properties': {}},
        },
    ]
    first = planner._harness_payload(
        messages=[{'role': 'user', 'content': '此前消息'}],
        user_content='执行分析',
        workflow_state={'currentStep': 'PREFLIGHT'},
        tools=tools,
    )
    second = planner._harness_payload(
        messages=[{'role': 'user', 'content': '此前消息'}],
        user_content='继续',
        workflow_state={'currentStep': 'EXECUTION'},
        tools=list(reversed(tools)),
    )

    assert first['messages'][0] == second['messages'][0] == {
        'role': 'system',
        'content': WORKFLOW_HARNESS_SYSTEM_PROMPT,
    }
    assert first['messages'][:-1] == second['messages'][:-1]
    assert [tool['function']['name'] for tool in first['tools']] == [
        'analysis.prepare', 'solver.execute',
    ]
    assert first['tools'] == second['tools']
    assert first['enable_thinking'] is True
    assert first['chat_template_kwargs'] == {'enable_thinking': True}
    assert first['max_tokens'] == 4096
    assert '结合完整对话历史' in first['messages'][0]['content']
    assert json.loads(first['messages'][-1]['content']) == {
        'workflowState': {'currentStep': 'PREFLIGHT'},
        'userContent': '执行分析',
    }


def test_harness_payload_appends_result_context_after_user_content_for_cache() -> None:
    planner = OpenAICompatiblePlanner(base_url='http://127.0.0.1:11434/v1', model='momo-planner')

    payload = planner._harness_payload(
        messages=[
            {'role': 'user', 'content': '此前追问'},
            {'role': 'system', 'content': '旧版服务端上下文'},
            {'role': 'assistant', 'content': '此前回复'},
        ],
        user_content='分别给出所有统计量的峰值',
        workflow_state={'currentStep': 'QUERY'},
        tools=[],
        turn_context={'resultInquiryContext': {'sourceRunId': 'agr_1'}},
    )

    roles = [message['role'] for message in payload['messages']]
    assert roles == ['system', 'user', 'user', 'assistant', 'user']
    assert all(role != 'system' for role in roles[1:])
    final_payload = json.loads(payload['messages'][-1]['content'])
    assert list(final_payload) == ['workflowState', 'userContent', 'resultInquiryContext']
    assert final_payload['userContent'] == '分别给出所有统计量的峰值'
    assert final_payload['resultInquiryContext']['sourceRunId'] == 'agr_1'


def test_harness_thinking_can_be_explicitly_disabled_for_provider_compatibility(monkeypatch) -> None:
    monkeypatch.setenv('MOMO_LLM_HARNESS_THINKING', 'false')
    monkeypatch.setenv('MOMO_LLM_HARNESS_MAX_TOKENS', '8192')
    planner = OpenAICompatiblePlanner(base_url='http://127.0.0.1:11434/v1', model='momo-planner')

    payload = planner._harness_payload(
        messages=[],
        user_content='执行分析',
        workflow_state={'currentStep': 'REQUIREMENTS'},
        tools=[],
    )

    assert payload['enable_thinking'] is False
    assert payload['chat_template_kwargs'] == {'enable_thinking': False}
    assert payload['max_tokens'] == 8192


def test_harness_turn_parses_native_tool_calls_and_cache_usage(monkeypatch) -> None:
    planner = OpenAICompatiblePlanner(base_url='http://127.0.0.1:11434/v1', model='momo-planner')
    monkeypatch.setattr(
        planner,
        '_request',
        lambda _payload, *, stage=None: {
            'choices': [{
                'finish_reason': 'tool_calls',
                'message': {
                    'content': None,
                    'tool_calls': [{
                        'id': 'call_1',
                        'type': 'function',
                        'function': {
                            'name': 'analysis.prepare',
                            'arguments': '{"solver":"OPENSEESPY_INPROC"}',
                        },
                    }],
                },
            }],
            'usage': {'prompt_tokens_details': {'cached_tokens': 384}},
        },
    )

    result = planner.run_harness_turn(
        messages=[],
        user_content='检查环境',
        workflow_state={'currentStep': 'PREFLIGHT'},
        tools=[{
            'name': 'analysis.prepare',
            'description': '执行预检',
            'inputSchema': {'type': 'object', 'properties': {}},
        }],
    )

    assert result == HarnessModelTurn(
        content=None,
        finishReason='tool_calls',
        cachedTokens=384,
        toolCalls=[{
            'toolCallId': 'call_1',
            'name': 'analysis.prepare',
            'arguments': {'solver': 'OPENSEESPY_INPROC'},
        }],
    )


def test_stream_response_reassembles_content_and_tool_arguments() -> None:
    planner = OpenAICompatiblePlanner(base_url='http://127.0.0.1:11434/v1', model='momo-planner')

    class StreamResponse:
        def __init__(self) -> None:
            self.lines = iter([
                b'data: {"choices":[{"delta":{"role":"assistant","tool_calls":[{"index":0,"id":"call_1","function":{"name":"result.topsis","arguments":"{\\"limit\\":"}}]}}]}\n',
                b'\n',
                b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"10}"}}]}}]}\n',
                b'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}\n',
                b'data: [DONE]\n',
            ])

        def readline(self) -> bytes:
            return next(self.lines, b'')

    response = planner._read_stream_response(StreamResponse())

    assert response['choices'][0]['finish_reason'] == 'tool_calls'
    assert response['choices'][0]['message']['tool_calls'][0]['function'] == {
        'name': 'result.topsis',
        'arguments': '{"limit":10}',
    }


def test_stream_response_enforces_total_read_deadline() -> None:
    """慢滴流 keep-alive 不能借单次 socket 超时无限拖长读取。"""
    import time as time_module

    from app.services.agent_llm import message_time_budget

    planner = OpenAICompatiblePlanner(base_url='http://127.0.0.1:11434/v1', model='momo-planner')

    class SlowDripResponse:
        def readline(self) -> bytes:
            time_module.sleep(0.02)
            return b': keep-alive\n'

    with message_time_budget(0.15):
        with pytest.raises(TimeoutError):
            planner._read_stream_response(SlowDripResponse())


def test_stream_response_enforces_cumulative_size_cap() -> None:
    planner = OpenAICompatiblePlanner(base_url='http://127.0.0.1:11434/v1', model='momo-planner')
    flood_chunk = b'data: {"choices":[{"delta":{"content":"' + b'x' * 65536 + b'"}}]}\n'

    class FloodResponse:
        def readline(self) -> bytes:
            return flood_chunk

    with pytest.raises(ValueError, match='大小上限'):
        planner._read_stream_response(FloodResponse())


def test_harness_payload_requests_stream_usage_for_kv_telemetry() -> None:
    planner = OpenAICompatiblePlanner(base_url='http://127.0.0.1:11434/v1', model='momo-planner')

    payload = planner._harness_payload(
        messages=[],
        user_content='执行分析',
        workflow_state={'currentStep': 'REQUIREMENTS'},
        tools=[],
    )

    assert payload['stream'] is True
    assert payload['stream_options'] == {'include_usage': True}


def test_harness_turn_allows_final_text_without_cache_usage(monkeypatch) -> None:
    planner = OpenAICompatiblePlanner(base_url='http://127.0.0.1:11434/v1', model='momo-planner')
    monkeypatch.setattr(
        planner,
        '_request',
        lambda _payload, *, stage=None: {
            'choices': [{
                'finish_reason': 'stop',
                'message': {'content': '<think>先核对工具结果和证据。</think>预检已经完成。'},
            }],
            'usage': {},
        },
    )

    result = planner.run_harness_turn(
        messages=[],
        user_content='继续',
        workflow_state={'currentStep': 'REPORT'},
        tools=[],
    )

    assert result.content == '预检已经完成。'
    assert result.tool_calls == []
    assert result.cached_tokens is None


def test_compress_context_uses_dedicated_model_and_query_aware_prompt(monkeypatch) -> None:
    planner = OpenAICompatiblePlanner(base_url='http://127.0.0.1:11434/v1', model='momo-planner')
    monkeypatch.setenv('MOMO_LLM_COMPRESSION_MODEL', 'momo-compressor')
    payloads: list[dict] = []

    def request(payload, *, stage=None):
        payloads.append(payload)
        return {'choices': [{'message': {'content': '摘要：峰值 3.2 kN（runId=agr_1）。'}}]}

    monkeypatch.setattr(planner, '_request', request)

    summary = planner.compress_context(
        query='给出塔底剪力峰值',
        workflow_state={'taskType': 'ANALYSIS', 'currentStep': 'EVIDENCE_REVIEW'},
        context='历史对话内容……',
        target_chars=2000,
    )

    assert summary == '摘要：峰值 3.2 kN（runId=agr_1）。'
    assert payloads[0]['model'] == 'momo-compressor'
    user_message = payloads[0]['messages'][-1]['content']
    assert 'Given the search query: 给出塔底剪力峰值' in user_message
    assert 'Current context:' in user_message


def test_compress_context_falls_back_to_primary_model(monkeypatch) -> None:
    planner = OpenAICompatiblePlanner(base_url='http://127.0.0.1:11434/v1', model='momo-planner')
    monkeypatch.delenv('MOMO_LLM_COMPRESSION_MODEL', raising=False)
    payloads: list[dict] = []

    def request(payload, *, stage=None):
        payloads.append(payload)
        return {'choices': [{'message': {'content': '摘要。'}}]}

    monkeypatch.setattr(planner, '_request', request)

    planner.compress_context(query='继续', workflow_state=None, context='内容')

    assert payloads[0]['model'] == 'momo-planner'


def test_harness_turn_repairs_malformed_tool_arguments_with_model_retry(monkeypatch) -> None:
    planner = OpenAICompatiblePlanner(base_url='http://127.0.0.1:11434/v1', model='momo-planner')
    payloads: list[dict] = []
    responses = [
        {
            'choices': [{
                'finish_reason': 'tool_calls',
                'message': {
                    'content': None,
                    'tool_calls': [{
                        'id': 'call_bad',
                        'type': 'function',
                        'function': {
                            'name': 'workflow.start',
                            'arguments': '{"taskType":"ANALYSIS"',
                        },
                    }],
                },
            }],
        },
        {
            'choices': [{
                'finish_reason': 'tool_calls',
                'message': {
                    'content': None,
                    'tool_calls': [{
                        'id': 'call_fixed',
                        'type': 'function',
                        'function': {
                            'name': 'workflow.start',
                            'arguments': '{"taskType":"ANALYSIS"}',
                        },
                    }],
                },
            }],
        },
    ]

    def request(payload, *, stage=None):
        payloads.append(payload)
        return responses[len(payloads) - 1]

    monkeypatch.setattr(planner, '_request', request)

    result = planner.run_harness_turn(
        messages=[],
        user_content='输出梁端位移和塔底内力',
        workflow_state={'currentStep': 'REQUIREMENTS'},
        tools=[{
            'name': 'workflow.start',
            'description': '启动工作流',
            'inputSchema': {'type': 'object', 'properties': {'taskType': {'type': 'string'}}},
        }],
    )

    assert result.tool_calls[0].tool_call_id == 'call_fixed'
    assert result.tool_calls[0].arguments == {'taskType': 'ANALYSIS'}
    assert len(payloads) == 2
    correction = json.loads(payloads[1]['messages'][-2]['content'])
    assert correction['formatCorrection']['code'] == 'TOOL_ARGUMENTS_INVALID_JSON'
    assert correction['formatCorrection']['instruction'].startswith('重新调用同一工具')


class _FakeHTTPResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = json.dumps(payload).encode('utf-8')

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return self.payload

    def readline(self) -> bytes:
        return self.payload


def test_request_retries_transient_500_and_succeeds(monkeypatch) -> None:
    planner = OpenAICompatiblePlanner(base_url='http://127.0.0.1:11434/v1', model='momo-planner')
    calls = {'count': 0}

    def fake_urlopen(_request, timeout):
        calls['count'] += 1
        if calls['count'] == 1:
            raise HTTPError('http://llm.test', 500, 'server error', {}, None)
        return _FakeHTTPResponse({'ok': True})

    sleeps: list[float] = []
    monkeypatch.setattr('app.services.agent_llm.urlopen', fake_urlopen)
    monkeypatch.setattr('app.services.agent_llm.time.sleep', sleeps.append)

    assert planner._request({'ping': True}, stage='ROUTING') == {'ok': True}
    assert calls['count'] == 2
    assert sleeps == [0.5]


def test_request_retries_server_error_three_times_then_raises(monkeypatch) -> None:
    planner = OpenAICompatiblePlanner(base_url='http://127.0.0.1:11434/v1', model='momo-planner')
    calls = {'count': 0}

    def fake_urlopen(_request, timeout):
        calls['count'] += 1
        raise HTTPError('http://llm.test', 500, 'server error', {}, None)

    sleeps: list[float] = []
    monkeypatch.setattr('app.services.agent_llm.urlopen', fake_urlopen)
    monkeypatch.setattr('app.services.agent_llm.time.sleep', sleeps.append)

    with pytest.raises(LLMUnavailableError) as info:
        planner._request({'ping': True}, stage='ROUTING')

    assert info.value.reason == 'LLM_SERVER_ERROR'
    assert calls['count'] == planner._MAX_ATTEMPTS == 3
    assert sleeps == list(planner._BACKOFF_SECONDS)


def test_request_retries_strict_openai_payload_after_optional_field_400(monkeypatch) -> None:
    """严格网关拒绝厂商字段时，自动降级但保留工具调用请求。"""

    planner = OpenAICompatiblePlanner(base_url='http://127.0.0.1:11434/v1', model='momo-planner')
    payload = {
        'model': 'momo-planner',
        'messages': [{'role': 'user', 'content': 'ping'}],
        'enable_thinking': True,
        'chat_template_kwargs': {'enable_thinking': True},
        'tools': [],
        'tool_choice': 'auto',
        'parallel_tool_calls': False,
    }
    sent: list[dict] = []

    def fake_urlopen(request, timeout):
        del timeout
        sent.append(json.loads(request.data.decode('utf-8')))
        if len(sent) == 1:
            raise HTTPError(
                'http://llm.test',
                400,
                'unsupported optional fields',
                {},
                io.BytesIO(b'{"error":{"message":"unknown field"}}'),
            )
        return _FakeHTTPResponse({'ok': True})

    monkeypatch.setattr('app.services.agent_llm.urlopen', fake_urlopen)

    assert planner._request(payload, stage='HARNESS') == {'ok': True}
    assert len(sent) == 2
    assert 'enable_thinking' in sent[0]
    assert 'chat_template_kwargs' in sent[0]
    assert 'enable_thinking' not in sent[1]
    assert 'chat_template_kwargs' not in sent[1]
    assert 'tool_choice' not in sent[1]
    assert 'parallel_tool_calls' not in sent[1]


def test_request_keeps_http_error_body_in_detail(monkeypatch) -> None:
    """HTTP 400 的网关诊断正文不能被统一错误文案吞掉。"""

    planner = OpenAICompatiblePlanner(base_url='http://127.0.0.1:11434/v1', model='momo-planner')

    def fake_urlopen(_request, timeout):
        del timeout
        raise HTTPError(
            'http://llm.test',
            400,
            'bad request',
            {},
            io.BytesIO(b'{"error":{"message":"model does not support tools"}}'),
        )

    monkeypatch.setattr('app.services.agent_llm.urlopen', fake_urlopen)

    with pytest.raises(LLMUnavailableError) as info:
        planner._request({'ping': True}, stage='HARNESS')

    assert info.value.reason == 'LLM_SERVER_ERROR'
    assert 'model does not support tools' in (info.value.detail or '')


def test_llm_unavailable_message_exposes_redacted_transport_detail() -> None:
    """用户可看到有限诊断，否则所有网关故障都会伪装成同一个错误。"""

    error = LLMUnavailableError(
        'HARNESS',
        'LLM_SERVER_ERROR',
        detail='HTTP 400: Authorization: Bearer secret-token; unknown field tools',
    )

    message = error.user_message

    assert 'HTTP 400' in message
    assert 'unknown field tools' in message
    assert 'secret-token' not in message


def test_every_declared_stage_and_reason_has_its_own_wording() -> None:
    """漏登记的 stage 会静默落到"理解你的输入"，把别的失败说成没读懂提问。

    HARNESS 就是这么错的：工具已经返回 TOPSIS 结果、只有叙述那步超时，
    用户看到的却是"无法理解你的输入"。
    """

    for stage in LLMUnavailableError.STAGES:
        message = LLMUnavailableError(stage, 'LLM_TIMEOUT', detail='30.0s').user_message
        assert '理解你的输入' not in message, f'{stage} 没有登记中文名'

    for reason in LLMUnavailableError.REASONS:
        message = LLMUnavailableError('HARNESS', reason, detail='30.0s').user_message
        assert not message.endswith('：请稍后重试。'), f'{reason} 没有登记建议文案'


def test_unknown_stage_still_falls_back_instead_of_raising() -> None:
    message = LLMUnavailableError('NOT_A_STAGE', 'LLM_TIMEOUT', detail='30.0s').user_message

    assert message == '无法理解你的输入：模型服务在 30.0s 内未响应，请稍后重试。'


def test_planner_defaults_to_two_minute_timeout_without_environment() -> None:
    planner = OpenAICompatiblePlanner()

    assert planner.timeout_s == 120.0


def test_timeout_detail_reports_effective_not_configured_timeout(monkeypatch) -> None:
    """预算将尽时单次超时会被夹小，报配置值会与用户实际等待时间对不上。"""

    from app.services import agent_llm

    planner = OpenAICompatiblePlanner(
        base_url='http://127.0.0.1:11434/v1', model='momo-planner', timeout_s=30,
    )
    seen: dict[str, float] = {}

    def fake_urlopen(_request, timeout):
        seen['timeout'] = timeout
        raise TimeoutError()

    monkeypatch.setattr(agent_llm, 'urlopen', fake_urlopen)

    with agent_llm.message_time_budget(4.0):
        with pytest.raises(LLMUnavailableError) as info:
            planner._request({'model': 'momo-planner'}, stage='HARNESS')

    assert info.value.reason == 'LLM_TIMEOUT'
    # 实际传给 urlopen 的超时被预算夹小，detail 必须跟着它而不是 30。
    assert seen['timeout'] < 30
    assert info.value.detail == f'{seen["timeout"]:.1f}s'
    assert '30.0s' not in (info.value.detail or '')


@pytest.mark.parametrize(
    ('error', 'reason', 'attempts'),
    [
        (HTTPError('http://llm.test', 401, 'unauthorized', {}, None), 'LLM_AUTH_FAILED', 1),
        (HTTPError('http://llm.test', 400, 'bad request', {}, None), 'LLM_SERVER_ERROR', 1),
        (URLError(TimeoutError()), 'LLM_TIMEOUT', 3),
    ],
)
def test_request_classifies_non_success_errors_and_retry_count(monkeypatch, error, reason, attempts) -> None:
    planner = OpenAICompatiblePlanner(base_url='http://127.0.0.1:11434/v1', model='momo-planner')
    calls = {'count': 0}

    def fake_urlopen(_request, timeout):
        calls['count'] += 1
        raise error

    sleeps: list[float] = []
    monkeypatch.setattr('app.services.agent_llm.urlopen', fake_urlopen)
    monkeypatch.setattr('app.services.agent_llm.time.sleep', sleeps.append)

    with pytest.raises(LLMUnavailableError) as info:
        planner._request({'ping': True}, stage='ROUTING')
    assert info.value.reason == reason
    assert calls['count'] == attempts
    assert len(sleeps) == max(0, attempts - 1)


def test_capability_facts_and_claim_guard_are_registry_backed() -> None:
    facts = build_capability_facts()
    assert facts['damperTypes']['FRICTION']['optimizationReady'] == {
        'ANSYS': False,
        'OPENSEESPY_INPROC': False,
    }
    assert capability_claims_are_grounded('支持 ANSYS 和 OPENSEESPY_INPROC。', facts)
    assert not capability_claims_are_grounded('也支持 SAP2000。', facts)
    assert not capability_claims_are_grounded('FRICTION 阻尼器可以优化。', facts)


def _planner_with_conversation_response(monkeypatch, text: str) -> OpenAICompatiblePlanner:
    planner = OpenAICompatiblePlanner(base_url='http://127.0.0.1:11434/v1', model='momo-planner')
    monkeypatch.setattr(
        planner,
        '_request',
        lambda _payload, *, stage=None: {
            'choices': [{'message': {'content': json.dumps({'text': text}, ensure_ascii=False)}}],
        },
    )
    return planner


def test_respond_conversationally_returns_small_talk_without_upload_prompt(monkeypatch) -> None:
    planner = _planner_with_conversation_response(monkeypatch, '你好，我可以协助桥梁结构响应分析。')

    reply = planner.respond_conversationally(
        message='你好',
        intent='SMALL_TALK',
        capability_facts=build_capability_facts(),
    )

    assert reply == '你好，我可以协助桥梁结构响应分析。'
    assert '请先上传' not in reply


def test_respond_conversationally_capability_reply_uses_registered_solvers(monkeypatch) -> None:
    planner = _planner_with_conversation_response(
        monkeypatch,
        '支持 ANSYS 和 OPENSEESPY_INPROC，真实求解需要人工审批。',
    )

    reply = planner.respond_conversationally(
        message='你支持哪些求解器？',
        intent='CAPABILITY_QUERY',
        capability_facts=build_capability_facts(),
    )

    assert 'ANSYS' in reply
    assert 'OPENSEESPY_INPROC' in reply


def test_respond_conversationally_rejects_foreign_solver_claim(monkeypatch) -> None:
    planner = _planner_with_conversation_response(monkeypatch, '也支持 SAP2000。')

    with pytest.raises(LLMUnavailableError) as error:
        planner.respond_conversationally(
            message='你支持哪些求解器？',
            intent='CAPABILITY_QUERY',
            capability_facts=build_capability_facts(),
        )

    assert error.value.stage == 'CONVERSATION'
    assert error.value.reason == 'LLM_INVALID_RESPONSE'


def test_respond_conversationally_rejects_unready_damper_optimization_claim(monkeypatch) -> None:
    planner = _planner_with_conversation_response(monkeypatch, 'FRICTION 阻尼器可以优化。')

    with pytest.raises(LLMUnavailableError) as error:
        planner.respond_conversationally(
            message='摩擦阻尼器可以优化吗？',
            intent='CAPABILITY_QUERY',
            capability_facts=build_capability_facts(),
        )

    assert error.value.reason == 'LLM_INVALID_RESPONSE'


def test_respond_conversationally_rejects_invalid_json(monkeypatch) -> None:
    planner = OpenAICompatiblePlanner(base_url='http://127.0.0.1:11434/v1', model='momo-planner')
    monkeypatch.setattr(
        planner,
        '_request',
        lambda _payload, *, stage=None: {'choices': [{'message': {'content': 'not-json'}}]},
    )

    with pytest.raises(LLMUnavailableError) as error:
        planner.respond_conversationally(
            message='你好',
            intent='SMALL_TALK',
            capability_facts=build_capability_facts(),
        )

    assert error.value.stage == 'CONVERSATION'
    assert error.value.reason == 'LLM_INVALID_RESPONSE'


def test_engineering_planner_returns_valid_controlled_intent(monkeypatch) -> None:
    planner = OpenAICompatiblePlanner(base_url='http://127.0.0.1:11434/v1', model='momo-planner')
    monkeypatch.setattr(
        planner,
        '_request',
        lambda _payload, *, stage=None: {
            'choices': [{
                'message': {
                    'content': '{"taskType":"DAMPER_OPTIMIZATION","solver":"ANSYS",'
                    '"damperType":"EDDY_CURRENT","loadKind":"WIND","selectedLayoutId":"TWO_PER_TOWER",'
                    '"responseIds":["max_girder_end_displacement"],"budgetProfile":"STANDARD",'
                    '"requiresRealFem":true,"missingFields":[],"summary":"执行电涡流优化"}'
                }
            }]
        },
    )

    result = planner.plan_engineering('使用电涡流阻尼器优化风荷载响应', requested_task='AUTO', has_file=True)

    assert result.planner_mode == 'LLM'
    assert result.intent.task_type == 'DAMPER_OPTIMIZATION'
    assert result.intent.damper_type == 'EDDY_CURRENT'


def test_engineering_planner_preserves_llm_omission_without_keyword_repair(monkeypatch) -> None:
    planner = OpenAICompatiblePlanner(base_url='http://127.0.0.1:11434/v1', model='momo-planner')
    monkeypatch.setattr(
        planner,
        '_request',
        lambda _payload, *, stage=None: {
            'choices': [{
                'message': {
                    'content': '{"taskType":"CLARIFICATION","solver":"ANSYS",'
                    '"damperType":null,"loadKind":"EARTHQUAKE","responseIds":[],'
                    '"budgetProfile":"STANDARD","requiresRealFem":true,'
                    '"missingFields":["damperType"],"summary":"请指定类型"}'
                }
            }]
        },
    )

    result = planner.plan_engineering(
        '使用黏滞阻尼器优化附件地震荷载并提取塔底剪力',
        requested_task='DAMPER_OPTIMIZATION',
        has_file=True,
    )

    assert result.planner_mode == 'LLM'
    assert result.intent.task_type == 'CLARIFICATION'
    assert result.intent.damper_type is None
    assert result.intent.response_ids == []


def test_engineering_prompt_exposes_catalogs_but_not_attachment_rows() -> None:
    planner = OpenAICompatiblePlanner(base_url='http://127.0.0.1:11434/v1', model='momo-planner')

    payload = planner._engineering_payload(
        '分析附件',
        requested_task='AUTO',
        has_file=True,
        attachment_summary={'columns': ['time', 'load'], 'rowCount': 200000},
    )
    prompt = payload['messages'][0]['content']
    user_payload = payload['messages'][1]['content']

    assert 'VISCOUS, FRICTION, EDDY_CURRENT' in prompt
    assert 'ANSYS, OPENSEESPY_INPROC' in prompt
    assert '200000' in user_payload
    assert 'attachmentRows' not in user_payload
    assert payload['enable_thinking'] is False
    assert payload['chat_template_kwargs'] == {'enable_thinking': False}
    assert payload['max_tokens'] == 512


def test_engineering_intent_requires_required_and_suggested_slots_together() -> None:
    planner = OpenAICompatiblePlanner(base_url='http://127.0.0.1:11434/v1', model='momo-planner')
    intent = EngineeringIntent(
        taskType='ANALYSIS',
        solver=None,
        loadKind=None,
        selectedLayoutId=None,
        responseIds=[],
        missingFields=[],
        summary='分析地震响应',
    )

    finalized = planner._finalize_engineering_intent(intent, requested_task='ANALYSIS')

    # solver 有默认值，_finalize 自动填入，不再触发澄清。
    assert finalized.solver == 'OPENSEESPY_INPROC'
    assert finalized.task_type == 'CLARIFICATION'
    assert finalized.missing_fields == ['loadKind', 'responseIds']
    assert SLOT_SPECS['solver']['default'] == 'OPENSEESPY_INPROC'


def test_engineering_intent_keeps_defaulted_budget_out_of_missing_fields() -> None:
    planner = OpenAICompatiblePlanner(base_url='http://127.0.0.1:11434/v1', model='momo-planner')
    intent = EngineeringIntent(
        taskType='ANALYSIS',
        solver='OPENSEESPY_INPROC',
        loadKind='EARTHQUAKE',
        selectedLayoutId='TWO_PER_TOWER',
        responseIds=['max_tower_base_shear'],
        missingFields=[],
        summary='按默认预算分析',
    )

    finalized = planner._finalize_engineering_intent(intent, requested_task='ANALYSIS')

    assert finalized.task_type == 'ANALYSIS'
    assert finalized.missing_fields == []


def test_ask_for_slots_composes_one_question_with_defaults(monkeypatch) -> None:
    planner = OpenAICompatiblePlanner(base_url='http://127.0.0.1:11434/v1', model='momo-planner')
    captured: dict = {}
    monkeypatch.setattr(
        planner,
        '_request',
        lambda payload, *, stage=None: captured.update(payload) or {
            'choices': [{'message': {'content': '{"text":"请确认求解器，并按默认地震荷载和两塔各两个阻尼器执行，可以吗？"}'}}],
        },
    )

    text = planner.ask_for_slots(
        missing_slots=['solver', 'loadKind', 'selectedLayoutId'],
        prior_intent={'taskType': 'ANALYSIS'},
        goal='帮我做分析',
    )

    assert '求解器' in text
    payload = json.loads(captured['messages'][1]['content'])
    assert [item['name'] for item in payload['missingSlots']] == [
        'solver', 'loadKind', 'selectedLayoutId',
    ]
    assert payload['missingSlots'][1]['default'] == 'EARTHQUAKE'


def test_engineering_planner_reconciles_two_comparison_types_from_user_text(monkeypatch) -> None:
    planner = OpenAICompatiblePlanner(base_url='http://127.0.0.1:11434/v1', model='momo-planner')
    monkeypatch.setattr(
        planner,
        '_request',
        lambda _payload, *, stage=None: {
            'choices': [{
                'message': {
                    'content': '{"taskType":"DAMPER_COMPARISON","solver":"ANSYS",'
                    '"damperType":null,"damperTypes":["EDDY_CURRENT","VISCOUS"],'
                    '"loadKind":"EARTHQUAKE","selectedLayoutId":"TWO_PER_TOWER","responseIds":["max_damper_force"],'
                    '"budgetProfile":"STANDARD","requiresRealFem":true,'
                    '"missingFields":[],"summary":"对比两个工况"}'
                }
            }]
        },
    )

    result = planner.plan_engineering(
        '对比黏滞阻尼器和电涡流阻尼器的地震响应',
        requested_task='AUTO',
    )

    assert result.intent.task_type == 'DAMPER_COMPARISON'
    # 对比顺序完全由 LLM 决定。
    assert result.intent.damper_types == ['EDDY_CURRENT', 'VISCOUS']
    prompt = planner._engineering_payload(
        '对比黏滞阻尼器和电涡流阻尼器',
        requested_task='AUTO',
        has_file=False,
        attachment_summary=None,
    )['messages'][0]['content']
    assert 'damperTypes' in prompt
    assert 'DAMPER_COMPARISON' in prompt


def test_task_route_requires_configuration() -> None:
    with pytest.raises(LLMUnavailableError) as error:
        OpenAICompatiblePlanner(base_url='', model='').classify_task('看看地震响应')
    assert error.value.stage == 'ROUTING'
    assert error.value.reason == 'LLM_NOT_CONFIGURED'


def test_task_route_raises_on_request_failure_or_invalid_json(monkeypatch) -> None:
    planner = OpenAICompatiblePlanner(base_url='http://127.0.0.1:11434/v1', model='momo-planner')
    monkeypatch.setattr(
        planner,
        '_request',
        lambda _payload, *, stage=None: (_ for _ in ()).throw(
            LLMUnavailableError(stage or 'ROUTING', 'LLM_CONNECTION_FAILED')
        ),
    )
    with pytest.raises(LLMUnavailableError) as error:
        planner.classify_task('看看地震响应')
    assert error.value.reason == 'LLM_CONNECTION_FAILED'

    monkeypatch.setattr(
        planner,
        '_request',
        lambda _payload, *, stage=None: {'choices': [{'message': {'content': 'not-json'}}]},
    )
    with pytest.raises(LLMUnavailableError) as error:
        planner.classify_task('看看地震响应')
    assert error.value.reason == 'LLM_INVALID_RESPONSE'


def test_task_route_parses_valid_llm_response(monkeypatch) -> None:
    planner = OpenAICompatiblePlanner(base_url='http://127.0.0.1:11434/v1', model='momo-planner')
    monkeypatch.setattr(
        planner,
        '_request',
        lambda _payload, *, stage=None: {
            'choices': [{'message': {'content': '{"taskType":"ANALYSIS","confidence":0.8,"reason":"地震响应诉求"}'}}]
        },
    )

    result = planner.classify_task('帮我看看8度地震下减震效果咋样')

    assert result.route_mode == 'LLM'
    assert result.route.task_type == 'ANALYSIS'
    assert result.route.confidence == pytest.approx(0.8)


def test_engineering_clarification_merges_prior_intent_and_new_goal(monkeypatch) -> None:
    planner = OpenAICompatiblePlanner(base_url='http://127.0.0.1:11434/v1', model='momo-planner')
    captured: dict = {}
    monkeypatch.setattr(
        planner,
        '_request',
        lambda payload, *, stage=None: captured.update(payload) or {
            'choices': [{
                'message': {
                    'content': '{"taskType":"DAMPER_OPTIMIZATION","solver":"ANSYS",'
                    '"damperType":"VISCOUS","loadKind":"EARTHQUAKE",'
                    '"responseIds":["max_tower_base_shear"],"budgetProfile":"STANDARD",'
                    '"requiresRealFem":true,"missingFields":[],"summary":"已补充黏滞阻尼器"}'
                }
            }]
        },
    )
    prior_intent = EngineeringIntent(
        taskType='CLARIFICATION',
        solver='ANSYS',
        damperType=None,
        loadKind='EARTHQUAKE',
        responseIds=['max_tower_base_shear'],
        missingFields=['damperType'],
        summary='请指定阻尼器类型。',
    ).model_dump(by_alias=True)

    result = planner.plan_engineering_clarification(
        '用黏滞阻尼器',
        prior_intent=prior_intent,
        prior_goal='优化地震下的阻尼器参数',
        requested_task='DAMPER_OPTIMIZATION',
    )

    assert result.planner_mode == 'LLM'
    assert result.intent.damper_type == 'VISCOUS'
    sent = json.loads(captured['messages'][1]['content'])
    assert sent['newGoal'] == '用黏滞阻尼器'
    assert sent['priorGoal'] == '优化地震下的阻尼器参数'
    assert set(sent['priorIntent']) <= {
        'taskType', 'solver', 'damperType', 'damperTypes', 'loadKind',
        'responseIds', 'missingFields', 'summary',
        'modelArtifactId', 'responseNodes', 'responseElementIds', 'responseDirection',
    }
    assert 'workflowContract' not in sent['priorIntent']


def test_engineering_clarification_requires_configuration() -> None:
    with pytest.raises(LLMUnavailableError) as error:
        OpenAICompatiblePlanner(base_url='', model='').plan_engineering_clarification(
            '用黏滞阻尼器',
            prior_intent={'taskType': 'CLARIFICATION', 'missingFields': ['damperType']},
            prior_goal='优化地震下的阻尼器参数',
            requested_task='DAMPER_OPTIMIZATION',
        )
    assert error.value.stage == 'CLARIFICATION'
    assert error.value.reason == 'LLM_NOT_CONFIGURED'


def test_narrative_number_normalization_and_recursive_fact_collection() -> None:
    assert _normalize_number('52.299850') == '52.29985'
    assert numbers_are_grounded('结果包含 52.29985。', {'nested': [{'value': 52.299850}]} )
    assert numbers_are_grounded('两个工况，第 1 项，100%', {})
    assert not numbers_are_grounded('门槛为 8%。', {})
    assert numbers_are_grounded('结果约为 52.3。', {'nested': [{'value': 52.29985}]} )
    assert numbers_are_grounded('峰值约为 0.034。', {'nested': [{'value': 0.03395}]} )


def test_numbers_are_grounded_does_not_use_short_count_fields_as_rounding_facts() -> None:
    facts = {
        'sampleIndex': 17,
        'sampleCount': 150,
        'physicalCountPerTower': 2,
        'totalCheckCount': 12,
        'passedCheckCount': 11,
        'shortValues': [0.2, 0.3, 0.4, 0.6, 0.7],
    }

    assert not numbers_are_grounded('位移 0.5 m。', facts)
    assert not numbers_are_grounded('样本编号 17。', facts)


def test_numbers_are_grounded_only_rounds_facts_with_at_least_four_significant_digits() -> None:
    assert numbers_are_grounded('位移约为 0.135 m。', {'value': 0.135040})
    assert numbers_are_grounded('峰值约为 0.034 m。', {'value': 0.03395})
    assert not numbers_are_grounded('速度指数约为 0.4。', {'value': 0.35})
    assert not numbers_are_grounded('图中有 123 条曲线。', {'figures': [{'artifactId': 'art_123', 'metrics': ['位移']} ]})


def test_narrate_result_falls_back_without_configuration() -> None:
    result = OpenAICompatiblePlanner(base_url='', model='').narrate_result(
        task_type='ANALYSIS',
        accepted=True,
        evidence_mode='REAL_FEM',
        template_message='模板结论',
        facts={'value': 52.29985},
    )

    assert result.narrative_mode == 'TEMPLATE_FALLBACK'
    assert result.text == '模板结论'
    assert result.fallback_reason == 'LLM_NOT_CONFIGURED'


def test_describe_pending_action_falls_back_without_configuration() -> None:
    result = OpenAICompatiblePlanner(base_url='', model='').describe_pending_action(
        task_type='ANALYSIS',
        approval_action='RUN_SOLVER',
        facts={'solver': 'OPENSEESPY_INPROC', 'executionTimeoutS': 7200},
        template_message='使用审批冻结动作执行真实分析。',
    )

    assert result.narrative_mode == 'TEMPLATE_FALLBACK'
    assert result.text == '使用审批冻结动作执行真实分析。'
    assert result.fallback_reason == 'LLM_NOT_CONFIGURED'


def test_describe_pending_action_rejects_ungrounded_numbers_and_constrains_prompt(monkeypatch) -> None:
    planner = OpenAICompatiblePlanner(base_url='http://127.0.0.1:11434/v1', model='momo-planner')
    monkeypatch.setattr(
        planner,
        '_request',
        lambda _payload, *, stage=None: {'choices': [{'message': {'content': '{"text":"最长 99 秒。"}'}}]},
    )
    facts = {'solver': 'ANSYS', 'executionTimeoutS': 7200, 'caseCount': 2}

    result = planner.describe_pending_action(
        task_type='DAMPER_COMPARISON',
        approval_action='RUN_DAMPER_COMPARISON',
        facts=facts,
        template_message='模板审批说明。',
    )
    payload = planner._pending_action_payload(
        task_type='DAMPER_COMPARISON',
        approval_action='RUN_DAMPER_COMPARISON',
        facts=facts,
    )

    assert result.narrative_mode == 'TEMPLATE_FALLBACK'
    assert result.fallback_reason == 'LLM_NUMBER_HALLUCINATION'
    prompt = payload['messages'][0]['content']
    assert '只能使用 facts 中的事实和数字' in prompt
    assert '哈希' in prompt and '节点对' in prompt
    assert '参数扫描范围' in prompt
    assert 'hasDamper' in prompt
    assert 'loadCases' in prompt


@pytest.mark.parametrize('reply', ['同意执行', '批准', '可以开始', '好的，按这个执行', 'OK'])
def test_classify_approval_reply_accepts_explicit_positive_anchors(monkeypatch, reply: str) -> None:
    planner = OpenAICompatiblePlanner(base_url='http://127.0.0.1:11434/v1', model='momo-planner')
    monkeypatch.setattr(
        planner,
        '_request',
        lambda _payload, *, stage=None: {
            'choices': [{'message': {'content': '{"decision":"APPROVE","confidence":0.99,"reason":"明确同意"}'}}],
        },
    )

    result = planner.classify_approval_reply(reply, {'summary': '执行分析'})

    assert isinstance(result, ApprovalReplyIntent)
    assert result.decision == 'APPROVE'


@pytest.mark.parametrize('reply', ['不同意', '先别执行', '暂时不要', '我再看看'])
def test_classify_approval_reply_never_accepts_negative_or_unclear_text(monkeypatch, reply: str) -> None:
    planner = OpenAICompatiblePlanner(base_url='http://127.0.0.1:11434/v1', model='momo-planner')
    monkeypatch.setattr(
        planner,
        '_request',
        lambda _payload, *, stage=None: {
            'choices': [{'message': {'content': '{"decision":"APPROVE","confidence":0.99,"reason":"模型判断同意"}'}}],
        },
    )

    result = planner.classify_approval_reply(reply, {'summary': '执行分析'})

    assert result.decision in {'REJECT', 'UNCLEAR'}


def test_classify_approval_reply_downgrades_low_confidence_and_llm_failure(monkeypatch) -> None:
    planner = OpenAICompatiblePlanner(base_url='http://127.0.0.1:11434/v1', model='momo-planner')
    monkeypatch.setattr(
        planner,
        '_request',
        lambda _payload, *, stage=None: {
            'choices': [{'message': {'content': '{"decision":"APPROVE","confidence":0.85,"reason":"可能同意"}'}}],
        },
    )
    assert planner.classify_approval_reply('同意', {}).decision == 'UNCLEAR'

    monkeypatch.setattr(
        planner,
        '_request',
        lambda _payload, *, stage=None: (_ for _ in ()).throw(
            LLMUnavailableError('APPROVAL_REPLY', 'LLM_CONNECTION_FAILED')
        ),
    )
    failed = planner.classify_approval_reply('同意', {})
    assert failed.decision == 'UNCLEAR'


def test_classify_approval_reply_rejects_empty_modify_payload(monkeypatch) -> None:
    planner = OpenAICompatiblePlanner(base_url='http://127.0.0.1:11434/v1', model='momo-planner')
    monkeypatch.setattr(
        planner,
        '_request',
        lambda _payload, *, stage=None: {
            'choices': [{'message': {'content': '{"decision":"MODIFY","confidence":0.99,"modifications":{},"reason":"修改"}'}}],
        },
    )

    assert planner.classify_approval_reply('改一下', {}).decision == 'UNCLEAR'


def test_approval_facts_omit_hashes_and_frozen_hash_ignores_summary() -> None:
    service = AgentService()
    frozen = {
        'solver': 'ANSYS',
        'runMode': 'REAL_DAMPER_COMPARISON',
        'responseIds': ['max_tower_base_shear'],
        'selectedLayoutId': 'layout_1',
        'selectedLayout': {'direction': 'X', 'physicalCountPerTower': 4, 'nodePairs': [[1, 2]]},
        'cases': [{'damperType': 'VISCOUS'}, {'damperType': 'EDDY_CURRENT'}],
        'executionTimeoutS': 7200,
        'budget': {'maxFemRuns': 4},
        'loadDatasetArtifactId': 'art_load',
        'loadDatasetSha256': 'secret-hash',
        'inputProvenance': [
            {'field': 'solver', 'source': 'USER_DECISION', 'value': 'ANSYS'},
            {'field': 'executionTimeoutS', 'source': 'OPERATIONAL_DEFAULT', 'value': 7200},
            {'field': 'loadDataset', 'source': 'FILE_DERIVED', 'value': {'sha256': 'secret-hash'}},
        ],
    }

    contract = {
        'dampingCoefficient': {'min': 1000, 'max': 10000, 'step': 100},
        'velocityExponent': {'min': 0.3, 'max': 1.0, 'step': 0.1},
        'loadCases': ['EARTHQUAKE_40S', 'OPERATION_3600S'],
    }
    facts = service._approval_facts(frozen, contract)
    assert 'sha256' not in repr(facts).lower()
    assert 'nodePairs' not in facts
    assert facts['caseCount'] == 2
    assert facts['damperType'] == ['VISCOUS', 'EDDY_CURRENT']
    assert facts['hasDamper'] is True
    assert facts['dampingCoefficient'] == contract['dampingCoefficient']
    assert facts['velocityExponent'] == contract['velocityExponent']
    assert facts['loadCases'] == contract['loadCases']
    assert facts['fieldSources'] == {}
    assert facts['estimatedRealSolves'] is None
    assert facts['selectedLayout']['physicalCountPerTower'] == 4
    assert facts['inputSources'] == {
        'solver': 'USER_DECISION',
        'executionTimeoutS': 'OPERATIONAL_DEFAULT',
    }
    assert numbers_are_grounded('阻尼系数从 1000 到 10000，步长 100；速度指数从 0.3 到 1.0，步长 0.1。', facts)
    assert service._payload_sha256(frozen) == service._payload_sha256(dict(frozen))


def test_approval_facts_explicitly_mark_analysis_as_uncontrolled_baseline() -> None:
    facts = AgentService._approval_facts(
        {
            'solver': 'OPENSEESPY_INPROC',
            'runMode': 'REAL_BASELINE',
            'responseIds': ['max_tower_base_shear'],
        },
        {'loadCases': ['EARTHQUAKE_40S']},
    )

    assert facts['damperType'] is None
    assert facts['hasDamper'] is False
    assert facts['loadCases'] == ['EARTHQUAKE_40S']


def test_approval_facts_distinguish_bundled_load_from_uploaded_file() -> None:
    facts = AgentService._approval_facts(
        {
            'solver': 'OPENSEESPY_INPROC',
            'loadDatasetArtifactId': 'art_bundled',
            'loadMapping': {'source': 'BUNDLED_PROJECT_DATA'},
        },
        {'loadKind': 'EARTHQUAKE'},
    )

    assert facts['loadSource'] == 'BUNDLED_PROJECT_DATA'
    assert facts['usesUploadedLoad'] is False


def test_create_approval_persists_narrative_metadata_without_hashing_it(monkeypatch) -> None:
    service = AgentService()
    saved: dict = {}
    repository = SimpleNamespace(save_approval=lambda approval: saved.update(approval))
    monkeypatch.setattr(service, 'repository', lambda: repository)
    frozen = {'solver': 'ANSYS', 'executionTimeoutS': 7200}

    approval = service._create_approval(
        run_id='run_1',
        action='RUN_ENGINEERING_WORKFLOW',
        frozen_action=frozen,
        summary='自然语言说明',
        narrative_mode='LLM',
        narrative_fallback_reason=None,
    )

    assert saved['narrativeMode'] == 'LLM'
    assert saved['narrativeFallbackReason'] is None
    assert approval['frozenActionSha256'] == service._payload_sha256(frozen)


@pytest.mark.parametrize(
    ('response', 'reason'),
    [
        ({'choices': [{'message': {'content': '{"text":"结果为 53。"}'}}]}, 'LLM_NUMBER_HALLUCINATION'),
        ({'choices': [{'message': {'content': '{"text":"结果为 52.29985。"}'}}]}, 'LLM_MISSING_DIAGNOSTIC_CAVEAT'),
    ],
)
def test_narrate_result_rejects_unsafe_llm_text(monkeypatch, response, reason) -> None:
    planner = OpenAICompatiblePlanner(base_url='http://127.0.0.1:11434/v1', model='momo-planner')
    monkeypatch.setattr(planner, '_request', lambda _payload, *, stage=None: response)

    result = planner.narrate_result(
        task_type='ANALYSIS',
        accepted=reason != 'LLM_MISSING_DIAGNOSTIC_CAVEAT',
        evidence_mode='DIAGNOSTIC_ONLY',
        template_message='模板结论',
        facts={'value': 52.29985},
    )

    assert result.narrative_mode == 'TEMPLATE_FALLBACK'
    assert result.text == '模板结论'
    assert result.fallback_reason == reason


def test_narrate_result_accepts_grounded_llm_text_and_constrains_prompt(monkeypatch) -> None:
    planner = OpenAICompatiblePlanner(base_url='http://127.0.0.1:11434/v1', model='momo-planner')
    monkeypatch.setattr(
        planner,
        '_request',
        lambda _payload, *, stage=None: {'choices': [{'message': {'content': '{"text":"最大响应为 52.29985。"}'}}]},
    )

    result = planner.narrate_result(
        task_type='ANALYSIS',
        accepted=True,
        evidence_mode='REAL_FEM',
        template_message='模板结论',
        facts={'value': 52.29985},
    )
    payload = planner._narrative_payload(
        task_type='ANALYSIS',
        accepted=False,
        evidence_mode='DIAGNOSTIC_ONLY',
        facts={'value': 52.29985},
    )

    assert result.narrative_mode == 'LLM'
    assert result.text == '最大响应为 52.29985。'
    assert payload['max_tokens'] == 800
    assert payload['response_format'] == {'type': 'json_object'}
    assert '必须逐字来自输入数据' in payload['messages'][0]['content']
    assert '仅供诊断' in payload['messages'][0]['content']


def test_full_optimization_report_adds_narrative_without_changing_evidence(monkeypatch) -> None:
    service = AgentService()
    captured: dict = {}
    service.planner = SimpleNamespace(narrate_result=lambda **_kwargs: SimpleNamespace(
        narrative_mode='LLM',
        text='这是用户可读的结论。',
        fallback_reason=None,
    ))
    monkeypatch.setattr(
        'app.services.agent_service.platform_store.register_artifact',
        lambda **kwargs: captured.update(kwargs) or SimpleNamespace(artifact_id='art_narrative'),
    )
    reflection = {
        'accepted': False,
        'evidenceMode': 'DIAGNOSTIC_ONLY',
        'checks': {'jobStatus': False},
        'message': '模板结论：仅供诊断。',
    }

    artifact = service._register_full_optimization_report(
        {'runId': 'agr_1', 'goal': '完整优化', 'taskType': 'FULL_OPTIMIZATION', 'plannerMode': 'LLM'},
        {'jobId': 'job_1', 'status': 'SUCCEEDED', 'result': {'mode': 'real_baseline_optimization'}},
        reflection,
    )

    assert artifact.artifact_id == 'art_narrative'
    assert captured['preview']['narrativeSummary'] == '这是用户可读的结论。'
    assert captured['preview']['narrativeMode'] == 'LLM'
    assert captured['preview']['checks'] == reflection['checks']
    assert captured['preview']['conclusion'] == reflection['message']


def test_report_registration_survives_narrative_exception(monkeypatch) -> None:
    service = AgentService()
    captured: dict = {}
    def raise_narrative(**_kwargs):
        raise RuntimeError('narrative unavailable')

    service.planner = SimpleNamespace(narrate_result=raise_narrative)
    monkeypatch.setattr(
        'app.services.agent_service.platform_store.register_artifact',
        lambda **kwargs: captured.update(kwargs) or SimpleNamespace(artifact_id='art_fallback'),
    )

    artifact = service._register_full_optimization_report(
        {'runId': 'agr_2', 'goal': '完整优化', 'taskType': 'FULL_OPTIMIZATION'},
        {'jobId': 'job_2', 'status': 'FAILED', 'result': {}},
        {
            'accepted': False,
            'evidenceMode': 'FAILED',
            'checks': {'jobStatus': False},
            'message': '任务失败，不构成最终结论。',
        },
    )

    assert artifact.artifact_id == 'art_fallback'
    assert captured['preview']['narrativeMode'] == 'TEMPLATE_FALLBACK'
    assert captured['preview']['narrativeSummary'] == '任务失败，不构成最终结论。'


def test_create_message_routes_oral_engineering_request_to_engineering(monkeypatch) -> None:
    service = AgentService()
    repository = _FakeRepository()
    captured: dict = {}
    monkeypatch.setattr(service, 'repository', lambda: repository)
    service.planner = SimpleNamespace(classify_task=lambda _goal, has_file=False: SimpleNamespace(
        route_mode='LLM',
        route=SimpleNamespace(task_type='ANALYSIS', confidence=0.8, reason='口语化地震响应诉求'),
    ))

    def capture_engineering(*args, **kwargs):
        captured.update(kwargs)
        return {'taskType': 'ANALYSIS'}

    monkeypatch.setattr(service, '_create_engineering_run', capture_engineering)
    monkeypatch.setattr(service, '_create_legacy_load_run', lambda *args, **kwargs: pytest.fail('不应进入荷载导入分支'))

    result = service.create_message('ags_test', '帮我看看8度地震下减震效果咋样', None)

    assert result['taskType'] == 'ANALYSIS'
    assert captured['requested_task'] == 'ANALYSIS'
    assert captured['route_evidence']['routeMode'] == 'LLM'


def test_create_message_returns_llm_unavailable_without_route_fallback(monkeypatch) -> None:
    service = AgentService()
    repository = _FakeRepository()
    monkeypatch.setattr(service, 'repository', lambda: repository)
    service.planner = SimpleNamespace(
        classify_task=lambda _goal, has_file=False: (_ for _ in ()).throw(
            LLMUnavailableError('ROUTING', 'LLM_CONNECTION_FAILED')
        ),
    )

    result = service.create_message('ags_test', '帮我看看8度地震下减震效果咋样', None)

    assert result['taskType'] == 'LLM_UNAVAILABLE'
    assert result['status'] == 'FAILED'
    assert result['llmFailure']['stage'] == 'ROUTING'
    assert result['llmFailure']['reason'] == 'LLM_CONNECTION_FAILED'
    assert result['resultSummary']['message']
    assert repository.messages[-1]['role'] == 'ASSISTANT'
    assert repository.messages[-1]['runId'] == result['runId']


def test_create_message_routes_clarification_to_llm_conversation(monkeypatch) -> None:
    service = AgentService()
    repository = _FakeRepository()
    monkeypatch.setattr(service, 'repository', lambda: repository)
    service.planner = SimpleNamespace(
        classify_task=lambda _goal, has_file=False: SimpleNamespace(
            route_mode='LLM',
            route=SimpleNamespace(task_type='CLARIFICATION', confidence=0.9, reason='信息不足'),
        ),
        respond_conversationally=lambda **_kwargs: '请说明希望进行分析、优化还是对比。',
    )

    result = service.create_message('ags_test', '你好', None)

    assert result['taskType'] == 'CONVERSATION'
    assert result['status'] == 'SUCCEEDED'
    assert result['routeEvidence']['resolvedTask'] == 'CLARIFICATION'


def test_create_message_resolves_pending_clarification_in_same_run(monkeypatch) -> None:
    service = AgentService()
    repository = _FakeRepository()
    prior_intent = EngineeringIntent(
        taskType='CLARIFICATION',
        solver='ANSYS',
        damperType=None,
        loadKind='EARTHQUAKE',
        responseIds=['max_tower_base_shear'],
        missingFields=['damperType'],
        summary='请指定阻尼器类型。',
    )
    pending_run = {
        'runId': 'agr_pending',
        'sessionId': 'ags_test',
        'goal': '优化地震下阻尼器参数',
        'taskType': 'DAMPER_OPTIMIZATION',
        'status': 'NEEDS_CLARIFICATION',
        'currentStage': 'CLARIFICATION',
        'importId': None,
        'artifactIds': [],
        'jobId': None,
        'intent': prior_intent.model_dump(by_alias=True),
        'workflowContract': {},
        'createdAt': '2026-01-01T00:00:00Z',
        'updatedAt': '2026-01-01T00:00:00Z',
    }
    repository.pending_clarification = pending_run
    monkeypatch.setattr(service, 'repository', lambda: repository)
    service.planner = SimpleNamespace(
        plan_engineering_clarification=lambda new_goal, **kwargs: SimpleNamespace(
            planner_mode='LLM',
            intent=EngineeringIntent(
                taskType='DAMPER_OPTIMIZATION',
                solver='ANSYS',
                damperType='VISCOUS',
                loadKind='EARTHQUAKE',
                responseIds=['max_tower_base_shear'],
                missingFields=[],
                summary='已补充黏滞阻尼器。',
            ),
        ),
    )
    monkeypatch.setattr(
        service,
        '_prepare_engineering_optimization_approval',
        lambda _repository, run, **_kwargs: run.update(
            status='WAITING_APPROVAL',
            currentStage='WAITING_APPROVAL',
            pendingApprovalId='approval_test',
        ),
    )

    result = service.create_message('ags_test', '用黏滞阻尼器', None)

    assert result['runId'] == 'agr_pending'
    assert result['status'] == 'WAITING_APPROVAL'
    assert result['intent']['damperType'] == 'VISCOUS'
    assert result['pendingApprovalId'] == 'approval_test'
    assert repository.messages[-1]['runId'] == 'agr_pending'
    assert not any(run.get('runId') != 'agr_pending' for run in repository.runs)


def test_clarification_llm_failure_preserves_pending_run_for_retry(monkeypatch) -> None:
    service = AgentService()
    repository = _FakeRepository()
    pending_run = {
        'runId': 'agr_pending_failure',
        'sessionId': 'ags_test',
        'goal': '优化地震下阻尼器参数',
        'taskType': 'DAMPER_OPTIMIZATION',
        'status': 'NEEDS_CLARIFICATION',
        'currentStage': 'CLARIFICATION',
        'importId': None,
        'artifactIds': [],
        'jobId': None,
        'intent': {
            'taskType': 'CLARIFICATION',
            'solver': 'ANSYS',
            'damperType': None,
            'loadKind': 'EARTHQUAKE',
            'responseIds': ['max_tower_base_shear'],
            'missingFields': ['damperType'],
            'summary': '请指定阻尼器类型。',
        },
        'workflowContract': {},
    }
    repository.pending_clarification = pending_run
    monkeypatch.setattr(service, 'repository', lambda: repository)
    service.planner = SimpleNamespace(
        plan_engineering_clarification=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            LLMUnavailableError('CLARIFICATION', 'LLM_TIMEOUT', detail='20s')
        ),
    )

    result = service.create_message('ags_test', '用黏滞阻尼器', None)

    assert result['taskType'] == 'LLM_UNAVAILABLE'
    assert result['status'] == 'FAILED'
    assert result['llmFailure']['stage'] == 'CLARIFICATION'
    assert pending_run['status'] == 'NEEDS_CLARIFICATION'
    assert pending_run['currentStage'] == 'CLARIFICATION'
    assert pending_run['goal'] == '优化地震下阻尼器参数'
    assert repository.messages[-1]['role'] == 'ASSISTANT'
    assert repository.messages[-1]['runId'] == result['runId']


def test_create_message_with_explicit_task_does_not_call_route_classifier(monkeypatch) -> None:
    service = AgentService()
    repository = _FakeRepository()
    captured: dict = {}
    monkeypatch.setattr(service, 'repository', lambda: repository)
    service.planner = SimpleNamespace(classify_task=lambda *_args, **_kwargs: pytest.fail('显式任务不应调用路由器'))
    monkeypatch.setattr(
        service,
        '_create_engineering_run',
        lambda *args, **kwargs: captured.update(kwargs) or {'taskType': 'ANALYSIS'},
    )

    service.create_message('ags_test', '提取地震响应', None, task_type='ANALYSIS')

    assert captured['requested_task'] == 'ANALYSIS'
    assert captured['route_evidence']['routeMode'] == 'USER_SPECIFIED'


def test_unsupported_route_creates_unsupported_run(monkeypatch) -> None:
    service = AgentService()
    repository = _FakeRepository()
    monkeypatch.setattr(service, 'repository', lambda: repository)
    monkeypatch.setattr(service, '_decorate_run', lambda run: run)
    service.planner = SimpleNamespace(classify_task=lambda _goal, has_file=False: SimpleNamespace(
        route_mode='LLM',
        route=SimpleNamespace(task_type='UNSUPPORTED', confidence=0.99, reason='与工程分析无关'),
    ), respond_conversationally=lambda **_kwargs: '该请求不在桥梁工程能力范围内。')

    run = service.create_message('ags_test', '给我写一首诗', None)

    assert run['status'] == 'UNSUPPORTED'
    assert run['taskType'] == 'UNSUPPORTED'
    assert run['routeEvidence']['resolvedTask'] == 'UNSUPPORTED'


def _planner_with_engineering_response(monkeypatch, content: str) -> OpenAICompatiblePlanner:
    planner = OpenAICompatiblePlanner(base_url='http://127.0.0.1:11434/v1', model='momo-planner')
    monkeypatch.setattr(planner, '_request', lambda _payload, *, stage=None: {'choices': [{'message': {'content': content}}]})
    return planner


def test_reconcile_uses_llm_solver_when_it_is_controlled(monkeypatch) -> None:
    planner = _planner_with_engineering_response(
        monkeypatch,
        '{"taskType":"ANALYSIS","solver":"OPENSEESPY_INPROC","loadKind":"EARTHQUAKE",'
        '"responseIds":[],"budgetProfile":"STANDARD","requiresRealFem":true,"missingFields":[],'
        '"summary":"使用开源求解器分析"}',
    )

    result = planner.plan_engineering('用开源求解器跑地震分析', requested_task='AUTO')

    assert result.intent.solver == 'OPENSEESPY_INPROC'


@pytest.mark.parametrize(
    'field_payload',
    [
        '"solver":"NOT_A_SOLVER"',
        '"damperType":"NOT_A_DAMPER"',
        '"loadKind":"NOT_A_LOAD"',
    ],
)
def test_reconcile_rejects_out_of_range_llm_values(monkeypatch, field_payload: str) -> None:
    planner = _planner_with_engineering_response(
        monkeypatch,
        '{"taskType":"ANALYSIS",' + field_payload + ',"responseIds":[],"budgetProfile":"STANDARD",'
        '"requiresRealFem":true,"missingFields":[],"summary":"越界字段"}',
    )

    with pytest.raises(LLMUnavailableError) as error:
        planner.plan_engineering('地震分析', requested_task='AUTO')
    assert error.value.stage == 'INTENT'
    assert error.value.reason == 'LLM_INVALID_RESPONSE'


def test_duplicate_llm_comparison_types_are_rejected(monkeypatch) -> None:
    planner = _planner_with_engineering_response(
        monkeypatch,
        '{"taskType":"DAMPER_COMPARISON","solver":"ANSYS","damperTypes":["VISCOUS","VISCOUS"],'
        '"responseIds":[],"budgetProfile":"STANDARD","requiresRealFem":true,"missingFields":[],'
        '"summary":"重复类型"}',
    )

    with pytest.raises(LLMUnavailableError) as error:
        planner.plan_engineering('对比黏滞阻尼器和电涡流阻尼器', requested_task='AUTO')
    assert error.value.reason == 'LLM_INVALID_RESPONSE'


def test_reconcile_always_keeps_llm_summary(monkeypatch) -> None:
    planner = _planner_with_engineering_response(
        monkeypatch,
        '{"taskType":"ANALYSIS","solver":"ANSYS","loadKind":"EARTHQUAKE","responseIds":[],'
        '"budgetProfile":"STANDARD","requiresRealFem":true,"missingFields":[],'
        '"summary":"这是来自模型的辨识摘要"}',
    )

    result = planner.plan_engineering('地震响应分析', requested_task='AUTO')

    assert result.intent.summary == '这是来自模型的辨识摘要'


def test_message_telemetry_collects_calls_events_and_kv_ratio(monkeypatch) -> None:
    """每条消息的 LLM 调用次数、耗时、token 与压缩事件必须可汇总观测。"""
    from app.services.agent_llm import (
        message_telemetry_summary,
        message_time_budget,
        record_message_event,
    )

    planner = OpenAICompatiblePlanner(base_url='http://127.0.0.1:11434/v1', model='momo-planner')
    monkeypatch.setattr(
        'app.services.agent_llm.urlopen',
        lambda _request, timeout: _FakeHTTPResponse({
            'ok': True,
            'usage': {
                'prompt_tokens': 1000,
                'completion_tokens': 50,
                'prompt_tokens_details': {'cached_tokens': 600},
            },
        }),
    )

    with message_time_budget(60):
        planner._request({'ping': True}, stage='ROUTING')
        planner._request({'ping': True}, stage='HARNESS')
        record_message_event('CONTEXT_COMPRESSION', {'foldedChars': 1234})
        summary = message_telemetry_summary()

    assert summary['llmCallCount'] == 2
    assert summary['failedCallCount'] == 0
    assert summary['byStage']['ROUTING']['count'] == 1
    assert summary['byStage']['HARNESS']['count'] == 1
    assert summary['promptTokens'] == 2000
    assert summary['completionTokens'] == 100
    assert summary['cachedTokens'] == 1200
    assert summary['kvCacheHitRatio'] == 0.6
    assert summary['compressionCount'] == 1
    assert summary['budgetS'] == 60
    assert message_telemetry_summary() is None


def test_message_telemetry_records_failed_attempts(monkeypatch) -> None:
    from app.services.agent_llm import message_telemetry_summary, message_time_budget

    planner = OpenAICompatiblePlanner(base_url='http://127.0.0.1:11434/v1', model='momo-planner')

    def fake_urlopen(_request, timeout):
        raise HTTPError('http://llm.test', 500, 'server error', {}, None)

    monkeypatch.setattr('app.services.agent_llm.urlopen', fake_urlopen)
    monkeypatch.setattr('app.services.agent_llm.time.sleep', lambda _s: None)

    with message_time_budget(60):
        with pytest.raises(LLMUnavailableError):
            planner._request({'ping': True}, stage='ROUTING')
        summary = message_telemetry_summary()

    assert summary['llmCallCount'] == planner._MAX_ATTEMPTS
    assert summary['failedCallCount'] == planner._MAX_ATTEMPTS
    assert summary['byStage']['ROUTING']['failed'] == planner._MAX_ATTEMPTS


def test_run_telemetry_totals_accumulate_across_messages() -> None:
    first = {
        'llmCallCount': 3, 'failedCallCount': 0, 'llmTimeS': 1.5,
        'promptTokens': 1000, 'completionTokens': 100, 'cachedTokens': 400,
        'compressionCount': 1,
    }
    second = {
        'llmCallCount': 2, 'failedCallCount': 1, 'llmTimeS': 0.5,
        'promptTokens': 500, 'completionTokens': 20, 'cachedTokens': 500,
    }

    merged = AgentService._merged_run_telemetry(None, first)
    merged = AgentService._merged_run_telemetry(merged, second)

    totals = merged['totals']
    assert totals['messageCount'] == 2
    assert totals['llmCallCount'] == 5
    assert totals['failedCallCount'] == 1
    assert totals['llmTimeS'] == 2.0
    assert totals['promptTokens'] == 1500
    assert totals['cachedTokens'] == 900
    assert totals['kvCacheHitRatio'] == 0.6
    assert totals['compressionCount'] == 1
    assert merged['lastMessage'] == second
