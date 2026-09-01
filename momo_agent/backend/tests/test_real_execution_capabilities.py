from __future__ import annotations

import json

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.services.platform_store import PlatformStore, PreparedEarthquakeWorkflow
from app.services.real_execution import real_execution_registry
from app.services.real_execution.contracts import RealJobRequest


def test_real_job_request_is_strict_and_alias_safe() -> None:
    request = RealJobRequest.model_validate({
        'jobType': 'SOLVER_BATCH',
        'source': 'AGENT',
        'inputArtifactIds': ['artifact_model'],
        'frozenConfig': {'scenario': 'EARTHQUAKE'},
        'idempotencyKey': 'sha256:example',
    })

    assert request.job_type == 'SOLVER_BATCH'
    assert request.input_artifact_ids == ['artifact_model']
    with pytest.raises(ValidationError):
        RealJobRequest.model_validate({
            'jobType': 'SOLVER_BATCH',
            'source': 'AGENT',
            'idempotencyKey': 'sha256:example',
            'unexpected': True,
        })


def test_registry_keeps_controlled_live_and_standalone_fail_closed() -> None:
    controlled = real_execution_registry.resolve(
        'SOLVER_BATCH',
        {'runMode': 'REAL_AGENT_ANALYSIS'},
    )
    standalone = real_execution_registry.resolve('SOLVER_BATCH', {})
    surrogate = real_execution_registry.resolve('SURROGATE_TRAINING', {})

    assert controlled.status == 'LIVE'
    assert controlled.mode == 'CONTROLLED_AGENT'
    assert standalone.status == 'DISABLED'
    assert surrogate.status == 'DISABLED'
    assert real_execution_registry.is_live('SOLVER_BATCH', {'runMode': 'REAL_AGENT_ANALYSIS'})
    assert not real_execution_registry.is_live('SOLVER_BATCH', {})


def test_registry_resolves_agent_only_capabilities_to_controlled_handlers() -> None:
    for job_type in ('ANALYSIS', 'DAMPER_COMPARISON', 'DAMPER_OPTIMIZATION', 'RESULT_INQUIRY'):
        capability = real_execution_registry.resolve(job_type, {'source': 'AGENT'})
        assert capability.mode == 'CONTROLLED_AGENT'
        assert capability.status == 'LIVE'

    retired = real_execution_registry.resolve('FULL_OPTIMIZATION', {'source': 'AGENT'})
    assert retired.mode == 'PLATFORM_API'
    assert retired.status == 'DISABLED'
    assert retired.handler == 'unregistered'


def test_capability_api_exposes_handler_status_and_unlock_requirements() -> None:
    with TestClient(app) as client:
        response = client.get('/api/v1/capabilities')

    assert response.status_code == 200
    payload = response.json()
    assert payload['version'] == '1.0.0'
    entries = {(item['jobType'], item['mode']): item for item in payload['data']}
    assert entries[('ANALYSIS', 'CONTROLLED_AGENT')]['status'] == 'LIVE'
    # 车流在两个求解器上都放行：ANSYS 走 TABLE，OpenSees 走逐节点 mapping，
    # 两侧读同一份 163 列矩阵制品。
    assert entries[('ANALYSIS', 'CONTROLLED_AGENT')]['solverScenarios'] == {
        'ANSYS': ['EARTHQUAKE', 'WIND', 'TRAFFIC'],
        'OPENSEESPY_INPROC': ['EARTHQUAKE', 'WIND', 'TRAFFIC'],
    }
    assert entries[('SOLVER_BATCH', 'CONTROLLED_AGENT')]['solverScenarios']['ANSYS'] == [
        'EARTHQUAKE',
        'WIND',
        'TRAFFIC',
    ]
    assert entries[('SOLVER_BATCH', 'CONTROLLED_AGENT')]['solverScenarios']['OPENSEESPY_INPROC'] == [
        'EARTHQUAKE',
        'WIND',
        'TRAFFIC',
    ]
    assert entries[('RESULT_INQUIRY', 'CONTROLLED_AGENT')]['status'] == 'LIVE'
    assert entries[('SOLVER_BATCH', 'PLATFORM_API')]['status'] == 'DISABLED'
    assert entries[('SOLVER_BATCH', 'PLATFORM_API')]['handler'] == 'solver.batch'
    assert entries[('SURROGATE_TRAINING', 'PLATFORM_API')]['unlockRequirements']
    assert entries[('ACTIVE_LEARNING', 'PLATFORM_API')]['status'] == 'DISABLED'
    assert entries[('MULTI_OBJECTIVE_OPTIMIZATION', 'PLATFORM_API')]['status'] == 'DISABLED'


def test_live_platform_mode_fails_closed_for_mock_only_entries(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv('MOMO_PLATFORM_MODE', 'LIVE')
    store = PlatformStore(state_path=tmp_path / 'state.sqlite3')
    initial_job_count = len(store.jobs)

    with pytest.raises(HTTPException) as error:
        store.create_job(
            'EXPERIMENT_DESIGN',
            {
                'scenarioType': 'EARTHQUAKE',
                'executionGoal': 'BATCH_CALCULATION',
                'variables': [{'name': 'c', 'enabled': True}],
                'responseTargets': ['beamEndDisplacement'],
            },
        )

    assert error.value.status_code == 501
    assert error.value.detail['code'] == 'CAPABILITY_NOT_IMPLEMENTED'
    assert len(store.jobs) == initial_job_count


def test_real_topsis_replays_accepted_optimization_artifact(tmp_path) -> None:
    store = PlatformStore(state_path=tmp_path / 'state.sqlite3')
    summary = {
        'optimization': {
            'objective_names': ['earthquake:displacement', 'earthquake:shear'],
            'pareto_solutions': [
                {
                    'design_parameters': {'c': 7600.0, 'alpha': 0.8},
                    'objective_values': {
                        'earthquake:displacement': 0.12,
                        'earthquake:shear': 4.0,
                    },
                    'selected': True,
                },
                {
                    'design_parameters': {'c': 8200.0, 'alpha': 0.7},
                    'objective_values': {
                        'earthquake:displacement': 0.14,
                        'earthquake:shear': 4.5,
                    },
                    'selected': False,
                },
            ],
            'topsis': {
                'best_index': 0,
                'ranking': [0, 1],
                'weights': [0.6, 0.4],
                'closeness': [0.91, 0.33],
            },
        },
        'review_status': {'all_verified_execution': True, 'all_accepted': True},
        'review_records': [
            {
                'candidate': {'pareto_index': 0},
                'accepted': True,
                'verified_execution': True,
            },
        ],
        'objective_limits': {'earthquake:shear': 4.2},
        'objective_limit_relative_tolerance': 0.0,
        'decision_source': 'surrogate_fem_review_accepted',
    }
    artifact = store.register_artifact(
        kind='OPTIMIZATION_REPORT',
        name='real_optimization_summary.json',
        path='output/real/real_optimization_summary.json',
        mime_type='application/json',
        preview=summary,
        content=json.dumps(summary, ensure_ascii=False).encode('utf-8'),
    )
    job = store.create_job('PROJECT_GATE_FAST', {})
    job.job_id = 'opt_real_001'
    job.type = 'MULTI_OBJECTIVE_OPTIMIZATION'
    job.status = 'SUCCEEDED'
    job.result = {
        'mode': 'real_baseline_optimization',
        'finalRecommendationStatus': 'ACCEPTED',
        'optimizationSummaryArtifactId': artifact.artifact_id,
    }
    job.artifacts = [artifact]

    result = store.real_topsis_result('opt_real_001')

    assert result['executionMode'] == 'REAL'
    assert result['simulation'] is False
    assert result['sourceArtifactId'] == artifact.artifact_id
    assert result['candidates'][0]['topsisScore'] == 0.91
    assert result['candidates'][0]['rank'] == 1
    assert result['candidates'][0]['femReviewed'] is True
    assert result['candidates'][1]['femReviewed'] is False
    assert result['candidates'][0]['constraintsPassed'] is True
    assert result['candidates'][1]['constraintsPassed'] is False
    assert result['recommendation']['candidateId'] == 'pareto_0'
    assert result['objectiveWeights'] == {'earthquake:displacement': 0.6, 'earthquake:shear': 0.4}
    assert result['entropyWeights'] == result['objectiveWeights']


def test_real_topsis_rejects_diagnostic_optimization(tmp_path) -> None:
    store = PlatformStore(state_path=tmp_path / 'state.sqlite3')
    job = store.create_job('PROJECT_GATE_FAST', {})
    job.job_id = 'opt_diagnostic_001'
    job.type = 'MULTI_OBJECTIVE_OPTIMIZATION'
    job.status = 'SUCCEEDED'
    job.result = {
        'mode': 'real_baseline_optimization',
        'finalRecommendationStatus': 'DIAGNOSTIC_NOT_ACCEPTED',
    }

    with pytest.raises(HTTPException) as error:
        store.real_topsis_result('opt_diagnostic_001')

    assert error.value.status_code == 422
    assert error.value.detail['code'] == 'OPTIMIZATION_NOT_ACCEPTED'


def test_real_training_dataset_fails_closed_when_doe_result_is_missing(tmp_path) -> None:
    store = PlatformStore(state_path=tmp_path / 'state.sqlite3')
    prepared = PreparedEarthquakeWorkflow(
        workflow_config_path=tmp_path / 'workflow.json',
        baseline_config_path=tmp_path / 'baseline.json',
        optimization_config_path=tmp_path / 'optimization.json',
        source_workflow_config_path=tmp_path / 'source.json',
        output_dir=tmp_path / 'run',
        doe_designs=[{'c': 5000.0, 'alpha': 0.6}, {'c': 6000.0, 'alpha': 0.7}],
        requested_doe_count=2,
        doe_design_sha256='a' * 64,
        solver='OPENSEESPY_INPROC',
        solver_parallel={},
    )

    with pytest.raises(HTTPException) as error:
        store._register_real_optimization_datasets(
            run_dir=prepared.output_dir,
            optimization_summary={
                'doe_designs': [
                    {
                        'design': [5000.0, 0.6],
                        'design_parameters': {'c': 5000.0, 'alpha': 0.6},
                        'analysis_results': [],
                    },
                    {
                        'design': [6000.0, 0.7],
                        'design_parameters': {'c': 6000.0, 'alpha': 0.7},
                        'analysis_results': [
                            {'case_id': 'case_2', 'status': 'completed'},
                        ],
                    },
                ],
            },
            execution_evidence={},
            prepared_workflow=prepared,
        )

    assert error.value.status_code == 422
    assert error.value.detail['code'] == 'REAL_DOE_DATASET_UNVERIFIED'


def test_real_training_dataset_includes_active_learning_records(tmp_path) -> None:
    store = PlatformStore(state_path=tmp_path / 'state.sqlite3')
    prepared = PreparedEarthquakeWorkflow(
        workflow_config_path=tmp_path / 'workflow.json',
        baseline_config_path=tmp_path / 'baseline.json',
        optimization_config_path=tmp_path / 'optimization.json',
        source_workflow_config_path=tmp_path / 'source.json',
        output_dir=tmp_path / 'run',
        doe_designs=[{'c': 5000.0, 'alpha': 0.6}, {'c': 6000.0, 'alpha': 0.7}],
        requested_doe_count=2,
        doe_design_sha256='a' * 64,
        solver='OPENSEESPY_INPROC',
        solver_parallel={},
    )
    summary = {
        'doe_designs': [
            {
                'design': [5000.0, 0.6],
                'design_parameters': {'c': 5000.0, 'alpha': 0.6},
                'analysis_results': [{'case_id': 'doe_1', 'status': 'completed'}],
            },
            {
                'design': [6000.0, 0.7],
                'design_parameters': {'c': 6000.0, 'alpha': 0.7},
                'analysis_results': [{'case_id': 'doe_2', 'status': 'completed'}],
            },
        ],
        'active_learning_status': {'record_count': 1},
        'active_learning_records': [
            {
                'design': [5500.0, 0.65],
                'design_parameters': {'c': 5500.0, 'alpha': 0.65},
                'analysis_results': [{'case_id': 'active_1', 'status': 'completed'}],
            },
        ],
    }
    summary_path = tmp_path / 'optimization_summary.json'
    summary_path.write_text(json.dumps(summary), encoding='utf-8')

    evidence = store._optimization_execution_evidence(
        optimization_summary_path=summary_path,
        prepared_workflow=prepared,
    )
    _, training_artifact = store._register_real_optimization_datasets(
        run_dir=prepared.output_dir,
        optimization_summary=summary,
        execution_evidence=evidence,
        prepared_workflow=prepared,
    )
    training_preview = store.get_artifact(training_artifact.artifact_id).preview

    assert evidence['actualInitialDoeCount'] == 2
    assert evidence['activeLearningAddedCount'] == 1
    assert evidence['trainingSampleCount'] == 3
    assert training_preview['trainingSampleCount'] == 3
    assert [row['sampleSource'] for row in training_preview['rows']] == [
        'INITIAL_DOE',
        'INITIAL_DOE',
        'ACTIVE_LEARNING',
    ]
    assert training_preview['trainingDatasetSha256'] == evidence['trainingDatasetSha256']


def test_real_optimization_evidence_reports_zero_actual_doe_when_summary_is_empty(tmp_path) -> None:
    store = PlatformStore(state_path=tmp_path / 'state.sqlite3')
    prepared = PreparedEarthquakeWorkflow(
        workflow_config_path=tmp_path / 'workflow.json',
        baseline_config_path=tmp_path / 'baseline.json',
        optimization_config_path=tmp_path / 'optimization.json',
        source_workflow_config_path=tmp_path / 'source.json',
        output_dir=tmp_path / 'run',
        doe_designs=[{'c': 5000.0, 'alpha': 0.6}, {'c': 6000.0, 'alpha': 0.7}],
        requested_doe_count=2,
        doe_design_sha256='a' * 64,
        solver='OPENSEESPY_INPROC',
        solver_parallel={},
    )

    evidence = store._optimization_execution_evidence(
        optimization_summary_path=tmp_path / 'missing.json',
        prepared_workflow=prepared,
    )

    assert evidence['actualInitialDoeCount'] == 0
    assert evidence['trainingSampleCount'] == 0


def test_mock_standalone_jobs_label_simulation_and_reject_placeholder_bytes(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv('MOMO_PLATFORM_MODE', 'MOCK')
    store = PlatformStore(state_path=tmp_path / 'state.sqlite3')
    job = store.create_job(
        'EXPERIMENT_DESIGN',
        {
            'scenarioType': 'EARTHQUAKE',
            'executionGoal': 'OPTIMIZATION_RECOMMENDATION',
            'caseSetPrefix': 'doe_mock',
            'variables': [{'id': 'c', 'min': 1, 'max': 2, 'enabled': True}],
            'responseTargets': ['beamEndDisplacement'],
            'sampling': {'lhsSamples': 4},
        },
    )

    assert job.status == 'SUCCEEDED'
    assert job.result['executionMode'] == 'MOCK'
    assert job.result['simulation'] is True
    assert 'PENDING_REPLACEMENT' not in str(job.result)
    for artifact in job.artifacts:
        record = store.get_artifact(artifact.artifact_id)
        assert b'surrogate-model-placeholder' not in (record.content or b'')
        assert b'doe-surrogate-model-placeholder' not in (record.content or b'')
        assert b'PENDING_REPLACEMENT' not in (record.content or b'')


def test_live_create_job_still_rejects_standalone_placeholder_capabilities(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv('MOMO_PLATFORM_MODE', 'LIVE')
    store = PlatformStore(state_path=tmp_path / 'state.sqlite3')
    with pytest.raises(HTTPException) as error:
        store.create_job('SURROGATE_TRAINING', {
            'datasetSourceMode': 'DOE_DATASET',
            'datasetId': 'dataset_not_used',
            'modelFamilies': ['GPR'],
            'targetMetricIds': ['metric_beam_end_ux_peak'],
        })
    assert error.value.status_code == 501
    assert error.value.detail['code'] == 'CAPABILITY_NOT_IMPLEMENTED'
