"""会话标题自动总结的回归测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.exceptions import LLMUnavailableError
from app.services.agent_llm import OpenAICompatiblePlanner
from app.services.agent_repository import AgentRepository


def _planner(monkeypatch: pytest.MonkeyPatch, content: str) -> tuple[OpenAICompatiblePlanner, list[dict]]:
    planner = OpenAICompatiblePlanner(base_url='http://127.0.0.1:11434/v1', model='momo-planner')
    payloads: list[dict] = []

    def _request(payload, *, stage=None):
        payloads.append({'payload': payload, 'stage': stage})
        return {'choices': [{'message': {'content': content}}]}

    monkeypatch.setattr(planner, '_request', _request)
    return planner, payloads


def test_summarize_session_title_returns_distinguishing_title(monkeypatch: pytest.MonkeyPatch) -> None:
    planner, payloads = _planner(monkeypatch, '{"title":"车流基线 OpenSeesPy"}')

    title = planner.summarize_session_title(
        '用 OpenSeesPy 分析当前桥梁在车流荷载下的无阻尼基线响应'
    )

    assert title == '车流基线 OpenSeesPy'
    assert payloads[0]['stage'] == 'SESSION_TITLE'
    assert payloads[0]['payload']['messages'][-1]['content'].startswith('用 OpenSeesPy 分析')


def test_summarize_session_title_rejects_unusable_response(monkeypatch: pytest.MonkeyPatch) -> None:
    planner, _ = _planner(monkeypatch, '{"title":"   "}')

    with pytest.raises(LLMUnavailableError) as error:
        planner.summarize_session_title('分析一下')

    assert error.value.stage == 'SESSION_TITLE'


def test_update_session_title_only_touches_title(tmp_path: Path) -> None:
    repository = AgentRepository(tmp_path / 'state.sqlite3')
    repository.save_session({
        'sessionId': 'ags_1',
        'ownerId': 'local',
        'title': 'MOMO 工程智能体',
        'status': 'ACTIVE',
        'createdAt': '2026-08-25T00:00:00Z',
        'updatedAt': '2026-08-25T01:00:00Z',
    })

    assert repository.update_session_title('ags_1', '车流基线 OpenSeesPy') is True

    stored = repository.get_session('ags_1')
    assert stored['title'] == '车流基线 OpenSeesPy'
    assert stored['updatedAt'] == '2026-08-25T01:00:00Z'
    assert stored['status'] == 'ACTIVE'


def test_title_is_scheduled_only_after_first_message_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.agent_service import agent_service

    repository = AgentRepository(tmp_path / 'state.sqlite3')
    repository.save_session({
        'sessionId': 'ags_1',
        'ownerId': 'local',
        'title': 'MOMO 工程智能体',
        'status': 'ACTIVE',
        'createdAt': '2026-08-25T00:00:00Z',
        'updatedAt': '2026-08-25T00:00:00Z',
    })
    monkeypatch.setattr(agent_service, 'repository', lambda: repository)
    order: list[str] = []
    monkeypatch.setattr(
        agent_service,
        '_dispatch_message',
        lambda *_args, **_kwargs: order.append('dispatch') or {'runId': 'agr_1', 'status': 'SUCCEEDED'},
    )
    monkeypatch.setattr(
        agent_service,
        '_schedule_session_title',
        lambda *_args: order.append('title'),
        raising=False,
    )

    agent_service.create_message('ags_1', '用 OpenSeesPy 分析车流荷载', None)
    agent_service.create_message('ags_1', '继续分析', None)

    assert order == ['dispatch', 'title', 'dispatch']
