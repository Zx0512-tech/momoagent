from __future__ import annotations

from app.services.agent_engineering import EngineeringIntent
from app.services.agent_project_context import EngineeringProjectContextService
from app.services.agent_project_repository import EngineeringProjectRepository
from app.services.agent_repository import AgentRepository
from app.services.platform_store import platform_store


def test_workspace_model_reference_is_context_only_without_response_mapping() -> None:
    intent = EngineeringIntent(
        taskType='ANALYSIS',
        solver='ANSYS',
        damperType=None,
        loadKind='EARTHQUAKE',
        selectedLayoutId=None,
        responseIds=['max_tower_base_shear'],
        modelArtifactId=None,
        responseNodes=[],
        requiresRealFem=True,
        summary='继续分析',
    )
    context = {
        'workspace': {
            'modelArtifactId': 'art_model_memory',
            'modelSha256': 'a' * 64,
            'solver': 'ANSYS',
            'loadKind': 'EARTHQUAKE',
            'responseIds': ['max_tower_base_shear'],
        }
    }

    resolved, sources = EngineeringProjectContextService().resolve_intent(
        intent,
        project_context=context,
        user_content='继续上次分析',
    )

    assert resolved.model_artifact_id is None
    assert 'modelArtifactId' not in sources


def test_relevant_run_memory_does_not_embed_numeric_result_summary(tmp_path, monkeypatch) -> None:
    state_path = tmp_path / 'agent.sqlite3'
    monkeypatch.setattr(platform_store, 'state_path', state_path)
    repository = AgentRepository(state_path)
    project_repository = EngineeringProjectRepository(state_path)
    repository.save_session({
        'sessionId': 'ags_memory',
        'ownerId': 'local',
        'title': 'memory',
        'status': 'ACTIVE',
        'createdAt': '2026-09-01T00:00:00Z',
        'updatedAt': '2026-09-01T00:00:00Z',
    })
    project_repository.create_project({
        'projectId': 'agp_memory',
        'ownerId': 'local',
        'name': 'memory',
        'description': '',
        'status': 'ACTIVE',
        'workspace': {
            'schemaVersion': 1,
            'modelArtifactId': None,
            'modelFileName': None,
            'modelSha256': None,
            'solver': 'ANSYS',
            'loadKind': 'EARTHQUAKE',
            'loadArtifactId': None,
            'loadSha256': None,
            'damperType': 'VISCOUS',
            'selectedLayoutId': 'TWO_PER_TOWER',
            'responseIds': ['max_tower_base_shear'],
            'optimizationProfile': 'FULL',
        },
        'workspaceRevision': 1,
        'sessionIds': ['ags_memory'],
        'createdAt': '2026-09-01T00:00:00Z',
        'updatedAt': '2026-09-01T00:00:00Z',
    })
    repository.save_run({
        'runId': 'agr_memory',
        'sessionId': 'ags_memory',
        'ownerId': 'local',
        'taskType': 'DAMPER_OPTIMIZATION',
        'status': 'SUCCEEDED',
        'currentStage': 'COMPLETED',
        'intent': {
            'taskType': 'DAMPER_OPTIMIZATION',
            'solver': 'ANSYS',
            'damperType': 'VISCOUS',
            'loadKind': 'EARTHQUAKE',
            'selectedLayoutId': 'TWO_PER_TOWER',
            'responseIds': ['max_tower_base_shear'],
            'optimizationProfile': 'FULL',
        },
        'workflowContract': {
            'solver': 'ANSYS',
            'loadKind': 'EARTHQUAKE',
            'damper': {'type': 'VISCOUS'},
            'selectedLayoutId': 'TWO_PER_TOWER',
            'responseIds': ['max_tower_base_shear'],
            'optimizationProfile': 'FULL',
        },
        'artifactIds': ['art_result'],
        'reportArtifactId': 'art_report',
        'resultSummary': {
            'evidenceMode': 'REAL_FEM',
            'message': '塔底剪力下降 18.5%。',
            'objectives': {'maxTowerBaseShear': 123.456},
            'recommendedObjectives': {'maxTowerBaseShear': 100.1},
            'validationStatus': 'PASSED',
        },
        'createdAt': '2026-09-01T00:00:00Z',
        'updatedAt': '2026-09-01T00:01:00Z',
    })

    context = EngineeringProjectContextService().build(
        repository=repository,
        session_id='ags_memory',
        query='继续上次优化',
    )

    assert context is not None
    summary = context['relevantRuns'][0]['resultSummary']
    assert summary == {'evidenceMode': 'REAL_FEM', 'validationStatus': 'PASSED'}
    assert '18.5' not in str(context['relevantRuns'][0])
    assert '123.456' not in str(context['relevantRuns'][0])
