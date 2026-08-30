from __future__ import annotations

import json

from app.api.v1.agent_router import stream_agent_message_events
from app.api.v1.agent_schemas import AgentMessageCreateRequest
from app.services.agent_service import agent_service


def test_message_stream_emits_progress_before_complete(monkeypatch) -> None:
    def create_message(session_id, content, file_id, task_type, *, event_sink=None):
        assert session_id == 'ags_stream'
        assert content == '给出所有峰值'
        assert file_id is None
        assert task_type == 'AUTO'
        assert event_sink is not None
        event_sink({
            'type': 'run',
            'run': {'runId': 'agr_stream', 'status': 'PLANNING', 'artifactIds': []},
        })
        event_sink({
            'type': 'progress',
            'run': {
                'runId': 'agr_stream',
                'status': 'PLANNING',
                'artifactIds': [],
                'resultSummary': {'queryProgress': {'completed': 1}},
            },
        })
        return {'runId': 'agr_stream', 'status': 'SUCCEEDED', 'artifactIds': []}

    monkeypatch.setattr(agent_service, 'create_message', create_message)

    chunks = list(stream_agent_message_events(
        'ags_stream',
        AgentMessageCreateRequest(content='给出所有峰值'),
    ))
    events = [json.loads(chunk) for chunk in chunks]

    assert [event['type'] for event in events] == ['accepted', 'run', 'progress', 'complete']
    assert events[-1]['run']['status'] == 'SUCCEEDED'
