import numpy as np
import pytest

from app.services.load_conversion import convert_girder_loads, convert_tower_loads, summarize_validation


def test_convert_girder_loads_returns_fh_fv_m() -> None:
    along_fluctuating = np.array([[0.8, -0.4, 0.2]])
    vertical_fluctuating = np.array([[0.1, -0.2, 0.0]])
    result = convert_girder_loads(
        mean_velocity=30.0,
        along_fluctuating=along_fluctuating,
        vertical_fluctuating=vertical_fluctuating,
        air_density=1.25,
        ch=1.2,
        cv=0.8,
        cm=0.15,
        width=35.0,
        depth=4.5,
    )

    assert set(result.keys()) == {'fh', 'fv', 'm'}
    assert result['fh'].shape == (1, 3)

    expected_static_fh = 0.5 * 1.25 * 30.0**2 * 1.2 * 4.5
    expected_dynamic_fh = 1.25 * 30.0 * 1.2 * 4.5 * 0.8
    assert result['fh'][0, 0] == pytest.approx(expected_static_fh + expected_dynamic_fh)

    expected_static_fv = 0.5 * 1.25 * 30.0**2 * 0.8 * 35.0
    expected_along_fv = 1.25 * 30.0 * 0.8 * 35.0 * 0.8
    expected_vertical_fv = 0.5 * 1.25 * 30.0 * 1.2 * 35.0 * 0.1
    assert result['fv'][0, 0] == pytest.approx(expected_static_fv + expected_along_fv + expected_vertical_fv)


def test_convert_girder_loads_supports_full_velocity_squared_model() -> None:
    along_fluctuating = np.array([[1.0, -2.0]])
    vertical_fluctuating = np.array([[0.5, -0.5]])
    result = convert_girder_loads(
        mean_velocity=30.0,
        along_fluctuating=along_fluctuating,
        vertical_fluctuating=vertical_fluctuating,
        air_density=1.25,
        ch=1.2,
        cv=0.8,
        cm=0.15,
        width=35.0,
        depth=4.5,
        load_model='full_velocity_squared',
    )

    expected_pressure = 0.5 * 1.25 * 31.0**2
    expected_vertical_pressure = 0.5 * 1.25 * 0.5**2
    assert result['fh'][0, 0] == pytest.approx(expected_pressure * 1.2 * 4.5)
    assert result['fv'][0, 0] == pytest.approx(expected_pressure * 0.8 * 35.0 + expected_vertical_pressure * 1.2 * 35.0)


def test_convert_tower_loads_returns_drag() -> None:
    along_fluctuating = np.array([[0.5, -0.2]])
    result = convert_tower_loads(mean_velocity=20.0, along_fluctuating=along_fluctuating, air_density=1.25, cd=1.1, width=8.0)

    assert set(result.keys()) == {'drag'}
    expected_static_drag = 0.5 * 1.25 * 20.0**2 * 1.1 * 8.0
    expected_dynamic_drag = 1.25 * 20.0 * 1.1 * 8.0 * 0.5
    assert result['drag'][0, 0] == pytest.approx(expected_static_drag + expected_dynamic_drag)


def test_convert_tower_loads_supports_full_velocity_squared_model() -> None:
    along_fluctuating = np.array([[0.5, -0.2]])
    result = convert_tower_loads(
        mean_velocity=20.0,
        along_fluctuating=along_fluctuating,
        air_density=1.25,
        cd=1.1,
        width=8.0,
        load_model='full_velocity_squared',
    )

    expected_drag = 0.5 * 1.25 * 20.5**2 * 1.1 * 8.0
    assert result['drag'][0, 0] == pytest.approx(expected_drag)


def test_convert_tower_loads_supports_per_level_mean_velocity() -> None:
    along_fluctuating = np.zeros((2, 3))
    result = convert_tower_loads(
        mean_velocity=np.array([20.0, 30.0]),
        along_fluctuating=along_fluctuating,
        air_density=1.25,
        cd=1.1,
        width=8.0,
    )

    assert result['drag'].shape == (2, 3)
    assert result['drag'][0, 0] == pytest.approx(0.5 * 1.25 * 20.0**2 * 1.1 * 8.0)
    assert result['drag'][1, 0] == pytest.approx(0.5 * 1.25 * 30.0**2 * 1.1 * 8.0)


def test_summarize_validation_returns_mean_and_std_error() -> None:
    sample_history = np.array([10.0, 12.0, 14.0])
    result = summarize_validation(target_ud=12.0, sample_history=sample_history, target_sigma=1.0)
    assert 'meanError' in result
    assert 'stdError' in result
