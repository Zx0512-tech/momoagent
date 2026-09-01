from app.api.schemas import WindRequest
from app.services.normative import compute_normative_parameters

import pytest


def test_compute_normative_parameters_returns_ud_and_turbulence() -> None:
    request = WindRequest.model_validate(
        {
            "project": {"name": "示例桥梁"},
            "site": {
                "u10": 30.0,
                "riskCoefficient": 1.0,
                "surfaceClass": "B",
                "terrainCoefficient": 1.0,
                "airDensity": 1.25,
                "girderReferenceHeight": 50.0,
                "towerHeights": [0.0, 30.0, 60.0],
            },
            "girder": {
                "length": 1200.0,
                "segmentCount": 8,
                "width": 35.0,
                "depth": 4.5,
                "ch": 1.2,
                "cv": 0.8,
                "cm": 0.15,
            },
            "tower": {"height": 120.0, "segmentCount": 6, "width": 8.0, "cd": 1.1},
            "timeHistory": {
                "duration": 600.0,
                "timeStep": 0.5,
                "frequencyCount": 512,
                "seed": 42,
            },
            "overrides": {"enabled": False},
        }
    )

    result = compute_normative_parameters(request)

    assert result.girder.ud > 0
    assert result.girder.iu > 0
    assert len(result.tower.levels) == 3


def test_compute_normative_parameters_applies_override_values() -> None:
    request = WindRequest.model_validate(
        {
            "project": {"name": "示例桥梁"},
            "site": {
                "u10": 20.0,
                "riskCoefficient": 1.0,
                "surfaceClass": "B",
                "terrainCoefficient": 1.0,
                "airDensity": 1.25,
                "girderReferenceHeight": 50.0,
                "towerHeights": [10.0, 30.0],
            },
            "girder": {
                "length": 1200.0,
                "segmentCount": 8,
                "width": 35.0,
                "depth": 4.5,
                "ch": 1.2,
                "cv": 0.8,
                "cm": 0.15,
            },
            "tower": {"height": 120.0, "segmentCount": 6, "width": 8.0, "cd": 1.1},
            "timeHistory": {
                "duration": 600.0,
                "timeStep": 0.5,
                "frequencyCount": 512,
                "seed": 42,
            },
            "overrides": {"enabled": True, "iu": 0.2, "iv": 0.1, "iw": 0.05},
        }
    )

    result = compute_normative_parameters(request)

    assert result.girder.ud >= 24.5
    assert result.girder.iu == 0.2
    assert result.girder.iv == 0.1
    assert result.girder.iw == 0.05


def test_compute_normative_parameters_allows_operational_wind_below_code_minimum() -> None:
    request = WindRequest.model_validate(
        {
            "project": {"name": "运营风速"},
            "site": {
                "u10": 10.0,
                "riskCoefficient": 1.0,
                "surfaceClass": "B",
                "terrainCoefficient": 1.0,
                "airDensity": 1.25,
                "girderReferenceHeight": 50.0,
                "towerHeights": [10.0, 40.0, 80.0],
                "enforceCodeMinimumWindSpeed": False,
            },
            "girder": {
                "length": 1200.0,
                "segmentCount": 8,
                "width": 35.0,
                "depth": 4.5,
                "ch": 1.2,
                "cv": 0.8,
                "cm": 0.15,
            },
            "tower": {"height": 120.0, "segmentCount": 6, "width": 8.0, "cd": 1.1},
            "timeHistory": {
                "duration": 600.0,
                "timeStep": 1.0,
                "frequencyCount": 512,
                "seed": 42,
            },
            "overrides": {"enabled": False},
        }
    )

    result = compute_normative_parameters(request)

    assert result.girder.ud < 24.5
    assert result.girder.ud == pytest.approx(10.0 * (50.0 / 10.0) ** 0.16)
