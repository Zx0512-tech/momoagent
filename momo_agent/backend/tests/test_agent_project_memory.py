from __future__ import annotations

from app.services.agent_engineering import EngineeringIntent
from app.services.agent_project_context import EngineeringProjectContextService
from app.services.agent_project_repository import EngineeringProjectRepository
from app.services.agent_repository import AgentRepository
from app.services.platform_store import platform_store


def _project(project_id: str = 'agp_memory') -> dict:
    return {
        'projectId': project_id,
        'ownerId': 'local',
        'name': 'Memory bridge',
        'description': '',
        'status': 'ACTIVE',
        'workspace': {
            'schemaVersion': 1,
            'modelArtifactId': None,
            'modelFileName': None,
            'modelSha256': 'a' * 64,
            'solver': 'ANSYS',
            'loadKind': 'EARTHQUAKE',
            'loadArtifactId': 'art_load_current',
            'loadSha256': 'b' * 64,
            'damperType': 'VISCOUS',
            'selectedLayoutId': 'TWO_PER_TOWER',
            'responseIds': ['max_tower_base_shear'],
            'optimizationProfile': 'FULL',
        },
        'workspaceRevision': 1,
        'sessionIds': ['ags_a', 'ags_b'],
        'createdAt': '2026-09-01T00:00:00Z',
        'updatedAt': '2026-09-01T00:00:00Z',
    }


def _run(
    run_id: str,
    session_id: str,
    *,
    status: str = 'SUCCEEDED',
    evidence_mode: str = 'REAL_FEM',
    solver: str = 'ANSYS',
    profile: str = 'FULL',
) -> dict:
    return {
        'runId': run_id,
        'sessionId': session_id,
        'ownerId': 'local',
        'taskType': 'DAMPER_OPTIMIZATION',
        'status': status,
        'currentStage': 'COMPLETED',
        'intent': {
            'taskType': 'DAMPER_OPTIMIZATION',
            'solver': solver,
            'damperType': 'VISCOUS',
            'loadKind': 'EARTHQUAKE',
            'selectedLayoutId': 'TWO_PER_TOWER',
            'responseIds': ['max_tower_base_shear'],
            'optimizationProfile': profile,
        },
        'workflowContract': {
            'taskType': 'DAMPER_OPTIMIZATION',
            'solver': solver,
            'loadKind': 'EARTHQUAKE',
            'damper': {'type': 'VISCOUS'},
            'selectedLayoutId': 'TWO_PER_TOWER',
            'responseIds': ['max_tower_base_shear'],
            'optimizationProfile': profile,
            'modelSha256': 'a' * 64,
            'loadArtifactId': 'art_load_current',
            'loadSha256': 'b' * 64,
        },
        'artifactIds': [f'art_{run_id}'],
        'reportArtifactId': f'art_report_{run_id}',
        'resultSummary': {'evidenceMode': evidence_mode, 'message': 'verified'},
        'createdAt': '2026-09-01T00:00:00Z',
        'updatedAt': f'2026-09-01T00:0{1 if run_id.endswith("1") else 2}:00Z',
    }


def _setup(tmp_path, monkeypatch):
    state_path = tmp_path / 'agent.sqlite3'
    monkeypatch.setattr(platform_store, 'state_path', state_path)
    agent_repository = AgentRepository(state_path)
    project_repository = EngineeringProjectRepository(state_path)
    for session_id in ('ags_a', 'ags_b', 'ags_other'):
        agent_repository.save_session({
            'sessionId': session_id,
            'ownerId': 'local',
            'title': session_id,
            'status': 'ACTIVE',
            'createdAt': '2026-09-01T00:00:00Z',
            'updatedAt': '2026-09-01T00:00:00Z',
        })
    project_repository.create_project(_project())
    return agent_repository, project_repository


def test_project_memory_uses_only_verified_runs_from_same_project(tmp_path, monkeypatch) -> None:
    repository, _ = _setup(tmp_path, monkeypatch)
    repository.save_run(_run('agr_1', 'ags_a'))
    repository.save_run(_run('agr_2', 'ags_b', status='FAILED'))
    repository.save_run(_run('agr_3', 'ags_b', evidence_mode='DIAGNOSTIC'))
    repository.save_run(_run('agr_other', 'ags_other'))

    context = EngineeringProjectContextService().build(
        repository=repository,
        session_id='ags_b',
        owner='local',
        query='继续上次 FULL 优化',
        requested_task='DAMPER_OPTIMIZATION',
    )

    assert context is not None
    assert context['project']['projectId'] == 'agp_memory'
    assert [item['runId'] for item in context['relevantRuns']] == ['agr_1']
    assert context['resolvedFacts']['solver'] == {
        'value': 'ANSYS', 'source': 'PROJECT_WORKSPACE',
    }


def test_project_memory_exact_run_id_gets_highest_relevance(tmp_path, monkeypatch) -> None:
    repository, _ = _setup(tmp_path, monkeypatch)
    repository.save_run(_run('agr_1', 'ags_a'))
    repository.save_run(_run('agr_2', 'ags_b'))

    context = EngineeringProjectContextService().build(
        repository=repository,
        session_id='ags_b',
        query='继续 agr_1 的结果',
    )

    assert context is not None
    assert context['relevantRuns'][0]['runId'] == 'agr_1'
    assert context['relevantRuns'][0]['relevanceScore'] >= 100


def test_workspace_defaults_do_not_override_current_user_solver(tmp_path, monkeypatch) -> None:
    _setup(tmp_path, monkeypatch)
    service = EngineeringProjectContextService()
    repository = AgentRepository(platform_store.state_path)
    context = service.build(repository=repository, session_id='ags_a', query='换成 OpenSees 再算')
    intent = EngineeringIntent(
        taskType='DAMPER_OPTIMIZATION',
        solver='OPENSEESPY_INPROC',
        damperType='VISCOUS',
        loadKind=None,
        selectedLayoutId=None,
        responseIds=[],
        optimizationProfile='STANDARD',
        requiresRealFem=True,
        summary='test',
    )

    resolved, sources = service.resolve_intent(
        intent,
        project_context=context,
        user_content='换成 OpenSees 再算',
    )

    assert resolved.solver == 'OPENSEESPY_INPROC'
    assert sources['solver'] == 'USER_SPECIFIED'
    assert resolved.load_kind == 'EARTHQUAKE'
    assert sources['loadKind'] == 'PROJECT_WORKSPACE'
    assert resolved.selected_layout_id == 'TWO_PER_TOWER'
    assert resolved.response_ids == ['max_tower_base_shear']
    assert resolved.optimization_profile == 'FULL'


def test_verified_run_writeback_updates_workspace_once(tmp_path, monkeypatch) -> None:
    _, project_repository = _setup(tmp_path, monkeypatch)
    run = _run('agr_1', 'ags_a', solver='OPENSEESPY_INPROC', profile='STANDARD')
    service = EngineeringProjectContextService()

    updated = service.write_back_verified_run(run)
    assert updated is not None
    assert updated['workspace']['solver'] == 'OPENSEESPY_INPROC'
    assert updated['workspace']['optimizationProfile'] == 'STANDARD'
    assert updated['workspaceRevision'] == 2

    service.write_back_verified_run(run)
    persisted = project_repository.get_project('agp_memory')
    assert persisted is not None
    assert persisted['workspaceRevision'] == 2


def test_failed_or_diagnostic_run_never_writes_workspace(tmp_path, monkeypatch) -> None:
    _, project_repository = _setup(tmp_path, monkeypatch)
    service = EngineeringProjectContextService()

    assert service.write_back_verified_run(_run('agr_failed', 'ags_a', status='FAILED')) is None
    assert service.write_back_verified_run(_run('agr_diag', 'ags_a', evidence_mode='DIAGNOSTIC')) is None
    persisted = project_repository.get_project('agp_memory')
    assert persisted is not None
    assert persisted['workspaceRevision'] == 1
    assert persisted['workspace']['solver'] == 'ANSYS'


def test_bound_project_filters_owner_global_runs(tmp_path, monkeypatch) -> None:
    _setup(tmp_path, monkeypatch)
    runs = [_run('agr_a', 'ags_a'), _run('agr_other', 'ags_other')]

    scoped = EngineeringProjectContextService().filter_runs_to_project(
        runs,
        session_id='ags_b',
        owner='local',
    )

    assert [item['runId'] for item in scoped] == ['agr_a']
