import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.api.schemas import WindRequest
from app.main import app
from app.services.normative import compute_normative_parameters
from app.services.workflow import run_full_workflow


def test_wind_request_parses_nested_sections() -> None:
    payload = {
        'project': {'name': '示例桥梁'},
        'site': {
            'u10': 30.0,
            'riskCoefficient': 1.0,
            'surfaceClass': 'B',
            'terrainCoefficient': 1.0,
            'airDensity': 1.25,
            'girderReferenceHeight': 50.0,
            'towerHeights': [0.0, 30.0, 60.0],
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
            'duration': 600.0,
            'timeStep': 0.5,
            'frequencyCount': 512,
            'seed': 42,
            'spectrumModel': 'kaimal',
            'verticalSpectrumModel': 'panofsky',
            'simulationMethod': 'ar',
            'arOrder': 3,
            'loadModel': 'full_velocity_squared',
        },
        'overrides': {'enabled': False},
    }

    request = WindRequest.model_validate(payload)
    assert request.project.name == '示例桥梁'
    assert request.girder.segment_count == 8
    assert request.time_history.frequency_count == 512
    assert request.time_history.spectrum_model == 'kaimal'
    assert request.time_history.simulation_method == 'ar'
    assert request.time_history.load_model == 'full_velocity_squared'


def test_workflow_returns_summary_charts_and_exports() -> None:
    client = TestClient(app)
    response = client.post(
        '/api/workflow',
        json={
            'project': {'name': '示例桥梁'},
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
                'duration': 60.0,
                'timeStep': 1.0,
                'frequencyCount': 64,
                'seed': 7,
                'spectrumModel': 'davenport',
                'verticalSpectrumModel': 'panofsky',
                'simulationMethod': 'harmonic',
                'arOrder': 4,
            },
            'overrides': {'enabled': False},
        },
    )

    body = response.json()
    assert response.status_code == 200
    assert 'summary' in body
    assert 'spectra' in body
    assert 'spectrumValidation' in body
    assert 'coherenceValidation' in body
    assert 'extremeValidation' in body
    assert 'pointHistories' in body
    assert 'exports' in body
    assert body['summary']['simulation']['spectrumModel'] == 'davenport'
    assert body['summary']['simulation']['simulationMethod'] == 'harmonic'
    assert body['summary']['simulation']['loadModel'] == 'linearized'
    assert body['summary']['girder']['pointValidation']['maxAbsMeanError'] >= 0.0
    assert len(body['pointHistories']['girder']['points']) == 8
    assert len(body['pointHistories']['girder']['velocity']) == 8
    assert body['coherenceValidation']['girder']['meanAbsError'] >= 0.0
    assert body['extremeValidation']['peakFactor']['girderVelocity']['max'] > 0.0
    assert body['extremeValidation']['extremes']['girderFh']['max'] >= body['extremeValidation']['extremes']['girderFh']['min']
    assert any('模拟谱' in series['name'] for series in body['spectra'])
    assert body['spectrumValidation']['meanLogRelativeError'] < 0.6


def test_workflow_uses_tower_level_parameters_for_representative_history() -> None:
    payload = {
        'project': {'name': '示例桥梁'},
        'site': {
            'u10': 30.0,
            'riskCoefficient': 1.0,
            'surfaceClass': 'B',
            'terrainCoefficient': 1.0,
            'airDensity': 1.25,
            'girderReferenceHeight': 50.0,
            'towerHeights': [10.0, 80.0],
        },
        'girder': {
            'length': 1200.0,
            'segmentCount': 4,
            'width': 35.0,
            'depth': 4.5,
            'ch': 1.2,
            'cv': 0.8,
            'cm': 0.15,
        },
        'tower': {'height': 120.0, 'segmentCount': 2, 'width': 8.0, 'cd': 1.1},
        'timeHistory': {
            'duration': 80.0,
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
    request = WindRequest.model_validate(payload)
    normative = compute_normative_parameters(request)
    result = run_full_workflow(request)

    tower_velocity = np.asarray(result['histories']['tower']['velocity'])
    assert tower_velocity.mean() == pytest.approx(normative.tower.levels[0].ud)
    assert tower_velocity.std() == pytest.approx(normative.tower.levels[0].sigma_u)
