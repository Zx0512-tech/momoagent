import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api.v1.schemas import (
    MultiObjectiveOptimizationRequest,
    SurrogateTrainingRequest,
    TrafficLoadRequest,
    validate_job_params,
)
from app.main import app
from app.services.platform_store import PlatformStore, platform_store


SAMPLE_WIND_PAYLOAD = {
    'project': {'name': '示例桥梁', 'note': 'v1 wind wrapper'},
    'site': {
        'u10': 30.0,
        'riskCoefficient': 1.0,
        'surfaceClass': 'B',
        'terrainCoefficient': 1.0,
        'airDensity': 1.25,
        'girderReferenceHeight': 50.0,
        'towerHeights': [10.0, 40.0, 80.0],
    },
    'girder': {
        'length': 1200.0,
        'segmentCount': 8,
        'width': 35.0,
        'depth': 4.5,
        'ch': 1.2,
        'cv': 0.8,
        'cm': 0.15,
    },
    'tower': {'height': 120.0, 'segmentCount': 6, 'width': 8.0, 'cd': 1.1},
    'timeHistory': {
        'duration': 20.0,
        'timeStep': 1.0,
        'frequencyCount': 64,
        'seed': 7,
        'spectrumModel': 'davenport',
        'verticalSpectrumModel': 'panofsky',
        'simulationMethod': 'harmonic',
        'arOrder': 4,
    },
    'overrides': {'enabled': False},
}


SAMPLE_EXPERIMENT_DESIGN_PAYLOAD = {
    'bridgeId': 'stbridge',
    'designName': 'user300_damper_training_doe',
    'solver': 'ANSYS',
    'caseSetPrefix': 'doe_user300',
    'scenarioType': 'EARTHQUAKE',
    'executionGoal': 'BATCH_CALCULATION',
    'damper': {
        'enabled': True,
        'elementType': 'USER300',
        'material': {'materialType': 'VISCOUS'},
        'placement': {
            'layoutId': 'CUSTOM_NODE_PAIRS',
            'southTowerCount': 2,
            'northTowerCount': 2,
            'connectionNodePairIds': ['north_tower_36_517', 'south_tower_107_520'],
            'connectionNodePairs': [
                {'id': 'north_tower_36_517', 'tower': 'NORTH', 'nodeI': 36, 'nodeJ': 517},
                {'id': 'south_tower_107_520', 'tower': 'SOUTH', 'nodeI': 107, 'nodeJ': 520},
            ],
        },
    },
    'variables': [
        {'id': 'dampingCoefficient', 'label': '阻尼系数 c', 'unit': 'kN*s/m', 'min': 600, 'max': 2400, 'enabled': True},
        {'id': 'velocityExponent', 'label': '速度指数 alpha', 'unit': '-', 'min': 0.2, 'max': 0.8, 'enabled': True},
    ],
    'sampling': {'lhsSamples': 4, 'includeCorners': True, 'includeCenter': True, 'seed': 42},
    'responseTargets': ['beamEndDisplacement', 'towerBaseShear', 'towerBaseMoment'],
    'resources': {'processCount': 1, 'coresPerProcess': 1, 'executionTimeoutS': 7200},
}


SAMPLE_COMMAND_STREAM_PAYLOAD = {
    'solver': 'ANSYS',
    'bridgeId': 'stbridge',
    'caseSetId': 'case_user300_001',
    'moduleConfig': {
        'modules': ['MODEL', 'DAMPER', 'LOAD_EARTHQUAKE', 'TRANSIENT', 'POSTPROCESS'],
        'damper': {
            'enabled': True,
            'elementType': 'USER300',
            'material': {'materialType': 'VISCOUS', 'dampingCoefficient': 1200, 'velocityExponent': 0.35},
            'placement': {
                'layoutId': 'CUSTOM_NODE_PAIRS',
                'southTowerCount': 2,
                'northTowerCount': 2,
                'connectionNodePairIds': ['south_tower_36_481', 'north_tower_36_518'],
                'connectionNodePairs': [
                    {'id': 'north_tower_36_517', 'tower': 'NORTH', 'nodeI': 36, 'nodeJ': 517},
                    {'id': 'south_tower_107_520', 'tower': 'SOUTH', 'nodeI': 107, 'nodeJ': 520},
                ],
            },
        },
        'responseTargets': ['beamEndDisplacement', 'towerBaseShear', 'towerBaseMoment'],
    },
}


def test_solver_job_materializes_default_execution_timeout() -> None:
    validated = validate_job_params('SOLVER_BATCH', {'caseSetId': 'default_timeout'})

    assert validated['resources']['executionTimeoutS'] == 7200


SAMPLE_ENGINEERING_CONFIG = {
    'projectConfig': {
        'projectName': 'MOMO Live',
        'modelFile': {
            'fileName': 'STbridge.txt',
            'format': 'APDL',
            'parseStatus': 'PARSED',
        },
    },
    'globalTaskConfig': {'solver': 'ANSYS', 'scenario': 'EARTHQUAKE', 'executionTarget': 'OPTIMIZATION_DECISION'},
    'damperBaseConfig': {
        'placementValidated': True,
        'connectionNodePairs': [
            {'id': 'north_custom_1', 'tower': 'NORTH', 'nodeI': 36, 'nodeJ': 517},
            {'id': 'south_custom_1', 'tower': 'SOUTH', 'nodeI': 107, 'nodeJ': 520},
        ],
        'damperInstanceRegistry': [{'damperId': 'DMP-1', 'elementType': 'USER300'}],
    },
    'loadConfig': {
        'earthquake': {'configured': True, 'validated': True},
        'wind': {'configured': False, 'validated': False},
        'traffic': {'configured': False, 'validated': False},
    },
    'doeConfig': {
        'variables': [{'id': 'dampingCoefficient', 'enabled': True}, {'id': 'velocityExponent', 'enabled': True}],
        'sampleCount': 12,
        'validated': True,
    },
    'solverBatchConfig': {
        'processCount': 1,
        'coresPerProcess': 1,
        'batchMode': 'SERIAL',
        'validated': True,
    },
    'resultExtractionConfig': {
        'structuralMetrics': [{'id': 'metric_beam_end_ux_peak', 'enabled': True}],
        'autoDamperMetrics': [{'id': 'DMP-1_force', 'enabled': True}],
        'validated': True,
    },
    'surrogateLearningConfig': {
        'modelFamilies': ['GPR', 'SVR'],
        'outputResponseIds': ['beamEndDisplacement', 'towerBaseShear'],
        'validated': True,
    },
    'optimizationDecisionConfig': {
        'objectives': [{'name': 'beamEndDisplacement', 'direction': 'MIN'}],
        'decisionMethods': ['TOPSIS'],
        'validated': True,
    },
}


def create_doe_dataset(client: TestClient) -> str:
    response = client.post('/api/v1/experiment-designs', json=SAMPLE_EXPERIMENT_DESIGN_PAYLOAD)
    assert response.status_code == 200
    return response.json()['result']['datasetId']


def imported_dataset_schema() -> dict[str, object]:
    return {
        'featureColumns': ['dampingCoefficient', 'velocityExponent'],
        'targetColumns': {'metric_beam_end_ux_peak': 'beam_end_displacement'},
    }


def test_multi_objective_modes_match_the_solver_objective_contract() -> None:
    overall = MultiObjectiveOptimizationRequest.model_validate({
        'objectiveMode': 'OVERALL',
        'objectives': [
            {'name': 'beamEndDisplacement', 'direction': 'MIN'},
            {'name': 'towerBaseShear', 'direction': 'MIN'},
            {'name': 'towerBaseMoment', 'direction': 'MIN'},
            {'name': 'beamEndCumulativeDisplacement', 'direction': 'MIN'},
        ],
    })
    assert overall.objective_mode == 'OVERALL'

    operation = MultiObjectiveOptimizationRequest.model_validate({
        'objectiveMode': 'OPERATION',
        'objectives': [{'name': 'beamEndCumulativeDisplacement', 'direction': 'MIN'}],
    })
    assert [objective.name for objective in operation.objectives] == ['beamEndCumulativeDisplacement']

    with pytest.raises(ValueError, match='OPERATION objectives must exactly match'):
        MultiObjectiveOptimizationRequest.model_validate({
            'objectiveMode': 'OPERATION',
            'objectives': [
                {'name': 'beamEndCumulativeDisplacement', 'direction': 'MIN'},
                {'name': 'damperCost', 'direction': 'MIN'},
            ],
        })


def test_surrogate_training_rejects_undefined_damper_energy_target() -> None:
    with pytest.raises(ValueError, match='unsupported surrogate targetMetricIds'):
        SurrogateTrainingRequest.model_validate({
            'datasetSourceMode': 'DOE_DATASET',
            'datasetId': 'dataset_demo',
            'modelFamilies': ['GPR'],
            'targetMetricIds': ['ND01_energy'],
        })


def test_v1_health_dashboard_jobs_and_artifacts() -> None:
    client = TestClient(app)

    health = client.get('/api/v1/health')
    assert health.status_code == 200
    assert health.json()['status'] == 'OK'
    assert 'now' in health.json()

    preflight = client.options(
        '/api/v1/health',
        headers={
            'Origin': 'http://localhost:5173',
            'Access-Control-Request-Method': 'GET',
        },
    )
    assert preflight.status_code == 200
    assert preflight.headers['access-control-allow-origin'] == 'http://localhost:5173'

    summary = client.get('/api/v1/dashboard/summary')
    assert summary.status_code == 200
    body = summary.json()
    assert body['repo']['branch'] == 'main'
    assert 'latestGate' in body

    jobs = client.get('/api/v1/jobs')
    assert jobs.status_code == 200
    assert jobs.json()['pagination']['totalItems'] >= 1

    artifacts = client.get('/api/v1/artifacts')
    assert artifacts.status_code == 200
    first_artifact = artifacts.json()['data'][0]
    preview = client.get(f"/api/v1/artifacts/{first_artifact['artifactId']}/preview")
    assert preview.status_code == 200
    download = client.get(f"/api/v1/artifacts/{first_artifact['artifactId']}/download")
    assert download.status_code == 200
    assert download.content


def test_live_dashboard_summary_does_not_report_mock_runtime_as_fact(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv('MOMO_PLATFORM_MODE', 'LIVE')
    store = PlatformStore(state_path=tmp_path / 'state.sqlite3')

    summary = store.dashboard_summary()

    assert summary['repo']['status'] == 'UNKNOWN'
    assert summary['latestGate']['status'] in {'PASS', 'FAIL', 'UNKNOWN'}
    assert summary['latestParity']['status'] == 'UNKNOWN'
    assert summary['runtime']['status'] == 'UNKNOWN'
    assert 'MOCK_HANDLER' not in json.dumps(summary)


def test_v1_readiness_reports_required_runtime_components() -> None:
    with TestClient(app) as client:
        response = client.get('/api/v1/readiness', headers={'X-Request-ID': 'readiness-test'})

    assert response.status_code == 200
    assert response.headers['X-Request-ID'] == 'readiness-test'
    body = response.json()
    assert body['status'] == 'READY'
    assert body['components']['database']['required'] is True
    assert body['components']['artifact_storage']['required'] is True
    assert body['components']['dispatcher']['status'] == 'PASS'


def test_v1_create_job_generates_artifact_and_preview() -> None:
    client = TestClient(app)

    response = client.post(
        '/api/v1/jobs',
        json={
            'type': 'RESULT_EXTRACTION',
            'params': {'solverRunId': 'run_example', 'extractors': ['summary']},
        },
    )
    assert response.status_code == 200
    job = response.json()
    assert job['status'] == 'SUCCEEDED', job
    assert job['artifacts']

    listed = client.get('/api/v1/artifacts', params={'jobId': job['jobId']})
    assert listed.status_code == 200
    assert listed.json()['pagination']['totalItems'] == len(job['artifacts'])


def test_v1_preflight_requires_config_path_and_returns_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    client = TestClient(app)
    monkeypatch.setattr(
        'app.services.platform_store.preflight_config',
        lambda _: {
            'solver': 'ANSYS',
            'execution_mode': 'run',
            'solver_capability': {'not_implemented': []},
            'path_checks': {
                'config': {'path': 'config.json', 'exists': True},
                'solver_kwargs.mapdl_executable': {'path': 'ansys.exe', 'exists': True},
            },
        },
    )

    response = client.post(
        '/api/v1/preflights',
        json={'configPath': 'docs/examples/templates/solver_parity_baseline_workflow_template.json'},
    )
    assert response.status_code == 200
    body = response.json()
    assert body['status'] == 'PASS'
    assert body['configPath'] == 'docs/examples/templates/solver_parity_baseline_workflow_template.json'
    assert body['pathChecks'][0]['exists'] is True

    for payload in ({}, {'configPath': ''}):
        invalid = client.post('/api/v1/preflights', json=payload)
        assert invalid.status_code == 422
        assert invalid.json()['error']['code'] == 'VALIDATION_ERROR'


def test_v1_preflight_fails_closed_for_missing_invalid_and_unavailable_solver(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = TestClient(app)

    missing = client.post('/api/v1/preflights', json={'configPath': str(tmp_path / 'missing.json')})
    assert missing.status_code == 200
    assert missing.json()['status'] == 'FAIL'
    assert missing.json()['reasons'][0]['code'] == 'CONFIG_NOT_FOUND'

    invalid = tmp_path / 'invalid.json'
    invalid.write_text('{not-json', encoding='utf-8')
    invalid_response = client.post('/api/v1/preflights', json={'configPath': str(invalid)})
    assert invalid_response.status_code == 200
    assert invalid_response.json()['status'] == 'FAIL'
    assert invalid_response.json()['reasons'][0]['code'] == 'INVALID_CONFIG_JSON'

    invalid_schema = tmp_path / 'invalid-schema.json'
    invalid_schema.write_text('{}', encoding='utf-8')
    invalid_schema_response = client.post(
        '/api/v1/preflights',
        json={'configPath': str(invalid_schema)},
    )
    assert invalid_schema_response.status_code == 200
    assert invalid_schema_response.json()['status'] == 'FAIL'
    assert invalid_schema_response.json()['reasons'][0]['code'] == 'INVALID_CONFIG_SCHEMA'

    monkeypatch.setattr(
        'app.services.platform_store.preflight_config',
        lambda _: {
            'solver': 'ANSYS',
            'execution_mode': 'run',
            'solver_capability': {'not_implemented': []},
            'path_checks': {
                'config': {'path': 'config.json', 'exists': True},
                'solver_kwargs.mapdl_executable': {'path': 'missing-ansys.exe', 'exists': False},
            },
        },
    )
    unavailable = client.post('/api/v1/preflights', json={'configPath': str(invalid)})
    assert unavailable.status_code == 200
    assert unavailable.json()['status'] == 'FAIL'
    assert unavailable.json()['reasons'][0]['code'] == 'REQUIRED_PATH_UNAVAILABLE'

    monkeypatch.setattr(
        'app.services.platform_store.preflight_config',
        lambda _: {
            'solver': 'OPENSEESPY_INPROC',
            'execution_mode': 'run',
            'solver_capability': {'available': False},
            'path_checks': {'config': {'path': 'config.json', 'exists': True}},
        },
    )
    unavailable_capability = client.post('/api/v1/preflights', json={'configPath': str(invalid)})
    assert unavailable_capability.status_code == 200
    assert unavailable_capability.json()['status'] == 'FAIL'
    assert unavailable_capability.json()['reasons'][0]['code'] == 'SOLVER_UNAVAILABLE'


def test_v1_dashboard_workflow_job_generates_summary_artifact() -> None:
    client = TestClient(app)
    response = client.post(
        '/api/v1/jobs',
        json={
            'type': 'MULTI_OBJECTIVE_OPTIMIZATION',
            'params': {
                'projectName': 'MOMO Live',
                'modelFileName': 'STbridge.txt',
                'solver': 'ANSYS',
                'scenario': 'EARTHQUAKE',
                'executionTarget': 'OPTIMIZATION_DECISION',
                'requiredModules': [
                    'DAMPER_BASE',
                    'DOE',
                    'LOADS',
                    'SOLVER_BATCH',
                    'RESULT_EXTRACTION',
                    'SURROGATE_LEARNING',
                    'OPTIMIZATION_DECISION',
                ],
            },
        },
    )

    assert response.status_code == 200
    job = response.json()
    assert job['status'] == 'SUCCEEDED'
    assert job['result']['mode'] == 'workflow_summary'
    assert job['result']['executionTarget'] == 'OPTIMIZATION_DECISION'
    assert job['result']['requiredModuleCount'] == 7
    assert job['artifacts'][0]['kind'] == 'JSON_SUMMARY'

    preview = client.get(f"/api/v1/artifacts/{job['artifacts'][0]['artifactId']}/preview")
    assert preview.status_code == 200
    summary = preview.json()
    assert summary['projectName'] == 'MOMO Live'
    assert summary['modelFileName'] == 'STbridge.txt'
    assert summary['executionMode'] == 'MOCK'
    assert summary['simulation'] is True
    assert 'PENDING_REPLACEMENT' not in str(summary)


def test_v1_dashboard_can_start_real_baseline_optimization_workflow(monkeypatch, tmp_path) -> None:
    client = TestClient(app)
    monkeypatch.setattr(
        'app.services.platform_store.EARTHQUAKE_WORKFLOW_OUTPUT_ROOT',
        tmp_path / 'real_workflows',
    )

    def fake_run(workflow_config_path, *, execution_timeout_s):
        workflow_config_path = Path(workflow_config_path)
        assert workflow_config_path.name == 'ansys_run_joint_baseline_workflow_template.json'
        assert execution_timeout_s == 1200
        workflow_config = json.loads(workflow_config_path.read_text(encoding='utf-8'))
        optimization_config_path = workflow_config_path.parent / workflow_config['optimization_config']
        baseline_config_path = workflow_config_path.parent / workflow_config['baseline_config']
        optimization_config = json.loads(optimization_config_path.read_text(encoding='utf-8'))
        baseline_config = json.loads(baseline_config_path.read_text(encoding='utf-8'))
        calibration = optimization_config['solver_kwargs']['damper_calibration']
        calibration_path = Path(calibration['artifact_path'])
        assert calibration_path.is_file()
        assert hashlib.sha256(calibration_path.read_bytes()).hexdigest() == calibration['sha256']
        assert workflow_config['baseline_objective_limits'] == {
            'scenario': 'earthquake',
            'objectives': [
                'max_girder_end_displacement',
                'max_tower_base_shear',
                'max_tower_base_moment',
            ],
        }
        assert len(optimization_config['load_cases']) == 2
        assert optimization_config['load_cases'][0]['load_type'] == 'earthquake'
        operation_case = optimization_config['load_cases'][1]
        assert operation_case['name'] == 'operation'
        assert operation_case['load_type'] == 'wind'
        assert operation_case['dt'] == 1.0
        assert operation_case['duration'] == 3600.0
        assert operation_case['metadata']['wind_speed_mps'] == 10.0
        assert operation_case['metadata']['precombined_wind_traffic'] is True
        assert Path(operation_case['path']).as_posix().endswith(
            'output/operation_staged_inputs/'
            'operation_3600s_dt1_10mps_precombined/operation_wind_traffic_3600s.csv'
        )
        assert 'load_combinations' not in optimization_config
        assert optimization_config['active_load_cases'] == ['earthquake', 'operation']
        assert [
            f"{item['scenario']}:{item['objective']}"
            for item in optimization_config['objective_specs']
        ] == [
            'earthquake:max_girder_end_displacement',
            'earthquake:max_tower_base_shear',
            'earthquake:max_tower_base_moment',
            'operation:cumulative_displacement',
        ]
        assert optimization_config['bounds'] == {'c': [1000.0, 10000.0], 'alpha': [0.3, 1.0]}
        assert optimization_config['steps'] == {'c': 100.0, 'alpha': 0.1}
        assert len(optimization_config['doe_designs']) == 15
        assert optimization_config['progress_completed_offset'] == 0
        assert optimization_config['progress_total_cases'] == 15
        assert optimization_config['progress_component'] == 'doe'
        assert optimization_config['n_candidates'] == 728
        assert optimization_config['surrogate_cv'] == 10
        assert 'max_normalized_doe_distance' not in optimization_config
        assert optimization_config['run_validation'] is True
        assert 'min_validation_r2' not in optimization_config
        assert optimization_config['max_validation_peak_relative_error'] == 0.05
        assert 'min_surrogate_r2' not in optimization_config
        assert optimization_config['max_active_learning_iterations'] == 2
        assert optimization_config['n_validation_points'] == 2
        assert optimization_config['max_validation_designs'] == 2
        assert optimization_config['run_review'] is True
        assert optimization_config['review_reuse_doe_results'] is False
        assert optimization_config['review_relative_error_limit'] == 0.05
        assert optimization_config['max_review_iterations'] == 1
        assert optimization_config['solver_kwargs']['execution_timeout_s'] == 7200
        assert optimization_config['solver_kwargs']['postprocessor']['cumulative_displacement_node'] == 107
        assert optimization_config['parallel'] == {
            'enabled': True,
            'max_workers': 4,
            'license_limit': 4,
            'mode': 'thread',
        }
        assert baseline_config['load_case']['load_type'] == 'earthquake'
        joint_payload = json.dumps(
            {'workflow': workflow_config, 'baseline': baseline_config, 'optimization': optimization_config},
            ensure_ascii=False,
        ).lower()
        assert 'operation' in joint_payload
        assert 'wind' in joint_payload
        assert 'traffic' in joint_payload
        baseline_summary_path = Path(baseline_config['summary_path'])
        optimization_summary_path = Path(optimization_config['summary_path'])
        workflow_summary_path = Path(workflow_config['summary_path'])
        baseline_summary_path.write_text(
            json.dumps(
                {
                    'case_id': 'baseline_no_control',
                    'solver': 'ansys',
                    'status': 'completed',
                    'objectives': {
                        'max_girder_end_displacement': 0.38,
                        'max_tower_base_shear': 5.3e7,
                        'max_tower_base_moment': 2.7e9,
                    },
                }
            ),
            encoding='utf-8',
        )
        optimization_summary_path.write_text(
            json.dumps(
                {
                    'status': 'completed',
                    'optimization': {
                        'objective_names': [
                            'earthquake:max_girder_end_displacement',
                            'earthquake:max_tower_base_shear',
                            'earthquake:max_tower_base_moment',
                        ],
                        'best_objectives': [0.17, 4.8e7, 2.1e9],
                        'parameter_names': ['c', 'alpha'],
                        'best_design': [7800.0, 0.8],
                        'pareto_solutions': [{
                            'design_parameters': {'c': 7800.0, 'alpha': 0.8},
                            'objective_values': {
                                'earthquake:max_girder_end_displacement': 0.17,
                                'earthquake:max_tower_base_shear': 4.8e7,
                                'earthquake:max_tower_base_moment': 2.1e9,
                            },
                        }],
                        'topsis': {
                            'best_index': 0,
                            'ranking': [0],
                            'closeness': [0.9],
                            'weights': [0.4, 0.4, 0.2],
                        },
                    },
                    'objective_limits': {
                        'earthquake:max_girder_end_displacement': 0.38,
                        'earthquake:max_tower_base_shear': 5.3e7,
                        'earthquake:max_tower_base_moment': 2.7e9,
                    },
                    'surrogate_candidate_filter': {
                        'total_candidate_count': 20,
                        'accepted_candidate_count': 8,
                        'rejected_candidate_count': 12,
                    },
                    'doe_designs': [
                        {
                            'design': [5500.0, 0.65],
                            'design_parameters': {'c': 5500.0, 'alpha': 0.65},
                            'analysis_results': [
                                {
                                    'case_id': 'case_001',
                                    'solver': 'ansys',
                                    'status': 'completed',
                                    'load_case': {'name': 'earthquake', 'load_type': 'earthquake'},
                                    'objectives': {
                                        'max_girder_end_displacement': 0.18,
                                        'max_tower_base_shear': 4.9e7,
                                        'max_tower_base_moment': 2.2e9,
                                    },
                                },
                                {
                                    'case_id': 'case_002',
                                    'solver': 'ansys',
                                    'status': 'completed',
                                    'load_case': {'name': 'operation', 'load_type': 'wind'},
                                    'objectives': {'cumulative_displacement': 4.28},
                                },
                            ],
                        }
                    ] + [
                        {
                            'design': [5500.0 + index * 100.0, 0.65],
                            'design_parameters': {'c': 5500.0 + index * 100.0, 'alpha': 0.65},
                            'analysis_results': [
                                {
                                    'case_id': f'case_{index + 1:03d}',
                                    'solver': 'ansys',
                                    'status': 'completed',
                                    'load_case': {'name': 'earthquake', 'load_type': 'earthquake'},
                                    'objectives': {
                                        'max_girder_end_displacement': 0.18,
                                        'max_tower_base_shear': 4.9e7,
                                        'max_tower_base_moment': 2.2e9,
                                    },
                                },
                            ],
                        }
                        for index in range(1, 15)
                    ],
                    'surrogate_selections': {
                        'earthquake:max_girder_end_displacement': {
                            'model_name': 'gpr',
                            'metric_source': 'full_sample_fit',
                            'metrics': {'r2': 0.99, 'mae': 0.01, 'rmse': 0.02},
                            'cross_validation_metrics': {'peak_relative_error': 0.04},
                        },
                    },
                    'surrogate_candidate_metrics': {
                        'earthquake:max_girder_end_displacement': {
                            'gpr': {
                                'selected': True,
                                'fit': {'r2': 0.99, 'mae': 0.01, 'rmse': 0.02},
                                'cross_validation': {'peak_relative_error': 0.04},
                            }
                        }
                    },
                    'validation_status': {
                        'validation_design_count': 0,
                        'validation_record_count': 0,
                        'validation_report_count': 0,
                        'has_validation_records': False,
                        'has_validation_reports': False,
                        'all_verified_execution': False,
                        'all_accepted': False,
                    },
                    'review_status': {
                        'review_record_count': 1,
                        'has_review_records': True,
                        'all_verified_execution': True,
                        'all_accepted': True,
                    },
                    'review_records': [{
                        'candidate': {'pareto_index': 0},
                        'accepted': True,
                        'verified_execution': True,
                        'analysis_results': [{
                            'case_id': 'review_001',
                            'solver': 'ansys',
                            'status': 'completed',
                            'load_case': {'name': 'earthquake', 'load_type': 'earthquake'},
                            'objectives': {
                                'max_girder_end_displacement': 0.18,
                                'max_tower_base_shear': 4.9e7,
                                'max_tower_base_moment': 2.2e9,
                            },
                        }],
                    }],
                }
            ),
            encoding='utf-8',
        )
        workflow_summary = {
            'workflow_summary_path': str(workflow_summary_path),
            'baseline_summary_path': str(baseline_summary_path),
            'optimization_summary_path': str(optimization_summary_path),
            'baseline_status': 'completed',
            'optimization_objective_names': ['earthquake:max_girder_end_displacement'],
            'objective_limits': {'earthquake:max_girder_end_displacement': 1.0},
        }
        workflow_summary_path.write_text(json.dumps(workflow_summary), encoding='utf-8')
        return workflow_summary

    monkeypatch.setattr(
        'app.api.v1.router.platform_store._run_real_baseline_optimization_workflow',
        fake_run,
    )

    response = client.post(
        '/api/v1/jobs',
        json={
            'type': 'MULTI_OBJECTIVE_OPTIMIZATION',
            'params': {
                'projectName': 'MOMO Live',
                'modelFileName': 'STbridge.txt',
                'solver': 'ANSYS',
                'scenario': 'EARTHQUAKE',
                'executionTarget': 'OPTIMIZATION_DECISION',
                'requiredModules': [
                    'DAMPER_BASE',
                    'DOE',
                    'LOADS',
                    'SOLVER_BATCH',
                    'RESULT_EXTRACTION',
                    'SURROGATE_LEARNING',
                    'OPTIMIZATION_DECISION',
                ],
                'runMode': 'REAL_BASELINE_OPTIMIZATION',
                'workflowConfigPath': 'docs/examples/templates/ansys_run_joint_baseline_workflow_template.json',
                'executionTimeoutS': 1200,
            },
        },
    )

    assert response.status_code == 202
    job = response.json()
    assert job['status'] == 'QUEUED'
    platform_store.execute_queued_job(job['jobId'])
    job = client.get(f"/api/v1/jobs/{job['jobId']}").json()
    assert job['status'] == 'SUCCEEDED', job.get('error')
    for artifact in job['artifacts']:
        artifact_path = Path(artifact['path'])
        if not artifact_path.is_absolute():
            artifact_path = Path(__file__).resolve().parents[3] / artifact_path
        assert hashlib.sha256(artifact_path.read_bytes()).hexdigest() == artifact['sha256']
    assert job['result']['mode'] == 'real_baseline_optimization'
    assert job['result']['baselineStatus'] == 'completed'
    assert job['result']['validationStatus']['all_verified_execution'] is False
    assert job['result']['reviewStatus']['all_accepted'] is True
    assert job['result']['finalRecommendationStatus'] == 'ACCEPTED'
    assert job['result']['artifactCount'] == 8
    manifest = next(item for item in job['artifacts'] if item['name'] == 'real_output_manifest.json')
    catalog = next(item for item in job['artifacts'] if item['name'] == 'result_catalog.json')
    assert job['result']['outputManifestArtifactId'] == manifest['artifactId']
    assert job['result']['outputManifestSha256'] == manifest['sha256']
    assert job['result']['resultCatalogArtifactId'] == catalog['artifactId']
    design_set = next(item for item in job['artifacts'] if item['name'] == 'real_design_set.json')
    training_dataset = next(item for item in job['artifacts'] if item['name'] == 'real_training_dataset.json')
    assert job['result']['designSetArtifactId'] == design_set['artifactId']
    assert job['result']['trainingDatasetArtifactId'] == training_dataset['artifactId']
    assert design_set['sha256'] != training_dataset['sha256']
    training_preview = client.get(f"/api/v1/artifacts/{training_dataset['artifactId']}/preview").json()
    assert training_preview['datasetType'] == 'REAL_DOE_TRAINING'
    assert training_preview['source'] == 'REAL_SOLVER_RESULT'
    assert training_preview['trainingSampleCount'] == 15
    assert len(training_preview['rows']) == 15
    assert all(row['sourceCaseIds'] for row in training_preview['rows'])
    assert {artifact['name'] for artifact in job['artifacts']} == {
        'real_workflow_summary.json',
        'real_optimization_summary.json',
        'real_baseline_summary.json',
        'real_earthquake_workflow_overview.json',
        'real_design_set.json',
        'real_training_dataset.json',
        'result_catalog.json',
        'real_output_manifest.json',
    }

    preview = client.get(f"/api/v1/artifacts/{job['result']['optimizationSummaryArtifactId']}/preview")
    assert preview.status_code == 200
    assert preview.json()['validation_status']['all_accepted'] is False
    overview = client.get(f"/api/v1/artifacts/{job['result']['earthquakeWorkflowOverviewArtifactId']}/preview")
    assert overview.status_code == 200
    overview_json = overview.json()
    assert overview_json['operationIncluded'] is True
    assert overview_json['solverParallel'] == {
        'enabled': True,
        'max_workers': 4,
        'license_limit': 4,
        'mode': 'thread',
    }
    assert overview_json['doeContract']['totalSamples'] == 16
    assert overview_json['surrogateCandidates'] == [
        'GPR',
        'KRIGING',
        'RBF',
        'RESPONSE_SURFACE',
        'SVR',
        'PCE',
        'MARS',
        'POWER_LAW',
        'LOG_C_CUBIC_RIDGE',
    ]
    assert overview_json['surrogateAccuracyGate'] == {
        'selectionMetric': 'cross_validation_max_relative_error',
        'acceptanceMetric': 'final_fem_max_relative_error',
        'maxFinalReviewRelativeError': 0.05,
        'r2UsedForAcceptance': False,
        'maxActiveLearningIterations': 2,
        'activeLearningBatchSize': 2,
    }
    assert overview_json['doeContract']['requestedInitialDoeCount'] == 15
    assert overview_json['doeContract']['actualInitialDoeCount'] == 15
    assert overview_json['doeContract']['activeLearningAddedCount'] == 0
    assert overview_json['doeContract']['realSolveCount'] == 17
    assert len(overview_json['doeContract']['designSetSha256']) == 64
    assert overview_json['surrogateCandidateMetrics']['earthquake:max_girder_end_displacement']['gpr']['fit']['r2'] == 0.99
    assert overview_json['objectiveLimits'][1]['displayUnit'] == 'kN'
    assert overview_json['sampleResponses'][0]['sampleType'] == 'UNCONTROLLED_BASELINE'
    assert overview_json['recommendedObjectives']['max_tower_base_shear'] == 4.9e7
    assert overview_json['recommendedObjectiveEvidence']['source'] == 'ACCEPTED_FEM_REVIEW'
    assert (
        overview_json['sampleResponses'][1]['responses']['operationCumulativeDisplacement']['rawValue']
        == 4.28
    )
    manifest_preview = client.get(f"/api/v1/artifacts/{manifest['artifactId']}/preview").json()
    manifest_paths = {item['path'] for item in manifest_preview['files']}
    assert any(path.endswith('real_design_set.json') for path in manifest_paths)
    assert any(path.endswith('real_training_dataset.json') for path in manifest_paths)


@pytest.mark.parametrize(
    ('solver', 'workflow_name', 'solver_key'),
    [
        ('ANSYS', 'ansys_run_joint_baseline_workflow_template.json', 'solver_kwargs'),
        (
            'OPENSEESPY_INPROC',
            'openseespy_inproc_run_joint_baseline_workflow_template.json',
            'solver',
        ),
    ],
)
def test_prepared_workflow_preserves_damper_calibration_evidence(
    monkeypatch,
    tmp_path,
    solver,
    workflow_name,
    solver_key,
) -> None:
    monkeypatch.setattr(
        'app.services.platform_store.EARTHQUAKE_WORKFLOW_OUTPUT_ROOT',
        tmp_path / 'real_workflows',
    )
    source_path = Path(__file__).resolve().parents[3] / 'docs' / 'examples' / 'templates' / workflow_name

    prepared = platform_store._prepare_earthquake_baseline_optimization_workflow(
        source_path,
        {'solver': solver, 'doeSeed': 20260705},
    )

    optimization_config = json.loads(prepared.optimization_config_path.read_text(encoding='utf-8'))
    calibration = optimization_config[solver_key]['damper_calibration']
    calibration_path = Path(calibration['artifact_path'])
    assert calibration_path.is_file()
    assert hashlib.sha256(calibration_path.read_bytes()).hexdigest() == calibration['sha256']
    assert optimization_config['run_validation'] is True
    assert optimization_config['n_validation_points'] == 2
    assert optimization_config['max_validation_designs'] == 2
    assert optimization_config['max_validation_peak_relative_error'] == 0.05
    assert optimization_config['run_review'] is True


def test_prepared_workflow_preserves_explicit_doe_designs(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        'app.services.platform_store.EARTHQUAKE_WORKFLOW_OUTPUT_ROOT',
        tmp_path / 'real_workflows',
    )
    source_path = (
        Path(__file__).resolve().parents[3]
        / 'docs'
        / 'examples'
        / 'templates'
        / 'ansys_run_joint_baseline_workflow_template.json'
    )
    workflow_config = json.loads(source_path.read_text(encoding='utf-8'))
    optimization_source = source_path.parent / workflow_config['optimization_config']
    expected_designs = json.loads(optimization_source.read_text(encoding='utf-8'))['doe_designs']

    prepared = platform_store._prepare_earthquake_baseline_optimization_workflow(
        source_path,
        {'solver': 'ANSYS', 'doeSeed': 20260705},
    )

    optimization_config = json.loads(prepared.optimization_config_path.read_text(encoding='utf-8'))
    assert prepared.doe_designs == expected_designs
    assert optimization_config['doe_designs'] == expected_designs


def test_prepared_workflow_uses_frozen_initial_doe_count(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        'app.services.platform_store.EARTHQUAKE_WORKFLOW_OUTPUT_ROOT',
        tmp_path / 'real_workflows',
    )
    source_path = (
        Path(__file__).resolve().parents[3]
        / 'docs'
        / 'examples'
        / 'templates'
        / 'ansys_run_joint_baseline_workflow_template.json'
    )

    prepared = platform_store._prepare_earthquake_baseline_optimization_workflow(
        source_path,
        {
            'solver': 'ANSYS',
            'doeSeed': 20260705,
            'budget': {'doeDesignCount': 20},
        },
    )

    optimization_config = json.loads(prepared.optimization_config_path.read_text(encoding='utf-8'))
    assert len(prepared.doe_designs) == 20
    assert optimization_config['n_doe_samples'] == 20
    assert optimization_config['doe_designs'] == platform_store._earthquake_doe_designs(20260705, 20)
    assert prepared.requested_doe_count == 20
    assert len(prepared.doe_design_sha256) == 64


def test_earthquake_doe_generation_is_deterministic_and_preserves_default_shape() -> None:
    first = platform_store._earthquake_doe_designs(20260705, 15)
    second = platform_store._earthquake_doe_designs(20260705, 15)

    assert first == second
    assert len(first) == 15
    assert first[:5] == [
        {'c': 5500.0, 'alpha': 0.65},
        {'c': 1000.0, 'alpha': 0.3},
        {'c': 1000.0, 'alpha': 1.0},
        {'c': 10000.0, 'alpha': 0.3},
        {'c': 10000.0, 'alpha': 1.0},
    ]


def test_v1_real_baseline_optimization_rejects_unregistered_workflow_config() -> None:
    client = TestClient(app)

    response = client.post(
        '/api/v1/jobs',
        json={
            'type': 'MULTI_OBJECTIVE_OPTIMIZATION',
            'params': {
                'solver': 'OPENSEESPY_INPROC',
                'scenario': 'EARTHQUAKE',
                'executionTarget': 'OPTIMIZATION_DECISION',
                'requiredModules': ['OPTIMIZATION_DECISION'],
                'runMode': 'REAL_BASELINE_OPTIMIZATION',
                'workflowConfigPath': 'docs/examples/templates/solver_parity_baseline_workflow_template.json',
            },
        },
    )

    assert response.status_code == 422
    assert response.json()['error']['code'] == 'INVALID_WORKFLOW_CONFIG_PATH'


def test_v1_real_baseline_optimization_rejects_unregistered_scenario() -> None:
    """未登记的荷载类型没有 baseline-first 优化模板，必须失败关闭。

    TRAFFIC 已登记（见 test_traffic_analysis_agent.py），这里改用仍然未放行的
    OPERATION 保持本用例原意：门禁只认注册表，不按工况名猜测。
    """
    client = TestClient(app)

    response = client.post(
        '/api/v1/jobs',
        json={
            'type': 'MULTI_OBJECTIVE_OPTIMIZATION',
            'params': {
                'solver': 'ANSYS',
                'scenario': 'OPERATION',
                'executionTarget': 'OPTIMIZATION_DECISION',
                'requiredModules': ['OPTIMIZATION_DECISION'],
                'runMode': 'REAL_BASELINE_OPTIMIZATION',
                'workflowConfigPath': 'docs/examples/templates/ansys_run_joint_baseline_workflow_template.json',
            },
        },
    )

    assert response.status_code == 422
    assert response.json()['error']['code'] == 'UNSUPPORTED_REAL_WORKFLOW_REQUEST'


@pytest.mark.parametrize(
    ('scenario', 'workflow_config_path'),
    [
        ('WIND', 'docs/examples/templates/ansys_run_joint_baseline_workflow_template.json'),
        ('EARTHQUAKE', 'docs/examples/templates/ansys_run_wind_baseline_workflow_template.json'),
    ],
)
def test_v1_real_baseline_optimization_rejects_cross_load_kind_template(
    scenario: str,
    workflow_config_path: str,
) -> None:
    """工况与模板必须同类：风工况配地震模板（或反向）都不得放行。"""
    client = TestClient(app)

    response = client.post(
        '/api/v1/jobs',
        json={
            'type': 'MULTI_OBJECTIVE_OPTIMIZATION',
            'params': {
                'solver': 'ANSYS',
                'scenario': scenario,
                'executionTarget': 'OPTIMIZATION_DECISION',
                'requiredModules': ['OPTIMIZATION_DECISION'],
                'runMode': 'REAL_BASELINE_OPTIMIZATION',
                'workflowConfigPath': workflow_config_path,
            },
        },
    )

    assert response.status_code == 422
    assert response.json()['error']['code'] == 'INVALID_WORKFLOW_CONFIG_PATH'


def test_v1_engineering_config_round_trip_and_validation() -> None:
    client = TestClient(app)
    config = SAMPLE_ENGINEERING_CONFIG

    saved = client.put('/api/v1/engineering-config', json=config)
    assert saved.status_code == 200
    assert saved.json()['projectConfig']['projectName'] == 'MOMO Live'

    loaded = client.get('/api/v1/engineering-config')
    assert loaded.status_code == 200
    assert loaded.json()['globalTaskConfig']['executionTarget'] == 'OPTIMIZATION_DECISION'

    validation = client.post('/api/v1/engineering-config/validate', json=config)
    assert validation.status_code == 200
    statuses = validation.json()['moduleStatus']
    assert any(item['moduleId'] == 'OPTIMIZATION_DECISION' and item['required'] for item in statuses)
    assert next(item for item in statuses if item['moduleId'] == 'DAMPER_BASE')['status'] == 'VALIDATED'
    assert next(item for item in statuses if item['moduleId'] == 'LOADS')['status'] == 'VALIDATED'

    registry = client.get('/api/v1/engineering-config/dampers/registry')
    assert registry.status_code == 200
    assert registry.json()[0]['damperId'] == 'DMP-1'


def test_v1_engineering_config_rejects_non_object_payload() -> None:
    client = TestClient(app)

    for method, path in (
        ('put', '/api/v1/engineering-config'),
        ('post', '/api/v1/engineering-config/validate'),
    ):
        response = getattr(client, method)(path, json=[])
        assert response.status_code == 422
        assert response.json()['error']['code'] == 'VALIDATION_ERROR'


def test_v1_engineering_config_validation_reports_incomplete_required_modules() -> None:
    client = TestClient(app)
    config = {
        **SAMPLE_ENGINEERING_CONFIG,
        'projectConfig': {
            **SAMPLE_ENGINEERING_CONFIG['projectConfig'],
            'modelFile': {
                **SAMPLE_ENGINEERING_CONFIG['projectConfig']['modelFile'],
                'parseStatus': 'UPLOADED',
            },
        },
        'loadConfig': {
            **SAMPLE_ENGINEERING_CONFIG['loadConfig'],
            'earthquake': {'configured': False, 'validated': False},
        },
        'doeConfig': {
            **SAMPLE_ENGINEERING_CONFIG['doeConfig'],
            'variables': [{'id': 'dampingCoefficient'}],
        },
        'solverBatchConfig': {
            **SAMPLE_ENGINEERING_CONFIG['solverBatchConfig'],
            'processCount': 0,
        },
        'resultExtractionConfig': {
            **SAMPLE_ENGINEERING_CONFIG['resultExtractionConfig'],
            'structuralMetrics': [{'id': 'metric_beam_end_ux_peak', 'enabled': False}],
            'autoDamperMetrics': [],
        },
        'surrogateLearningConfig': {
            **SAMPLE_ENGINEERING_CONFIG['surrogateLearningConfig'],
            'modelFamilies': [],
        },
        'optimizationDecisionConfig': {
            **SAMPLE_ENGINEERING_CONFIG['optimizationDecisionConfig'],
            'objectives': [],
        },
    }

    validation = client.post('/api/v1/engineering-config/validate', json=config)

    assert validation.status_code == 200
    statuses = {item['moduleId']: item for item in validation.json()['moduleStatus']}
    assert statuses['DAMPER_BASE']['status'] == 'INCOMPLETE'
    assert statuses['DOE']['status'] == 'UNCONFIGURED'
    assert statuses['LOADS']['status'] == 'UNCONFIGURED'
    assert statuses['SOLVER_BATCH']['status'] == 'INCOMPLETE'
    assert statuses['RESULT_EXTRACTION']['status'] == 'UNCONFIGURED'
    assert statuses['SURROGATE_LEARNING']['status'] == 'UNCONFIGURED'
    assert statuses['OPTIMIZATION_DECISION']['status'] == 'UNCONFIGURED'
    assert all(
        statuses[module_id]['required']
        for module_id in ('DAMPER_BASE', 'DOE', 'LOADS', 'SOLVER_BATCH', 'RESULT_EXTRACTION', 'SURROGATE_LEARNING', 'OPTIMIZATION_DECISION')
    )


def test_v1_platform_action_routes_return_jobs() -> None:
    client = TestClient(app)
    source_job = client.post('/api/v1/load-cases/earthquake', json={'bridgeId': 'demo', 'durationS': 40}).json()
    source_artifact_id = source_job['artifacts'][0]['artifactId']

    routes = [
        ('/api/v1/load-cases/traffic/random', {'bridgeId': 'demo', 'seed': 1}),
        ('/api/v1/load-cases/wind/vertical', {'bridgeId': 'demo', 'appliedComponent': 'VERTICAL'}),
        ('/api/v1/load-cases/earthquake', {'bridgeId': 'demo', 'durationS': 40}),
        ('/api/v1/load-curves/export', {'sourceArtifactId': source_artifact_id, 'loadKind': 'EARTHQUAKE', 'formats': ['CSV']}),
        ('/api/v1/command-streams/assemble', SAMPLE_COMMAND_STREAM_PAYLOAD),
        ('/api/v1/result-extractions', {'solverRunId': 'run'}),
        ('/api/v1/experiment-designs', SAMPLE_EXPERIMENT_DESIGN_PAYLOAD),
        (
            '/api/v1/optimizations/multi-objective',
            {
                'objectives': [{'name': 'beamEndDisplacement', 'direction': 'MIN'}],
                'constraints': [
                    {
                        'targetId': 'beamEndDisplacement',
                        'name': '梁端位移不超过无控状态',
                        'operator': '<=',
                        'value': 0.12,
                        'unit': 'm',
                        'source': 'UNCONTROLLED',
                    }
                ],
            },
        ),
        ('/api/v1/decisions/entropy-topsis', {'optimizationRunId': 'opt'}),
        ('/api/v1/optimizations/export', {'optimizationRunId': 'opt', 'exportKinds': ['PARETO_FRONT'], 'formats': ['CSV']}),
    ]

    for path, payload in routes:
        response = client.post(path, json=payload)
        assert response.status_code == 200, path
        body = response.json()
        assert body['status'] == 'SUCCEEDED'
        assert body['result']['artifactCount'] == len(body['artifacts'])
        if path == '/api/v1/optimizations/multi-objective':
            assert body['result']['objectiveCount'] == 1
            assert body['result']['constraintCount'] == 1


def test_v1_live_placeholder_capabilities_fail_before_creating_records() -> None:
    client = TestClient(app)
    requests = [
        ('/api/v1/solver-runs', {'solver': 'ANSYS', 'caseSetId': 'case'}),
        (
            '/api/v1/surrogates/train',
            {
                'datasetSourceMode': 'DOE_DATASET',
                'datasetId': 'dataset_not_used',
                'modelFamilies': ['GPR'],
                'targetMetricIds': ['metric_beam_end_ux_peak'],
            },
        ),
        ('/api/v1/active-learning/infill', {'activeLearningEnabled': True, 'batchSize': 2}),
        ('/api/v1/jobs', {'type': 'SOLVER_BATCH', 'params': {'solver': 'ANSYS', 'caseSetId': 'case'}}),
        (
            '/api/v1/jobs',
            {
                'type': 'SURROGATE_TRAINING',
                'params': {
                    'datasetSourceMode': 'DOE_DATASET',
                    'datasetId': 'dataset_not_used',
                    'modelFamilies': ['GPR'],
                    'targetMetricIds': ['metric_beam_end_ux_peak'],
                },
            },
        ),
        (
            '/api/v1/jobs',
            {'type': 'ACTIVE_LEARNING', 'params': {'activeLearningEnabled': True, 'batchSize': 2}},
        ),
    ]

    for path, payload in requests:
        job_count = len(platform_store.jobs)
        artifact_count = len(platform_store.artifacts)

        response = client.post(path, json=payload)

        assert response.status_code == 501, path
        assert response.json()['error']['code'] == 'CAPABILITY_NOT_IMPLEMENTED'
        assert len(platform_store.jobs) == job_count
        assert len(platform_store.artifacts) == artifact_count


def test_v1_uploaded_earthquake_load_consumes_registered_file_bytes() -> None:
    client = TestClient(app)
    source = b'time,acc\n0,0.1\n0.02,-0.2\n'

    uploaded_response = client.post(
        '/api/v1/artifacts/uploads?fileName=earthquake.csv',
        content=source,
        headers={'Content-Type': 'application/octet-stream'},
    )

    assert uploaded_response.status_code == 201
    uploaded = uploaded_response.json()
    assert uploaded['fileName'] == 'earthquake.csv'
    assert uploaded['sha256'] == hashlib.sha256(source).hexdigest()
    assert [column['name'] for column in uploaded['inspection']['columns']] == ['time', 'acc']

    response = client.post(
        '/api/v1/load-cases/earthquake',
        json={
            'source': 'LOCAL_FILE',
            'inputArtifactId': uploaded['artifactId'],
            'direction': 'X',
            'loadMapping': {
                'timeColumn': 'time',
                'timeUnit': 's',
                'valueColumn': 'acc',
                'sourceUnit': 'g',
                'quantity': 'ACCELERATION',
                'targetType': 'GROUND',
                'targetId': 'base_nodes',
                'component': 'X',
                'consistentExcitation': True,
            },
        },
    )

    assert response.status_code == 200
    job = response.json()
    assert job['result']['inputArtifactId'] == uploaded['artifactId']
    assert job['result']['sourceSha256'] == uploaded['sha256']
    assert job['result']['normalization']['sourceUnit'] == 'g'
    assert job['result']['normalization']['standardUnit'] == 'm/s2'
    assert job['result']['normalization']['sourceEncoding'] == 'utf-8-sig'
    assert job['result']['normalization']['standardEncoding'] == 'utf-8'
    output_artifact = job['artifacts'][0]
    download = client.get(output_artifact['downloadUrl'])
    assert download.status_code == 200
    assert b'0.02,GROUND,base_nodes,X,ACCELERATION,-1.96133' in download.content


def test_uploaded_load_artifact_cannot_cross_agent_run_boundary() -> None:
    source = b'time,acc\n0,0.1\n0.02,-0.2\n'
    artifact = platform_store.register_artifact(
        kind='RAW_DATA',
        name='owned.csv',
        path='output/platform_store/load_uploads/owned.csv',
        mime_type='text/csv',
        preview={},
        content=source,
        run_id='agr_owner',
    )
    job_count = len(platform_store.jobs)

    with pytest.raises(HTTPException) as excinfo:
        platform_store.create_job(
            'LOAD_EARTHQUAKE',
            {
                'agentRunId': 'agr_other',
                'source': 'LOCAL_FILE',
                'inputArtifactId': artifact.artifact_id,
                'loadMapping': {
                    'timeColumn': 'time',
                    'timeUnit': 's',
                    'valueColumn': 'acc',
                    'sourceUnit': 'g',
                    'quantity': 'ACCELERATION',
                    'targetType': 'GROUND',
                    'targetId': 'base_nodes',
                    'component': 'X',
                    'consistentExcitation': True,
                },
            },
        )

    assert getattr(excinfo.value, 'detail', {})['code'] == 'CROSS_RUN_ARTIFACT'
    assert len(platform_store.jobs) == job_count


@pytest.mark.parametrize(
    ('path', 'payload'),
    [
        (
            '/api/v1/load-cases/wind/vertical',
            {
                'source': 'LOCAL_FILE',
                'appliedComponent': 'VERTICAL',
                'loadMapping': {
                    'timeColumn': 'time',
                    'timeUnit': 's',
                    'valueColumn': 'force',
                    'sourceUnit': 'kN',
                    'quantity': 'FORCE',
                    'targetType': 'NODE_GROUP',
                    'targetId': 'girder_vertical_nodes',
                    'component': 'UZ',
                },
            },
        ),
        (
            '/api/v1/load-cases/traffic/random',
            {
                'sourceMode': 'LOAD_EXISTING',
                'loadMapping': {
                    'timeColumn': 'time',
                    'timeUnit': 's',
                    'valueColumn': 'force',
                    'sourceUnit': 'kN',
                    'quantity': 'FORCE',
                    'targetType': 'LANE_GROUP',
                    'targetId': 'main_girder_lanes',
                    'component': 'UZ',
                },
            },
        ),
    ],
)
def test_v1_uploaded_force_loads_use_artifact_and_explicit_mapping(path: str, payload: dict[str, object]) -> None:
    client = TestClient(app)
    source = b'time,force\n0,1.5\n1,2.5\n'
    uploaded = client.post('/api/v1/artifacts/uploads?fileName=force.csv', content=source).json()

    response = client.post(path, json={**payload, 'inputArtifactId': uploaded['artifactId']})

    assert response.status_code == 200
    job = response.json()
    assert job['result']['inputArtifactId'] == uploaded['artifactId']
    assert job['result']['sourceSha256'] == uploaded['sha256']
    assert job['result']['normalization']['conversionFactor'] == 1000
    standard = client.get(job['artifacts'][0]['downloadUrl']).content
    assert b'1,NODE_GROUP,girder_vertical_nodes,UZ,FORCE,2500,N' in standard or b'1,LANE_GROUP,main_girder_lanes,UZ,FORCE,2500,N' in standard


def test_v1_uploaded_load_rejects_bad_mapping_before_creating_job() -> None:
    client = TestClient(app)
    uploaded = client.post(
        '/api/v1/artifacts/uploads?fileName=earthquake.csv',
        content=b'time,acc\n0,0.1\n0.02,-0.2\n',
    ).json()
    job_count = len(platform_store.jobs)

    response = client.post(
        '/api/v1/load-cases/earthquake',
        json={
            'source': 'LOCAL_FILE',
            'inputArtifactId': uploaded['artifactId'],
            'loadMapping': {
                'timeColumn': 'time',
                'timeUnit': 's',
                'valueColumn': 'missing',
                'sourceUnit': 'g',
                'quantity': 'ACCELERATION',
                'targetType': 'GROUND',
                'targetId': 'base_nodes',
                'component': 'X',
                'consistentExcitation': True,
            },
        },
    )

    assert response.status_code == 422
    assert response.json()['error']['code'] == 'UNKNOWN_VALUE_COLUMN'
    assert len(platform_store.jobs) == job_count


def test_v1_command_requests_accept_declared_aliases_and_reject_unknown_fields() -> None:
    client = TestClient(app)

    camel = client.post('/api/v1/load-cases/earthquake', json={'bridgeId': 'camel_bridge', 'durationS': 20})
    snake = client.post('/api/v1/load-cases/earthquake', json={'bridge_id': 'snake_bridge', 'duration_s': 20})

    assert camel.status_code == 200
    assert snake.status_code == 200
    assert camel.json()['request']['bridgeId'] == 'camel_bridge'
    assert snake.json()['request']['bridgeId'] == 'snake_bridge'
    assert 'source' not in camel.json()['request']
    assert 'source' not in snake.json()['request']

    unknown = client.post(
        '/api/v1/load-cases/earthquake',
        json={'bridgeId': 'demo', 'duratonS': 20},
    )
    assert unknown.status_code == 422
    assert unknown.json()['error']['code'] == 'VALIDATION_ERROR'
    assert unknown.json()['error']['details']['errors'][0]['type'] == 'extra_forbidden'

    coerced = client.post(
        '/api/v1/load-cases/earthquake',
        json={'bridgeId': 'demo', 'durationS': '20'},
    )
    assert coerced.status_code == 422
    assert coerced.json()['error']['code'] == 'VALIDATION_ERROR'

    nested_wind = client.post(
        '/api/v1/wind/workflows',
        json={
            **SAMPLE_WIND_PAYLOAD,
            'timeHistory': {**SAMPLE_WIND_PAYLOAD['timeHistory'], 'frequencyCounnt': 64},
        },
    )
    assert nested_wind.status_code == 422
    assert nested_wind.json()['error']['code'] == 'VALIDATION_ERROR'


def test_v1_generic_jobs_reuse_specialized_request_contracts() -> None:
    client = TestClient(app)
    job_count = len(platform_store.jobs)

    unknown = client.post(
        '/api/v1/jobs',
        json={
            'type': 'RESULT_EXTRACTION',
            'params': {'solverRunId': 'run', 'extractor': ['summary']},
        },
    )
    misspelled_nested = client.post(
        '/api/v1/jobs',
        json={
            'type': 'LOAD_EARTHQUAKE',
            'params': {
                'source': 'LOCAL_FILE',
                'inputArtifactId': 'art_missing',
                'loadMappng': {},
            },
        },
    )

    for response in (unknown, misspelled_nested):
        assert response.status_code == 422
        assert response.json()['error']['code'] == 'VALIDATION_ERROR'
    assert len(platform_store.jobs) == job_count


def test_v1_platform_rejects_invalid_payloads_and_unknown_resources() -> None:
    client = TestClient(app)

    invalid_requests = [
        ('/api/v1/jobs', {'type': 'NOT_A_JOB', 'params': {}}),
        ('/api/v1/load-cases/traffic/random', {'trafficScale': -1}),
        ('/api/v1/experiment-designs', {**SAMPLE_EXPERIMENT_DESIGN_PAYLOAD, 'variables': [{**SAMPLE_EXPERIMENT_DESIGN_PAYLOAD['variables'][0], 'enabled': False}]}),
        ('/api/v1/experiment-designs', {**SAMPLE_EXPERIMENT_DESIGN_PAYLOAD, 'scenarioType': 'OPERATION', 'vehicleLibraryId': None}),
        (
            '/api/v1/command-streams/assemble',
            {
                **SAMPLE_COMMAND_STREAM_PAYLOAD,
                'moduleConfig': {**SAMPLE_COMMAND_STREAM_PAYLOAD['moduleConfig'], 'responseTargets': []},
            },
        ),
        ('/api/v1/surrogates/train', {'datasetSourceMode': 'DOE_DATASET', 'modelFamilies': ['GPR'], 'targetMetricIds': ['metric_beam_end_ux_peak']}),
        (
            '/api/v1/surrogates/train',
            {
                'datasetSourceMode': 'USER_IMPORTED_ARTIFACT',
                'importedDatasetArtifactId': 'art_missing',
                'modelFamilies': ['GPR'],
                'targetMetricIds': ['metric_beam_end_ux_peak'],
            },
        ),
        ('/api/v1/optimizations/multi-objective', {'objectives': []}),
        (
            '/api/v1/optimizations/multi-objective',
            {
                'objectives': [{'name': 'beamEndDisplacement', 'direction': 'MIN'}],
                'constraints': [{'name': '梁端位移约束', 'operator': '<=', 'value': 0.12, 'unit': 'm', 'source': 'UNCONTROLLED'}],
            },
        ),
        (
            '/api/v1/optimizations/multi-objective',
            {
                'objectives': [{'name': 'beamEndDisplacement', 'direction': 'MIN'}],
                'constraints': [
                    {'targetId': 'beamEndDisplacement', 'name': '梁端位移约束', 'operator': '<=', 'value': 0.12, 'unit': 'm', 'source': 'BASELINE'}
                ],
            },
        ),
        (
            '/api/v1/optimizations/multi-objective',
            {
                'objectives': [{'name': 'beamEndDisplacement', 'direction': 'MIN'}],
                'constraints': [
                    {'targetId': 'beamEndDisplacement', 'name': '梁端位移约束', 'operator': '<=', 'value': -0.01, 'unit': 'm', 'source': 'CUSTOM'}
                ],
            },
        ),
        ('/api/v1/optimizations/export', {'optimizationRunId': 'opt', 'exportKinds': [], 'formats': ['CSV']}),
        ('/api/v1/wind/exports', {'format': 'UNIFIED_WIND_CSV'}),
    ]
    for path, payload in invalid_requests:
        response = client.post(path, json=payload)
        assert response.status_code == 422, path
        assert response.json()['error']['code'] == 'VALIDATION_ERROR'
        assert response.json()['error']['message']

    missing_job = client.get('/api/v1/jobs/job_missing')
    assert missing_job.status_code == 404
    assert missing_job.json()['error']['code'] == 'NOT_FOUND'
    assert 'job_missing' in missing_job.json()['error']['message']
    missing_artifact = client.get('/api/v1/artifacts/art_missing')
    assert missing_artifact.status_code == 404
    assert missing_artifact.json()['error']['code'] == 'NOT_FOUND'
    assert 'art_missing' in missing_artifact.json()['error']['message']

    completed = client.post(
        '/api/v1/jobs',
        json={
            'type': 'RESULT_EXTRACTION',
            'params': {'solverRunId': 'run_example', 'extractors': ['summary']},
        },
    ).json()
    cancel = client.post(f"/api/v1/jobs/{completed['jobId']}/cancel")
    assert cancel.status_code == 200
    assert cancel.json()['success'] is False


def test_v1_load_curve_export_requires_registered_load_source() -> None:
    client = TestClient(app)

    missing = client.post(
        '/api/v1/load-curves/export',
        json={'sourceArtifactId': 'art_missing', 'loadKind': 'EARTHQUAKE', 'formats': ['SVG']},
    )
    assert missing.status_code == 404

    source_job = client.post('/api/v1/load-cases/earthquake', json={'bridgeId': 'demo', 'durationS': 40}).json()
    source_artifact_id = source_job['artifacts'][0]['artifactId']

    response = client.post(
        '/api/v1/load-curves/export',
        json={'sourceArtifactId': source_artifact_id, 'loadKind': 'EARTHQUAKE', 'formats': ['SVG', 'PNG']},
    )
    assert response.status_code == 200
    job = response.json()
    assert job['status'] == 'SUCCEEDED'
    assert job['result']['sourceArtifactId'] == source_artifact_id
    assert job['result']['artifactCount'] == 2

    status_report = client.get('/api/v1/artifacts', params={'kind': 'STATUS_REPORT'}).json()['data'][0]
    invalid = client.post(
        '/api/v1/load-curves/export',
        json={'sourceArtifactId': status_report['artifactId'], 'loadKind': 'EARTHQUAKE', 'formats': ['SVG']},
    )
    assert invalid.status_code == 422


def test_v1_active_learning_disabled_still_fails_closed_in_live_mode() -> None:
    client = TestClient(app)
    job_count = len(platform_store.jobs)
    artifact_count = len(platform_store.artifacts)

    response = client.post('/api/v1/active-learning/infill', json={'activeLearningEnabled': False})
    assert response.status_code == 501
    assert response.json()['error']['code'] == 'CAPABILITY_NOT_IMPLEMENTED'
    assert len(platform_store.jobs) == job_count
    assert len(platform_store.artifacts) == artifact_count


def test_platform_store_persists_jobs_artifacts_and_config(tmp_path) -> None:
    state_path = tmp_path / 'platform_state.json'
    store = PlatformStore(state_path=state_path)

    config = {'projectConfig': {'projectName': 'Persistent Project'}}
    store.save_engineering_config(config)
    job = store.create_job('LOAD_EARTHQUAKE', {'bridgeId': 'demo', 'durationS': 40})
    artifact_id = job.artifacts[0].artifact_id

    reloaded = PlatformStore(state_path=state_path)
    assert reloaded.get_engineering_config() == config
    assert reloaded.get_job(job.job_id).status == 'SUCCEEDED'
    assert reloaded.get_artifact(artifact_id).content


def test_platform_store_syncs_completed_real_optimization_history_idempotently(tmp_path) -> None:
    state_path = tmp_path / 'platform_state.sqlite3'
    history_root = tmp_path / 'real_workflows'
    completed_run = history_root / 'ansys_earthquake_123456789abc'
    completed_run.mkdir(parents=True)
    completed_files = {
        'workflow_summary.json': {
            'baseline_status': 'completed',
            'optimization_status': 'completed',
        },
        'optimization_summary.json': {
            'review_status': {'all_verified_execution': True, 'all_accepted': True},
        },
        'undamped_earthquake_summary.json': {'execution_mode': 'run'},
        'real_earthquake_workflow_overview.json': {'mode': 'real_baseline_optimization'},
        'ansys_run_joint_optimization_template.json': {'execution_mode': 'run'},
    }
    for name, payload in completed_files.items():
        (completed_run / name).write_text(json.dumps(payload), encoding='utf-8')
    (completed_run / 'index.csv').write_text('caseId,status\ncase_001,completed\n', encoding='utf-8')

    dry_run = history_root / 'ansys_earthquake_abcdef123456'
    dry_run.mkdir()
    (dry_run / 'workflow_summary.json').write_text(
        json.dumps({'baseline_status': 'completed', 'optimization_status': 'completed', 'dry_run': True}),
        encoding='utf-8',
    )
    (dry_run / 'optimization_summary.json').write_text('{}', encoding='utf-8')
    (dry_run / 'undamped_earthquake_summary.json').write_text('{}', encoding='utf-8')

    store = PlatformStore(state_path=state_path, real_workflow_root=history_root)
    first = store.sync_real_optimization_history()
    second = store.sync_real_optimization_history()

    assert first['importedArtifacts'] == 6
    assert first['importedRuns'] == 1
    assert first['skippedRuns'] == 1
    assert second['importedArtifacts'] == 0
    artifacts = store.list_artifacts(
        None,
        None,
        1,
        20,
        source='REAL_OPTIMIZATION_HISTORY',
    )['data']
    assert len(artifacts) == 6
    assert {artifact.run_id for artifact in artifacts} == {'ansys_earthquake_123456789abc'}
    assert all(artifact.sha256 for artifact in artifacts)

    reloaded = PlatformStore(state_path=state_path, real_workflow_root=history_root)
    persisted = reloaded.list_artifacts(
        None,
        None,
        1,
        20,
        source='REAL_OPTIMIZATION_HISTORY',
    )['data']
    assert len(persisted) == 6


def test_v1_syncs_and_filters_real_optimization_history(tmp_path) -> None:
    client = TestClient(app)
    history_root = tmp_path / 'real_workflows'
    completed_run = history_root / 'openseespy_inproc_earthquake_123456789abc'
    completed_run.mkdir(parents=True)
    payloads = {
        'workflow_summary.json': {'baseline_status': 'completed', 'optimization_status': 'completed'},
        'optimization_summary.json': {'status': 'completed'},
        'undamped_earthquake_summary.json': {'execution_mode': 'run'},
    }
    for name, payload in payloads.items():
        (completed_run / name).write_text(json.dumps(payload), encoding='utf-8')
    platform_store.real_workflow_root = history_root

    sync = client.post('/api/v1/artifacts/sync-real-optimizations')
    assert sync.status_code == 200
    assert sync.json()['importedArtifacts'] == 3

    response = client.get('/api/v1/artifacts', params={'source': 'REAL_OPTIMIZATION_HISTORY'})
    assert response.status_code == 200
    artifacts = response.json()['data']
    assert len(artifacts) == 3
    assert {artifact['runId'] for artifact in artifacts} == {completed_run.name}


def test_platform_store_persist_failure_is_not_silently_accepted(monkeypatch, tmp_path) -> None:
    state_path = tmp_path / 'platform_state.sqlite3'
    store = PlatformStore(state_path=state_path)
    config = {'projectConfig': {'projectName': 'Memory Only Project'}}

    def fail_save(**kwargs):
        raise sqlite3.OperationalError('database is locked')

    monkeypatch.setattr(store.repository, 'save', fail_save)
    with pytest.raises(sqlite3.OperationalError, match='database is locked'):
        store.save_engineering_config(config)
    assert store.get_engineering_config() == config


def test_v1_surrogate_training_fails_closed_before_dataset_lookup() -> None:
    client = TestClient(app)

    unknown_doe = client.post(
        '/api/v1/surrogates/train',
        json={
            'datasetSourceMode': 'DOE_DATASET',
            'datasetId': 'doe_dataset_missing',
            'modelFamilies': ['GPR'],
            'targetMetricIds': ['metric_beam_end_ux_peak'],
        },
    )
    assert unknown_doe.status_code == 501
    assert unknown_doe.json()['error']['code'] == 'CAPABILITY_NOT_IMPLEMENTED'

    dataset_id = create_doe_dataset(client)
    valid_import = client.post(
        '/api/v1/surrogates/train',
        json={
            'datasetSourceMode': 'USER_IMPORTED_ARTIFACT',
            'importedDatasetArtifactId': dataset_id,
            'importedDatasetSchema': imported_dataset_schema(),
            'modelFamilies': ['GPR', 'SVR'],
            'targetMetricIds': ['metric_beam_end_ux_peak'],
        },
    )
    assert valid_import.status_code == 501
    assert valid_import.json()['error']['code'] == 'CAPABILITY_NOT_IMPLEMENTED'


def test_v1_command_stream_preview_contains_audit_summary() -> None:
    client = TestClient(app)

    response = client.post('/api/v1/command-streams/assemble', json=SAMPLE_COMMAND_STREAM_PAYLOAD)

    assert response.status_code == 200
    job = response.json()
    assert job['status'] == 'SUCCEEDED'
    assert job['result']['moduleCount'] == 5
    assert job['result']['responseTargetCount'] == 3
    artifact = job['artifacts'][0]
    assert artifact['kind'] == 'COMMAND_STREAM'
    assert job['result']['commandStreamSha256'] == artifact['sha256']

    preview_response = client.get(f"/api/v1/artifacts/{artifact['artifactId']}/preview")
    assert preview_response.status_code == 200
    preview = preview_response.json()
    assert preview['sha256'] == artifact['sha256']
    assert preview['moduleSummary']['modules'] == SAMPLE_COMMAND_STREAM_PAYLOAD['moduleConfig']['modules']
    assert preview['moduleSummary']['responseTargets'] == SAMPLE_COMMAND_STREAM_PAYLOAD['moduleConfig']['responseTargets']
    assert '! MODULE DAMPER' in preview['commandText']
    assert 'nodes=36-517,107-520' in preview['commandText']
    assert 'command_stream.sha256' in preview['commandText']


def test_v1_random_traffic_defaults_to_one_second_time_step() -> None:
    payload = TrafficLoadRequest()

    assert payload.time_step_s == 1.0
    assert payload.model_dump(by_alias=True)['timeStepS'] == 1.0


def test_v1_random_traffic_uses_wim_library_scales_and_main_girder_artifacts() -> None:
    client = TestClient(app)

    response = client.post(
        '/api/v1/load-cases/traffic/random',
        json={
            'bridgeId': 'stbridge',
            'scenarioName': 'traffic_wim_scaled',
            'vehicleLibraryId': 'vehicle_library_wim_2021_01',
            'trafficScale': 1.12,
            'heavyVehicleScale': 1.5,
            'laneMode': 'MAIN_GIRDER_BIDIRECTIONAL',
            'appliedStructure': 'MAIN_GIRDER',
            'seed': 42,
        },
    )

    assert response.status_code == 200
    job = response.json()
    assert job['status'] == 'SUCCEEDED'
    assert job['result']['effectiveTotalVehicles24h'] == 100264
    assert job['result']['effectiveHeavyVehicleRatio'] == 0.3138
    assert job['result']['laneMode'] == 'MAIN_GIRDER_BIDIRECTIONAL'
    assert job['result']['appliedStructure'] == 'MAIN_GIRDER'

    artifact_kinds = {artifact['kind'] for artifact in job['artifacts']}
    assert {'LOAD_CASE', 'JSON_SUMMARY'} <= artifact_kinds
    summary_artifact = next(artifact for artifact in job['artifacts'] if artifact['kind'] == 'JSON_SUMMARY')
    preview = client.get(f"/api/v1/artifacts/{summary_artifact['artifactId']}/preview")
    assert preview.status_code == 200
    summary = preview.json()
    assert summary['effectiveTotalVehicles24h'] == 100264
    assert summary['effectiveHeavyVehicleRatio'] == 0.3138
    assert summary['laneMode'] == 'MAIN_GIRDER_BIDIRECTIONAL'
    assert summary['appliedStructure'] == 'MAIN_GIRDER'


def test_v1_existing_traffic_requires_registered_source_artifact() -> None:
    client = TestClient(app)

    missing_source = client.post('/api/v1/load-cases/traffic/random', json={'sourceMode': 'LOAD_EXISTING'})
    assert missing_source.status_code == 422
    assert missing_source.json()['error']['code'] == 'VALIDATION_ERROR'

    unknown_source = client.post(
        '/api/v1/load-cases/traffic/random',
        json={'sourceMode': 'LOAD_EXISTING', 'existingVehicleDataArtifactId': 'art_missing'},
    )
    assert unknown_source.status_code == 404
    assert unknown_source.json()['error']['code'] == 'NOT_FOUND'

    generated = client.post('/api/v1/load-cases/traffic/random', json={'bridgeId': 'stbridge', 'seed': 9}).json()
    source_artifact_id = next(artifact['artifactId'] for artifact in generated['artifacts'] if artifact['kind'] == 'LOAD_CASE')
    response = client.post(
        '/api/v1/load-cases/traffic/random',
        json={
            'sourceMode': 'LOAD_EXISTING',
            'existingVehicleDataArtifactId': source_artifact_id,
            'trafficScale': 2,
            'heavyVehicleScale': 0.5,
            'hourlyFlowProfile': [
                {'hour': 8, 'vehiclesPerHour': 100, 'heavyVehicleRatio': 0.2},
                {'hour': 9, 'vehiclesPerHour': 50, 'heavyVehicleRatio': 0.4},
            ],
        },
    )

    assert response.status_code == 200
    job = response.json()
    assert job['status'] == 'SUCCEEDED'
    assert job['result']['sourceMode'] == 'LOAD_EXISTING'
    assert job['result']['existingVehicleDataArtifactId'] == source_artifact_id
    assert job['result']['effectiveTotalVehicles24h'] == 300
    assert job['result']['hourlyFlow'][0]['vehiclesPerHour'] == 200
    assert job['result']['hourlyFlow'][0]['heavyVehicleRatio'] == 0.1

    summary_artifact = next(artifact for artifact in job['artifacts'] if artifact['kind'] == 'JSON_SUMMARY')
    summary = client.get(f"/api/v1/artifacts/{summary_artifact['artifactId']}/preview").json()
    assert summary['sourceMode'] == 'LOAD_EXISTING'
    assert summary['existingVehicleDataArtifactId'] == source_artifact_id


def test_v1_wind_workflow_reuses_existing_service() -> None:
    client = TestClient(app)

    response = client.post('/api/v1/wind/workflows', json=SAMPLE_WIND_PAYLOAD)
    assert response.status_code == 200
    body = response.json()
    assert body['status'] == 'SUCCEEDED'
    assert body['result']['mode'] == 'wind_workflow'
    assert 'summary' in body['result']


def test_v1_wind_export_uses_typed_contract_and_registers_artifact() -> None:
    client = TestClient(app)

    response = client.post(
        '/api/v1/wind/exports',
        json={'format': 'UNIFIED_WIND_CSV', 'request': SAMPLE_WIND_PAYLOAD},
    )

    assert response.status_code == 200
    artifact = response.json()
    assert artifact['kind'] == 'LOAD_CASE'
    assert artifact['name'] == 'wind_export_unified_wind_csv.csv'
    assert artifact['downloadUrl'].startswith('/api/v1/artifacts/')

    download = client.get(artifact['downloadUrl'])
    assert download.status_code == 200
    assert download.headers['content-type'].startswith('text/csv')
    assert download.content


def test_v1_experiment_design_route_returns_contract_artifacts() -> None:
    client = TestClient(app)

    response = client.post('/api/v1/experiment-designs', json=SAMPLE_EXPERIMENT_DESIGN_PAYLOAD)

    assert response.status_code == 200
    job = response.json()
    assert job['status'] == 'SUCCEEDED'
    assert job['result']['sampleCount'] == 9
    assert job['result']['responseTargetCount'] == 3
    artifact_kinds = {artifact['kind'] for artifact in job['artifacts']}
    assert {'CSV_TABLE', 'JSON_SUMMARY', 'RAW_DATA'} <= artifact_kinds

    summary_artifact = next(artifact for artifact in job['artifacts'] if artifact['kind'] == 'JSON_SUMMARY')
    preview = client.get(f"/api/v1/artifacts/{summary_artifact['artifactId']}/preview")
    assert preview.status_code == 200
    preview_body = preview.json()
    assert preview_body['executionMode'] == 'MOCK'
    assert preview_body['simulation'] is True
    assert 'PENDING_REPLACEMENT' not in str(preview_body)


def test_v1_experiment_design_optimization_goal_adds_decision_artifacts() -> None:
    client = TestClient(app)
    payload = {
        **SAMPLE_EXPERIMENT_DESIGN_PAYLOAD,
        'scenarioType': 'OPERATION',
        'executionGoal': 'OPTIMIZATION_RECOMMENDATION',
        'vehicleLibraryId': 'vehicle_library_wim_2021_01',
    }

    response = client.post('/api/v1/experiment-designs', json=payload)

    assert response.status_code == 200
    job = response.json()
    artifact_kinds = {artifact['kind'] for artifact in job['artifacts']}
    assert {'SURROGATE_MODEL', 'OPTIMIZATION_REPORT', 'DECISION_REPORT'} <= artifact_kinds
    assert job['result']['artifactCount'] == 6
    assert job['result']['executionMode'] == 'MOCK'
    assert job['result']['simulation'] is True
    for artifact in job['artifacts']:
        record = platform_store.get_artifact(artifact['artifactId'])
        assert b'surrogate-model-placeholder' not in (record.content or b'')
        assert b'doe-surrogate-model-placeholder' not in (record.content or b'')
        preview = record.preview if isinstance(record.preview, dict) else {}
        if artifact['kind'] in {'SURROGATE_MODEL', 'OPTIMIZATION_REPORT', 'DECISION_REPORT'}:
            assert preview.get('simulation') is True
            assert preview.get('executionMode') == 'MOCK'


def test_v1_topsis_result_detail_route_returns_frontend_shape() -> None:
    client = TestClient(app)

    response = client.get('/api/v1/optimizations/opt_live_001/topsis')

    assert response.status_code == 200
    body = response.json()
    assert body['optimizationRunId'] == 'opt_live_001'
    assert body['candidates'][0]['id'] == 'c1'
    assert body['candidates'][0]['topsisScore'] == 0.847
    assert body['entropyWeights']['beamEndDisplacement'] == 0.28
    assert set(body['entropyWeights']) == {
        'beamEndDisplacement',
        'towerBaseShear',
        'towerBaseMoment',
        'beamEndCumulativeDisplacement',
    }
    assert body['recommendation']['candidateId'] == 'c1'
    assert body['executionMode'] == 'MOCK'
    assert body['simulation'] is True


def test_v1_topsis_result_live_mode_fails_closed(monkeypatch) -> None:
    monkeypatch.setenv('MOMO_PLATFORM_MODE', 'LIVE')
    response = TestClient(app).get('/api/v1/optimizations/opt_live_001/topsis')

    assert response.status_code == 501
    assert response.json()['error']['code'] == 'CAPABILITY_NOT_IMPLEMENTED'
