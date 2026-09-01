import numpy as np
import pytest

from app.services.spectrum import (
    build_coherence_matrix,
    build_girder_spectrum,
    build_longitudinal_spectrum,
    build_tower_spectrum,
    build_vertical_spectrum,
    estimate_simulated_spectrum,
    spectrum_log_relative_error,
)
from app.services.simulation import generate_multivariate_history


def test_build_girder_spectrum_returns_frequency_and_density() -> None:
    frequency, density = build_girder_spectrum(30.0, 4.0, 256, 0.5)
    assert len(frequency) == 256
    assert density.shape == (256,)
    assert density[1] > 0


def test_build_tower_spectrum_returns_frequency_and_density() -> None:
    frequency, density = build_tower_spectrum(28.0, 3.0, 256, 0.5)
    assert len(frequency) == 256
    assert density.shape == (256,)


def test_build_coherence_matrix_returns_square_cube() -> None:
    frequency, _ = build_girder_spectrum(30.0, 4.0, 8, 0.5)
    coherence = build_coherence_matrix(frequency, [0.0, 10.0, 20.0], 30.0, 7.0)
    assert coherence.shape == (8, 3, 3)
    assert coherence[0, 0, 0] == 1.0


def test_build_coherence_matrix_supports_point_wind_speeds() -> None:
    frequency = np.array([0.0, 0.5])
    coherence = build_coherence_matrix(frequency, [0.0, 10.0], np.array([20.0, 30.0]), 7.0)

    expected = np.exp(-7.0 * 0.5 * 10.0 / 25.0)
    assert coherence[1, 0, 1] == pytest.approx(expected)


def test_build_longitudinal_spectrum_supports_davenport_and_kaimal() -> None:
    frequency_d, density_d = build_longitudinal_spectrum(
        ud=30.0,
        sigma_u=4.0,
        height=50.0,
        roughness=0.05,
        reference_speed=28.0,
        frequency_count=256,
        time_step=0.5,
        model='davenport',
    )
    frequency_k, density_k = build_longitudinal_spectrum(
        ud=30.0,
        sigma_u=4.0,
        height=50.0,
        roughness=0.05,
        reference_speed=28.0,
        frequency_count=256,
        time_step=0.5,
        model='kaimal',
    )

    assert len(frequency_d) == len(frequency_k) == 256
    assert density_d.shape == density_k.shape == (256,)
    assert density_d[1] > 0
    assert density_k[1] > 0
    assert not np.allclose(density_d[1:32], density_k[1:32])


def test_build_vertical_spectrum_matches_target_variance() -> None:
    target_sigma = 2.4
    frequency, density = build_vertical_spectrum(
        ud=32.0,
        sigma_w=target_sigma,
        height=60.0,
        roughness=0.05,
        frequency_count=512,
        time_step=0.25,
        model='panofsky',
    )

    variance = float(np.trapezoid(density, frequency))
    assert variance > 0
    assert variance == pytest.approx(target_sigma**2, rel=0.12, abs=0.2)


def test_estimate_simulated_spectrum_matches_target_shape() -> None:
    frequency, target_density = build_longitudinal_spectrum(
        ud=30.0,
        sigma_u=4.0,
        height=50.0,
        roughness=0.05,
        reference_speed=30.0,
        frequency_count=128,
        time_step=0.5,
        model='kaimal',
    )
    auto_density = target_density[np.newaxis, :]
    coherence = np.ones((len(frequency), 1, 1))
    history = generate_multivariate_history(
        frequency=frequency,
        auto_density=auto_density,
        coherence=coherence,
        duration=256.0,
        time_step=0.5,
        seed=11,
        method='harmonic',
    )

    simulated_density = estimate_simulated_spectrum(
        target_frequency=frequency,
        history=history[0],
        time_step=0.5,
        target_density=target_density,
    )
    error = spectrum_log_relative_error(frequency, target_density, simulated_density)

    assert float(np.trapezoid(simulated_density, frequency)) == pytest.approx(
        float(np.trapezoid(target_density, frequency)),
        rel=0.05,
    )
    assert error < 0.45
