from __future__ import annotations

import json

import pytest

from app.services.agent_project_repository import EngineeringProjectRepository
from app.services.agent_repository import AgentRepository
from app.services.agent_run_comparison import (
    CROSS_SOLVER,
    DIRECT,
    LIMITED,
    NOT_COMPARABLE,
    CrossRunComparisonService,
    RunComparisonError,
)
from app.services.platform_store import platform_store


def _run(
    run_id: str,
    session_id: str = 'ags_a',
    *,
    solver: str = 'ANSYS',
    owner: str = 'local',
) -> dict:
    return {
        'runId': run_id,
        'sessionId': session_id,
        'ownerId': owner,
        'goal': run_id,
        'taskType': 'ANALYSIS',
        'status': 'SUCCEEDED',
        'currentStage': 'COMPLETED',
        'workflowContract': {
            'model': 'STbridge',
            'solver': solver,
            'loadKind': 'EARTHQUAKE',
            'modelSha256': 'a' * 64,
            'loadSha256': 'b' * 64,
            'responseIds': ['max_tower_base_shear'],
        },
        'artifactIds': [f'art_{run_id}'],
        'reportArtifactId': f'art_report_{run_id}',
        'resultSummary': {'evidenceMode': 'REAL_FEM'},
        'createdAt': '2026-09-01T00:00:00Z',
        'updatedAt': '2026-09-01T00:00:01Z',
    }


def _setup(tmp_path, monkeypatch):
    state_path = tmp_path / 'agent.sqlite3'
    monkeypatch.setattr(platform_store, 'state_path', state_path)
    repository = AgentRepository(state_path)
    projects = EngineeringProjectRepository(state_path)
    for session_id in ('ags_a', 'ags_b', 'ags_other'):
        repository.save_session({
            'sessionId': session_id,
            'ownerId': 'local',
            'title': session_id,
            'status': 'ACTIVE',
            'createdAt': '2026-09-01T00:00:00Z',
            'updatedAt': '2026-09-01T00:00:00Z',
        })
    projects.create_project({
        'projectId': 'agp_compare',
        'ownerId': 'local',
        'name': 'Compare bridge',
        'description': '',
        'status': 'ACTIVE',
        'workspace': {'schemaVersion': 1},
        'workspaceRevision': 1,
        'sessionIds': ['ags_a', 'ags_b'],
        'createdAt': '2026-09-01T00:00:00Z',
        'updatedAt': '2026-09-01T00:00:00Z',
    })
    return repository


def _snapshot(
    run: dict,
    value: float,
    *,
    solver: str | None = None,
    model_sha: str | None = 'a' * 64,
    load_sha: str | None = 'b' * 64,
):
    return {
        'targetKey': run['runId'],
        'runId': run['runId'],
        'taskType': run['taskType'],
        'solver': solver or run['workflowContract']['solver'],
        'loadKind': 'EARTHQUAKE',
        'modelArtifactId': None,
        'modelSha256': model_sha,
        'modelIdentity': f'SHA256:{model_sha}' if model_sha else None,
        'loadArtifactId': None,
        'loadSha256': load_sha,
        'loadIdentity': f'SHA256:{load_sha}' if load_sha else None,
        'responseIds': ['max_tower_base_shear'],
        'reportArtifactId': run['reportArtifactId'],
        'metrics': {
            'max_tower_base_shear': {
                'value': value,
                'unit': 'N',
                'label': '塔底剪力',
                'direction': 'LOWER_IS_BETTER',
                'evidence': {'artifactId': f'art_{run["runId"]}'},
            },
        },
    }


def test_direct_comparison_computes_python_delta_and_ranking(tmp_path, monkeypatch) -> None:
    repository = _setup(tmp_path, monkeypatch)
    left, right = _run('agr_base'), _run('agr_candidate', 'ags_b')
    repository.save_run(left)
    repository.save_run(right)
    service = CrossRunComparisonService()
    snapshots = {'agr_base': _snapshot(left, 100.0), 'agr_candidate': _snapshot(right, 80.0)}
    monkeypatch.setattr(service, '_snapshot', lambda run, **_: snapshots[run['runId']])

    result = service.compare(
        repository=repository,
        session_id='ags_a',
        targets=[{'runId': 'agr_base'}, {'runId': 'agr_candidate'}],
        baseline_run_id='agr_base',
        metric_ids=['max_tower_base_shear'],
        owner='local',
        catalog_loader=lambda _run: None,
    )

    assert result['compatibility'] == DIRECT
    delta = result['comparisons'][0]['metrics']['max_tower_base_shear']
    assert delta['difference'] == -20.0
    assert delta['relativeChangePercent'] == -20.0
    assert delta['interpretation'] == 'PERFORMANCE_CHANGE'
    assert result['rankings'][0]['rows'][0]['runId'] == 'agr_candidate'


def test_cross_solver_is_validation_not_performance_ranking(tmp_path, monkeypatch) -> None:
    repository = _setup(tmp_path, monkeypatch)
    left, right = _run('agr_ansys'), _run('agr_os', 'ags_b', solver='OPENSEESPY_INPROC')
    repository.save_run(left)
    repository.save_run(right)
    service = CrossRunComparisonService()
    snapshots = {
        'agr_ansys': _snapshot(left, 100.0),
        'agr_os': _snapshot(right, 103.0, solver='OPENSEESPY_INPROC'),
    }
    monkeypatch.setattr(service, '_snapshot', lambda run, **_: snapshots[run['runId']])

    result = service.compare(
        repository=repository,
        session_id='ags_a',
        targets=[{'runId': 'agr_ansys'}, {'runId': 'agr_os'}],
        baseline_run_id='agr_ansys',
        metric_ids=['max_tower_base_shear'],
        owner='local',
        catalog_loader=lambda _run: None,
    )

    assert result['compatibility'] == CROSS_SOLVER
    assert result['rankings'] == []
    assert result['comparisons'][0]['metrics']['max_tower_base_shear']['interpretation'] == 'SOLVER_DIFFERENCE'


def test_different_model_sha_blocks_delta_and_ranking(tmp_path, monkeypatch) -> None:
    repository = _setup(tmp_path, monkeypatch)
    left, right = _run('agr_a'), _run('agr_b', 'ags_b')
    repository.save_run(left)
    repository.save_run(right)
    service = CrossRunComparisonService()
    snapshots = {
        'agr_a': _snapshot(left, 100.0),
        'agr_b': _snapshot(right, 70.0, model_sha='c' * 64),
    }
    monkeypatch.setattr(service, '_snapshot', lambda run, **_: snapshots[run['runId']])

    result = service.compare(
        repository=repository,
        session_id='ags_a',
        targets=[{'runId': 'agr_a'}, {'runId': 'agr_b'}],
        baseline_run_id='agr_a',
        metric_ids=['max_tower_base_shear'],
        owner='local',
        catalog_loader=lambda _run: None,
    )

    assert result['compatibility'] == NOT_COMPARABLE
    assert result['comparisons'][0]['metrics'] == {}
    assert result['rankings'] == []


def test_missing_identity_is_limited_and_does_not_claim_improvement(tmp_path, monkeypatch) -> None:
    repository = _setup(tmp_path, monkeypatch)
    left, right = _run('agr_a'), _run('agr_b', 'ags_b')
    repository.save_run(left)
    repository.save_run(right)
    service = CrossRunComparisonService()
    snapshots = {
        'agr_a': _snapshot(left, 100.0, model_sha=None),
        'agr_b': _snapshot(right, 80.0, model_sha=None),
    }
    monkeypatch.setattr(service, '_snapshot', lambda run, **_: snapshots[run['runId']])

    result = service.compare(
        repository=repository,
        session_id='ags_a',
        targets=[{'runId': 'agr_a'}, {'runId': 'agr_b'}],
        baseline_run_id='agr_a',
        metric_ids=['max_tower_base_shear'],
        owner='local',
        catalog_loader=lambda _run: None,
    )

    assert result['compatibility'] == LIMITED
    assert result['comparisons'][0]['metrics'] == {}


def test_registered_default_stbridge_identity_allows_direct_comparison() -> None:
    service = CrossRunComparisonService()
    left = _run('agr_a')
    right = _run('agr_b', 'ags_b')
    for run in (left, right):
        run['workflowContract'].pop('modelSha256', None)
        run['workflowContract'].pop('loadSha256', None)
    left_snapshot = service._snapshot(
        left,
        selector={'runId': 'agr_a'},
        metric_ids=[],
        catalog_loader=lambda _run: {'entries': []},
    )
    right_snapshot = service._snapshot(
        right,
        selector={'runId': 'agr_b'},
        metric_ids=[],
        catalog_loader=lambda _run: {'entries': []},
    )

    assert left_snapshot['modelIdentity'] == 'REGISTERED_MODEL:STbridge'
    assert left_snapshot['loadIdentity'] == 'REGISTERED_DEFAULT_LOAD:EARTHQUAKE'
    assert service._compatibility(left_snapshot, right_snapshot) == DIRECT


def test_project_scope_rejects_other_project_run(tmp_path, monkeypatch) -> None:
    repository = _setup(tmp_path, monkeypatch)
    repository.save_run(_run('agr_a'))
    repository.save_run(_run('agr_other', 'ags_other'))
    service = CrossRunComparisonService()

    with pytest.raises(RunComparisonError, match='当前 Project'):
        service.compare(
            repository=repository,
            session_id='ags_a',
            targets=[{'runId': 'agr_a'}, {'runId': 'agr_other'}],
            owner='local',
            catalog_loader=lambda _run: None,
        )


def test_owner_scope_rejects_foreign_run_before_unbound_project_fallback(tmp_path, monkeypatch) -> None:
    repository = _setup(tmp_path, monkeypatch)
    repository.save_run(_run('agr_a'))
    foreign = _run('agr_foreign', 'ags_other', owner='someone_else')
    repository.save_run(foreign)
    service = CrossRunComparisonService()

    monkeypatch.setattr(
        'app.services.agent_run_comparison.engineering_project_context_service.filter_runs_to_project',
        lambda runs, **_: list(runs),
    )
    with pytest.raises(RunComparisonError, match='owner'):
        service.compare(
            repository=repository,
            session_id='ags_a',
            targets=[{'runId': 'agr_a'}, {'runId': 'agr_foreign'}],
            owner='local',
            catalog_loader=lambda _run: None,
        )


def test_multi_case_run_requires_explicit_case_id(monkeypatch) -> None:
    service = CrossRunComparisonService()
    run = _run('agr_sweep')
    run['taskType'] = 'DAMPER_PARAMETER_SWEEP'
    monkeypatch.setattr(service, '_load_report', lambda _run: {
        'caseResults': [
            {'caseId': 'case_a', 'isVerifiedSolverOutput': True, 'objectives': {'max_tower_base_shear': 10.0}},
            {'caseId': 'case_b', 'isVerifiedSolverOutput': True, 'objectives': {'max_tower_base_shear': 9.0}},
        ]
    })

    with pytest.raises(RunComparisonError, match='必须明确 caseId'):
        service._selected_case(run, None)
    assert service._selected_case(run, 'case_b')['caseId'] == 'case_b'


def test_diagnostic_run_is_not_a_formal_comparison_source(tmp_path, monkeypatch) -> None:
    repository = _setup(tmp_path, monkeypatch)
    run = _run('agr_diag')
    run['resultSummary']['evidenceMode'] = 'DIAGNOSTIC_ONLY'
    repository.save_run(run)
    service = CrossRunComparisonService()

    with pytest.raises(RunComparisonError, match='SUCCEEDED \\+ REAL_FEM'):
        service._required_run(repository, 'agr_diag')


def test_optimization_comparison_uses_accepted_fem_review_values(tmp_path, monkeypatch) -> None:
    """正式优化比较必须读取最终 FEM review，不能把代理预测当成真实响应。"""
    _setup(tmp_path, monkeypatch)
    summary = {
        'optimization': {
            'objective_names': ['earthquake:max_tower_base_shear'],
            'pareto_solutions': [{
                'design_parameters': {'c': 7800.0, 'alpha': 0.8},
                'objective_values': {'earthquake:max_tower_base_shear': 48_144_942.0},
            }],
            'topsis': {'ranking': [0], 'closeness': [0.9]},
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
    }
    artifact = platform_store.register_artifact(
        kind='OPTIMIZATION_REPORT',
        name='real_optimization_summary.json',
        path='output/real/optimization_summary.json',
        mime_type='application/json',
        preview=summary,
        content=json.dumps(summary).encode('utf-8'),
    )
    run = _run('agr_opt')
    run['taskType'] = 'DAMPER_OPTIMIZATION'
    run['artifactIds'] = [artifact.artifact_id]

    metrics = CrossRunComparisonService()._optimization_metrics(
        run,
        ['max_tower_base_shear'],
        candidate_rank=1,
    )

    assert metrics['max_tower_base_shear']['value'] == 48_150_312.0
