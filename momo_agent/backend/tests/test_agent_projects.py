from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.v1.agent_project_schemas import EngineeringWorkspacePatch
from app.main import app
from app.services.agent_project_service import EngineeringProjectService
from app.services.agent_service import agent_service
from app.services.platform_store import platform_store, utc_now


@pytest.fixture(autouse=True)
def isolated_project_state(tmp_path: Path):
    original_state_path = platform_store.state_path
    platform_store.state_path = tmp_path / 'engineering_projects.sqlite3'
    try:
        yield
    finally:
        platform_store.state_path = original_state_path


def test_project_workspace_persists_and_groups_sessions_and_runs() -> None:
    service = EngineeringProjectService()
    project = service.create_project(
        'STbridge 参数优化',
        description='统一承载模型、工况和 Agent run。',
        workspace={
            'solver': 'OPENSEESPY_INPROC',
            'loadKind': 'WIND',
            'optimizationProfile': 'FULL',
        },
    )

    session = service.create_session(project['projectId'], '风致响应优化')
    now = utc_now()
    agent_service.repository().save_run({
        'runId': 'agr_project_history',
        'sessionId': session['sessionId'],
        'goal': '执行风致阻尼优化',
        'taskType': 'DAMPER_OPTIMIZATION',
        'status': 'SUCCEEDED',
        'currentStage': 'COMPLETED',
        'artifactIds': [],
        'createdAt': now,
        'updatedAt': now,
    })
    loaded = service.get_project(project['projectId'])

    assert session['projectId'] == project['projectId']
    assert loaded['sessionCount'] == 1
    assert loaded['runCount'] == 1
    assert loaded['sessions'][0]['sessionId'] == session['sessionId']
    assert loaded['sessions'][0]['runCount'] == 1
    assert loaded['runs'][0]['runId'] == 'agr_project_history'
    assert loaded['runs'][0]['taskType'] == 'DAMPER_OPTIMIZATION'
    assert loaded['workspace']['solver'] == 'OPENSEESPY_INPROC'
    assert loaded['workspace']['loadKind'] == 'WIND'
    assert loaded['workspace']['optimizationProfile'] == 'FULL'
    assert loaded['workspaceRevision'] == 1

    updated = service.update_workspace(
        project['projectId'],
        {'modelFileName': 'STbridge.txt', 'selectedLayoutId': 'TWO_PER_TOWER'},
        expected_revision=1,
    )
    reloaded = EngineeringProjectService().get_project(project['projectId'])

    assert updated['workspaceRevision'] == 2
    assert reloaded['runCount'] == 1
    assert reloaded['workspace']['solver'] == 'OPENSEESPY_INPROC'
    assert reloaded['workspace']['modelFileName'] == 'STbridge.txt'
    assert reloaded['workspace']['selectedLayoutId'] == 'TWO_PER_TOWER'


def test_workspace_update_rejects_stale_revision_and_is_value_idempotent() -> None:
    service = EngineeringProjectService()
    project = service.create_project('Workspace concurrency')

    first = service.update_workspace(
        project['projectId'],
        {'solver': 'OPENSEESPY_INPROC'},
        expected_revision=1,
    )
    assert first['workspaceRevision'] == 2

    unchanged = service.update_workspace(
        project['projectId'],
        {'solver': 'OPENSEESPY_INPROC'},
        expected_revision=2,
    )
    assert unchanged['workspaceRevision'] == 2

    with pytest.raises(HTTPException) as exc_info:
        service.update_workspace(
            project['projectId'],
            {'solver': 'ANSYS'},
            expected_revision=1,
        )
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail['code'] == 'WORKSPACE_REVISION_CONFLICT'
    assert exc_info.value.detail['expectedRevision'] == 1
    assert exc_info.value.detail['currentRevision'] == 2


def test_existing_session_can_attach_once_but_not_to_two_projects() -> None:
    service = EngineeringProjectService()
    first = service.create_project('工程 A')
    second = service.create_project('工程 B')
    session = agent_service.create_session('已有会话')

    attached = service.attach_session(first['projectId'], session['sessionId'])
    assert attached['attached'] is True
    assert service.project_for_session(session['sessionId'])['projectId'] == first['projectId']

    with pytest.raises(HTTPException) as exc_info:
        service.attach_session(second['projectId'], session['sessionId'])
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail['code'] == 'SESSION_ALREADY_BOUND'

    with sqlite3.connect(platform_store.state_path) as connection:
        rows = connection.execute(
            'SELECT project_id FROM agent_project_sessions WHERE session_id = ?',
            (session['sessionId'],),
        ).fetchall()
    assert rows == [(first['projectId'],)]


def test_project_workspace_http_api_round_trip() -> None:
    client = TestClient(app)
    created = client.post(
        '/api/v1/agent/projects',
        json={
            'name': '桥梁工程 Workspace',
            'workspace': {
                'solver': 'ANSYS',
                'loadKind': 'EARTHQUAKE',
                'damperType': 'VISCOUS',
                'optimizationProfile': 'STANDARD',
            },
        },
    )
    assert created.status_code == 200
    project = created.json()
    project_id = project['projectId']
    assert project['workspaceRevision'] == 1

    session_response = client.post(
        f'/api/v1/agent/projects/{project_id}/sessions',
        json={'title': '工程项目内会话'},
    )
    assert session_response.status_code == 200
    session_id = session_response.json()['sessionId']

    updated = client.put(
        f'/api/v1/agent/projects/{project_id}/workspace',
        json={
            'expectedRevision': 1,
            'patch': {
                'solver': 'OPENSEESPY_INPROC',
                'loadKind': 'WIND',
                'optimizationProfile': 'FULL',
            },
        },
    )
    assert updated.status_code == 200
    assert updated.json()['workspaceRevision'] == 2

    stale = client.put(
        f'/api/v1/agent/projects/{project_id}/workspace',
        json={'expectedRevision': 1, 'patch': {'solver': 'ANSYS'}},
    )
    assert stale.status_code == 409

    loaded = client.get(f'/api/v1/agent/projects/{project_id}')
    assert loaded.status_code == 200
    body = loaded.json()
    assert body['workspace']['solver'] == 'OPENSEESPY_INPROC'
    assert body['workspace']['loadKind'] == 'WIND'
    assert body['workspace']['optimizationProfile'] == 'FULL'
    assert body['sessionCount'] == 1
    assert body['sessions'][0]['sessionId'] == session_id

    listed = client.get('/api/v1/agent/projects')
    assert listed.status_code == 200
    assert [item['projectId'] for item in listed.json()['data']] == [project_id]


def test_workspace_schema_is_strict_and_project_routes_are_registered() -> None:
    with pytest.raises(ValidationError):
        EngineeringWorkspacePatch.model_validate({'solver': 'UNKNOWN'})
    with pytest.raises(ValidationError):
        EngineeringWorkspacePatch.model_validate({'optimizationProfile': 'FULL', 'extraField': True})

    paths = {route.path for route in app.routes}
    assert '/api/v1/agent/projects' in paths
    assert '/api/v1/agent/projects/{project_id}' in paths
    assert '/api/v1/agent/projects/{project_id}/workspace' in paths
    assert '/api/v1/agent/projects/{project_id}/sessions' in paths
    assert '/api/v1/agent/projects/{project_id}/sessions/{session_id}' in paths
