import numpy as np

from app.services.simulation import create_even_points, generate_multivariate_history, synthesize_multivariate_history


def test_synthesize_multivariate_history_returns_points_by_time() -> None:
    frequency = np.array([0.0, 0.5, 1.0])
    auto_density = np.array([[0.0, 1.0, 0.8], [0.0, 0.9, 0.7]])
    coherence = np.ones((3, 2, 2))

    history = synthesize_multivariate_history(
        frequency=frequency,
        auto_density=auto_density,
        coherence=coherence,
        duration=4.0,
        time_step=1.0,
        seed=42,
    )

    assert history.shape == (2, 4)


def test_create_even_points_returns_expected_spacing() -> None:
    points = create_even_points(12.0, 4)
    assert np.allclose(points, np.array([0.0, 4.0, 8.0, 12.0]))


def test_generate_multivariate_history_supports_ar_method() -> None:
    frequency = np.linspace(0.0, 1.5, 16)
    auto_density = np.array(
        [
            np.linspace(0.0, 1.2, 16),
            np.linspace(0.0, 0.9, 16),
        ]
    )
    coherence = np.ones((16, 2, 2))
    coherence[:, 0, 1] = 0.55
    coherence[:, 1, 0] = 0.55

    history = generate_multivariate_history(
        frequency=frequency,
        auto_density=auto_density,
        coherence=coherence,
        duration=8.0,
        time_step=0.5,
        seed=21,
        method='ar',
        ar_order=4,
    )

    assert history.shape == (2, 16)
    assert np.isfinite(history).all()
    assert not np.allclose(history[0], history[1])
