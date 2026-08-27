import pytest
from fastapi import HTTPException

from app.services.agent_repository import AgentRepository
from app.services.agent_service import AgentService


def test_find_pending_clarification_run_returns_most_recent(tmp_path) -> None:
    repository = AgentRepository(tmp_path / 'agent.sqlite3')
    repository.save_run({
        'runId': 'agr_old',
        'sessionId': 'ags_1',
        'status': 'NEEDS_CLARIFICATION',
        'createdAt': '2026-01-01T00:00:00Z',
        'updatedAt': '2026-01-01T00:00:00Z',
    })
    repository.save_run({
        'runId': 'agr_done',
        'sessionId': 'ags_1',
        'status': 'WAITING_APPROVAL',
        'createdAt': '2026-01-02T00:00:00Z',
        'updatedAt': '2026-01-02T00:00:00Z',
    })
    repository.save_run({
        'runId': 'agr_new',
        'sessionId': 'ags_1',
        'status': 'NEEDS_CLARIFICATION',
        'createdAt': '2026-01-03T00:00:00Z',
        'updatedAt': '2026-01-03T00:00:00Z',
    })

    result = repository.find_pending_clarification_run('ags_1')

    assert result is not None
    assert result['runId'] == 'agr_new'
    assert repository.find_pending_clarification_run('missing') is None


def test_list_all_runs_is_global_and_newest_first(tmp_path) -> None:
    repository = AgentRepository(tmp_path / 'agent.sqlite3')
    for run_id, session_id, updated_at in (
        ('agr_old', 'ags_1', '2026-01-01T00:00:00Z'),
        ('agr_new', 'ags_2', '2026-01-02T00:00:00Z'),
    ):
        repository.save_run({
            'runId': run_id,
            'sessionId': session_id,
            'status': 'SUCCEEDED',
            'updatedAt': updated_at,
        })

    assert [run['runId'] for run in repository.list_all_runs()] == ['agr_new', 'agr_old']


def test_get_session_keeps_each_approval_as_an_inline_history_message(tmp_path, monkeypatch) -> None:
    repository = AgentRepository(tmp_path / 'agent.sqlite3')
    repository.save_session({
        'sessionId': 'ags_history',
        'title': '审批历史',
        'status': 'ACTIVE',
        'createdAt': '2026-01-01T00:00:00Z',
        'updatedAt': '2026-01-01T00:00:00Z',
    })
    repository.save_run({
        'runId': 'agr_history',
        'sessionId': 'ags_history',
        'status': 'WAITING_APPROVAL',
        'createdAt': '2026-01-01T00:00:00Z',
        'updatedAt': '2026-01-01T00:00:02Z',
    })
    repository.add_message({
        'messageId': 'msg_user',
        'sessionId': 'ags_history',
        'role': 'USER',
        'content': '开始分析',
        'createdAt': '2026-01-01T00:00:01Z',
    })
    repository.save_approval({
        'approvalId': 'approval_history',
        'runId': 'agr_history',
        'status': 'PENDING',
        'summary': '请确认单次分析。',
        'createdAt': '2026-01-01T00:00:02Z',
        'updatedAt': '2026-01-01T00:00:02Z',
    })
    service = AgentService()
    monkeypatch.setattr(service, 'repository', lambda: repository)

    detail = service.get_session('ags_history')

    approval_messages = [message for message in detail['messages'] if message.get('approvalId') == 'approval_history']
    assert len(approval_messages) == 1
    assert approval_messages[0]['messageType'] == 'APPROVAL'
    assert approval_messages[0]['approval']['summary'] == '请确认单次分析。'


def test_tool_calls_round_trip_and_update_without_duplication(tmp_path) -> None:
    repository = AgentRepository(tmp_path / 'agent.sqlite3')
    repository.save_tool_call({
        'toolCallId': 'call_1',
        'runId': 'agr_1',
        'workflowStep': 'PREFLIGHT',
        'toolName': 'analysis.prepare',
        'toolVersion': '1.0.0',
        'arguments': {'solver': 'OPENSEESPY_INPROC'},
        'argumentsSha256': 'a' * 64,
        'risk': 'READ_ONLY',
        'status': 'PENDING',
        'createdAt': '2026-01-01T00:00:00Z',
        'updatedAt': '2026-01-01T00:00:00Z',
    })
    repository.save_tool_call({
        **repository.get_tool_call('call_1'),
        'status': 'SUCCEEDED',
        'compactResult': {'passed': True},
        'updatedAt': '2026-01-01T00:00:01Z',
    })

    stored = repository.get_tool_call('call_1')

    assert stored is not None
    assert stored['status'] == 'SUCCEEDED'
    assert stored['compactResult'] == {'passed': True}
    assert repository.list_tool_calls('agr_1') == [stored]


def test_decorated_run_exposes_workflow_tool_trace(tmp_path, monkeypatch) -> None:
    repository = AgentRepository(tmp_path / 'agent.sqlite3')
    run = {
        'runId': 'agr_trace',
        'sessionId': 'ags_trace',
        'status': 'PLANNING',
        'runtimeMode': 'WORKFLOW_HARNESS',
        'workflowId': 'analysis',
        'workflowVersion': '1.0.0',
        'workflowSha256': 'b' * 64,
        'currentStep': 'PREFLIGHT',
        'completedSteps': ['REQUIREMENTS', 'LOAD_PREPARATION'],
        'createdAt': '2026-01-01T00:00:00Z',
        'updatedAt': '2026-01-01T00:00:00Z',
    }
    repository.save_run(run)
    repository.save_tool_call({
        'toolCallId': 'call_trace',
        'runId': 'agr_trace',
        'workflowStep': 'PREFLIGHT',
        'toolName': 'analysis.prepare',
        'toolVersion': '1.0.0',
        'arguments': {},
        'argumentsSha256': 'c' * 64,
        'risk': 'READ_ONLY',
        'status': 'PENDING',
        'createdAt': '2026-01-01T00:00:01Z',
        'updatedAt': '2026-01-01T00:00:01Z',
    })
    service = AgentService()
    monkeypatch.setattr(service, 'repository', lambda: repository)

    decorated = service._decorate_run(run)

    assert decorated['toolCalls'][0]['toolCallId'] == 'call_trace'
    assert decorated['currentStep'] == 'PREFLIGHT'


def test_get_session_marks_completed_result_metadata(tmp_path, monkeypatch) -> None:
    repository = AgentRepository(tmp_path / 'agent.sqlite3')
    repository.save_session({
        'sessionId': 'ags_result',
        'title': '结果标记',
        'status': 'ACTIVE',
        'createdAt': '2026-01-01T00:00:00Z',
        'updatedAt': '2026-01-01T00:00:01Z',
    })
    repository.save_run({
        'runId': 'agr_result',
        'sessionId': 'ags_result',
        'taskType': 'ANALYSIS',
        'status': 'SUCCEEDED',
        'reportArtifactId': 'art_report',
        'artifactIds': ['art_csv'],
        'intent': {'solver': 'OPENSEESPY_INPROC', 'loadKind': 'EARTHQUAKE'},
        'workflowContract': {
            'model': 'STbridge',
            'damper': {'type': 'VISCOUS'},
            'dampingCoefficient': 7600,
            'velocityExponent': 0.8,
        },
        'createdAt': '2026-01-01T00:00:00Z',
        'updatedAt': '2026-01-01T00:00:01Z',
    })
    service = AgentService()
    monkeypatch.setattr(service, 'repository', lambda: repository)
    monkeypatch.setattr(
        'app.services.agent_service.platform_store.get_artifact',
        lambda artifact_id: type('Record', (), {
            'artifact': type('Artifact', (), {
                'artifact_id': artifact_id,
                'name': 'timeseries.csv',
                'kind': 'CSV_TIMESERIES',
                'size_bytes': 123,
            })()
        })(),
    )

    run = service.get_session('ags_result')['runs'][0]

    assert run['resultMetadata'] == {
        'runId': 'agr_result',
        'sessionId': 'ags_result',
        'taskType': 'ANALYSIS',
        'status': 'SUCCEEDED',
        'condition': 'EARTHQUAKE',
        'model': 'STbridge',
        'solver': 'OPENSEESPY_INPROC',
        'hasDamper': True,
        'damperTypes': ['VISCOUS'],
        'damperParameters': {'dampingCoefficient': 7600, 'velocityExponent': 0.8},
        'updatedAt': '2026-01-01T00:00:01Z',
    }
    assert run['resultArtifacts'] == [{
        'artifactId': 'art_csv',
        'name': 'timeseries.csv',
        'kind': 'CSV_TIMESERIES',
        'sizeBytes': 123,
    }]


def test_get_session_marks_completed_result_without_report_artifact(tmp_path, monkeypatch) -> None:
    repository = AgentRepository(tmp_path / 'agent.sqlite3')
    repository.save_session({
        'sessionId': 'ags_result_without_report',
        'title': '无报告结果',
        'status': 'ACTIVE',
        'createdAt': '2026-01-01T00:00:00Z',
        'updatedAt': '2026-01-01T00:00:01Z',
    })
    repository.save_run({
        'runId': 'agr_result_without_report',
        'sessionId': 'ags_result_without_report',
        'taskType': 'ANALYSIS',
        'status': 'SUCCEEDED',
        'artifactIds': ['art_csv'],
        'intent': {'solver': 'OPENSEESPY_INPROC', 'loadKind': 'EARTHQUAKE'},
        'createdAt': '2026-01-01T00:00:00Z',
        'updatedAt': '2026-01-01T00:00:01Z',
    })
    service = AgentService()
    monkeypatch.setattr(service, 'repository', lambda: repository)
    monkeypatch.setattr(
        'app.services.agent_service.platform_store.get_artifact',
        lambda artifact_id: type('Record', (), {
            'artifact': type('Artifact', (), {
                'artifact_id': artifact_id,
                'name': 'timeseries.csv',
                'kind': 'CSV_TIMESERIES',
                'size_bytes': 123,
            })()
        })(),
    )

    run = service.get_session('ags_result_without_report')['runs'][0]

    assert run['resultArtifacts'][0]['name'] == 'timeseries.csv'


def test_get_session_replaces_legacy_inquiry_markdown_message_without_rewriting_audit(tmp_path, monkeypatch) -> None:
    repository = AgentRepository(tmp_path / 'agent.sqlite3')
    repository.save_session({
        'sessionId': 'ags_inquiry_history',
        'title': '历史结果查询',
        'status': 'ACTIVE',
        'createdAt': '2026-01-01T00:00:00Z',
        'updatedAt': '2026-01-01T00:00:03Z',
    })
    legacy_markdown = '模型叙述未通过数字证据校验。\n| displacement | 0.4 |'
    repository.save_run({
        'runId': 'agr_inquiry_history',
        'sessionId': 'ags_inquiry_history',
        'taskType': 'INQUIRY',
        'status': 'SUCCEEDED',
        'artifactIds': [],
        'resultSummary': {
            'message': legacy_markdown,
            'narrativeSummary': legacy_markdown,
            'narrativeMode': 'DETERMINISTIC',
        },
        'inquiryFacts': {
            'queries': [{
                'tool': 'result.peak',
                'effectiveArguments': {'column': 'displacement'},
                'output': {
                    'column': 'displacement',
                    'peakAbsolute': 0.4,
                    'peakSigned': -0.4,
                    'peakTime': 2.0,
                    'sampleCount': 401,
                },
            }],
        },
        'createdAt': '2026-01-01T00:00:01Z',
        'updatedAt': '2026-01-01T00:00:02Z',
    })
    repository.add_message({
        'messageId': 'msg_legacy_inquiry',
        'sessionId': 'ags_inquiry_history',
        'role': 'ASSISTANT',
        'runId': 'agr_inquiry_history',
        'content': legacy_markdown,
        'createdAt': '2026-01-01T00:00:02Z',
    })
    service = AgentService()
    monkeypatch.setattr(service, 'repository', lambda: repository)

    detail = service.get_session('ags_inquiry_history')

    assert detail['messages'][0]['content'] == (
        '已读取 1 项结果指标，数值均来自已登记的只读结果文件。'
    )
    assert repository.list_messages('ags_inquiry_history')[0]['content'] == legacy_markdown


def test_delete_session_history_removes_visible_conversation_but_retains_audit(tmp_path, monkeypatch) -> None:
    repository = AgentRepository(tmp_path / 'agent.sqlite3')
    repository.save_session({
        'sessionId': 'ags_delete',
        'title': '待删除测试会话',
        'status': 'ACTIVE',
        'createdAt': '2026-01-01T00:00:00Z',
        'updatedAt': '2026-01-01T00:00:02Z',
    })
    repository.add_message({
        'messageId': 'msg_delete',
        'sessionId': 'ags_delete',
        'role': 'USER',
        'content': '测试消息',
        'createdAt': '2026-01-01T00:00:01Z',
    })
    repository.save_run({
        'runId': 'agr_delete',
        'sessionId': 'ags_delete',
        'status': 'SUCCEEDED',
        'createdAt': '2026-01-01T00:00:01Z',
        'updatedAt': '2026-01-01T00:00:02Z',
    })
    repository.save_tool_call({
        'toolCallId': 'call_delete',
        'runId': 'agr_delete',
        'toolName': 'analysis.run',
        'status': 'SUCCEEDED',
        'updatedAt': '2026-01-01T00:00:02Z',
    })
    service = AgentService()
    monkeypatch.setattr(service, 'repository', lambda: repository)

    result = service.delete_session('ags_delete')

    assert result == {
        'sessionId': 'ags_delete',
        'deleted': True,
        'retainedRunCount': 1,
        'cancelledRunCount': 0,
    }
    assert repository.get_session('ags_delete') is None
    assert repository.list_messages('ags_delete') == []
    assert repository.get_run('agr_delete') is not None
    assert repository.get_tool_call('call_delete') is not None


def test_delete_session_cancels_nonterminal_runs_and_retains_audit(tmp_path, monkeypatch) -> None:
    repository = AgentRepository(tmp_path / 'agent.sqlite3')
    repository.save_session({
        'sessionId': 'ags_running',
        'title': '运行中会话',
        'status': 'ACTIVE',
        'createdAt': '2026-01-01T00:00:00Z',
        'updatedAt': '2026-01-01T00:00:00Z',
    })
    repository.save_run({
        'runId': 'agr_running',
        'sessionId': 'ags_running',
        'status': 'WAITING_JOB',
        'createdAt': '2026-01-01T00:00:00Z',
        'updatedAt': '2026-01-01T00:00:00Z',
    })
    service = AgentService()
    monkeypatch.setattr(service, 'repository', lambda: repository)

    result = service.delete_session('ags_running')

    assert result == {
        'sessionId': 'ags_running',
        'deleted': True,
        'retainedRunCount': 1,
        'cancelledRunCount': 1,
    }
    assert repository.get_session('ags_running') is None
    assert repository.get_run('agr_running')['status'] == 'CANCELLED'
