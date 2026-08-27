import numpy as np



def build_cross_spectral_density(auto_density_at_frequency: np.ndarray, coherence_at_frequency: np.ndarray) -> np.ndarray:
    safe_density = np.maximum(np.asarray(auto_density_at_frequency, dtype=float), 0.0)
    root_density = np.sqrt(safe_density)
    return np.outer(root_density, root_density) * np.asarray(coherence_at_frequency, dtype=float)



def estimate_target_std(frequency: np.ndarray, auto_density: np.ndarray) -> np.ndarray:
    return np.sqrt(np.maximum(np.trapezoid(auto_density, frequency, axis=1), 0.0))



def match_target_statistics(history: np.ndarray, target_std: np.ndarray) -> np.ndarray:
    centered = history - history.mean(axis=1, keepdims=True)
    sample_std = centered.std(axis=1, keepdims=True)
    safe_std = np.where(sample_std > 1e-9, sample_std, 1.0)
    return centered * (target_std[:, None] / safe_std)



def synthesize_multivariate_history(
    frequency: np.ndarray,
    auto_density: np.ndarray,
    coherence: np.ndarray,
    duration: float,
    time_step: float,
    seed: int,
) -> np.ndarray:
    rng = np.random.default_rng(seed)
    time_axis = np.arange(0.0, duration, time_step)
    point_count = auto_density.shape[0]
    history = np.zeros((point_count, len(time_axis)))
    delta_f = frequency[1] - frequency[0] if len(frequency) > 1 else 1.0

    for idx, freq in enumerate(frequency[1:], start=1):
        cross_spectrum = build_cross_spectral_density(auto_density[:, idx], coherence[idx])
        chol = np.linalg.cholesky(cross_spectrum + np.eye(point_count) * 1e-12)
        random_complex = (rng.normal(size=point_count) + 1j * rng.normal(size=point_count)) / np.sqrt(2.0)
        spectral_amplitude = chol @ random_complex
        phase_term = np.exp(1j * 2.0 * np.pi * freq * time_axis)
        history += np.sqrt(2.0 * delta_f) * np.real(spectral_amplitude[:, None] * phase_term[None, :])

    target_std = estimate_target_std(frequency, auto_density)
    return match_target_statistics(history, target_std)



def estimate_covariance_lags(
    frequency: np.ndarray,
    auto_density: np.ndarray,
    coherence: np.ndarray,
    time_step: float,
    lag_count: int,
) -> list[np.ndarray]:
    cross_spectra = np.stack(
        [build_cross_spectral_density(auto_density[:, idx], coherence[idx]) for idx in range(len(frequency))],
        axis=0,
    )
    covariance_lags: list[np.ndarray] = []
    for lag in range(lag_count + 1):
        cosine = np.cos(2.0 * np.pi * frequency * lag * time_step)[:, None, None]
        covariance = np.trapezoid(cross_spectra * cosine, frequency, axis=0)
        covariance_lags.append(np.asarray(covariance, dtype=float))
    return covariance_lags



def build_block_toeplitz(covariance_lags: list[np.ndarray]) -> np.ndarray:
    order = len(covariance_lags) - 1
    blocks = []
    for row in range(order):
        row_blocks = []
        for col in range(order):
            lag = row - col
            block = covariance_lags[abs(lag)].T if lag < 0 else covariance_lags[lag]
            row_blocks.append(block)
        blocks.append(np.hstack(row_blocks))
    return np.vstack(blocks)



def solve_multivariate_ar(covariance_lags: list[np.ndarray]) -> tuple[list[np.ndarray], np.ndarray]:
    order = len(covariance_lags) - 1
    point_count = covariance_lags[0].shape[0]
    toeplitz = build_block_toeplitz(covariance_lags)
    rhs = np.vstack([covariance_lags[idx] for idx in range(1, order + 1)])
    coefficients_stack = np.linalg.solve(toeplitz + np.eye(order * point_count) * 1e-10, rhs)
    coefficients = [coefficients_stack[idx * point_count : (idx + 1) * point_count] for idx in range(order)]

    innovation_covariance = covariance_lags[0].copy()
    for idx, coefficient in enumerate(coefficients, start=1):
        innovation_covariance -= coefficient @ covariance_lags[idx].T

    innovation_covariance = 0.5 * (innovation_covariance + innovation_covariance.T)
    innovation_covariance += np.eye(point_count) * 1e-10
    return coefficients, innovation_covariance



def synthesize_multivariate_ar_history(
    frequency: np.ndarray,
    auto_density: np.ndarray,
    coherence: np.ndarray,
    duration: float,
    time_step: float,
    seed: int,
    ar_order: int,
) -> np.ndarray:
    rng = np.random.default_rng(seed)
    step_count = len(np.arange(0.0, duration, time_step))
    covariance_lags = estimate_covariance_lags(frequency, auto_density, coherence, time_step, ar_order)
    coefficients, innovation_covariance = solve_multivariate_ar(covariance_lags)

    point_count = auto_density.shape[0]
    burn_in = max(32, ar_order * 8)
    samples = np.zeros((point_count, step_count + burn_in))
    noise_factor = np.linalg.cholesky(innovation_covariance)

    for index in range(ar_order, step_count + burn_in):
        state = np.zeros(point_count)
        for lag, coefficient in enumerate(coefficients, start=1):
            state += coefficient @ samples[:, index - lag]
        innovation = noise_factor @ rng.normal(size=point_count)
        samples[:, index] = state + innovation

    target_std = estimate_target_std(frequency, auto_density)
    return match_target_statistics(samples[:, burn_in:], target_std)



def generate_multivariate_history(
    frequency: np.ndarray,
    auto_density: np.ndarray,
    coherence: np.ndarray,
    duration: float,
    time_step: float,
    seed: int,
    method: str = 'harmonic',
    ar_order: int = 4,
) -> np.ndarray:
    if method == 'ar':
        return synthesize_multivariate_ar_history(
            frequency=frequency,
            auto_density=auto_density,
            coherence=coherence,
            duration=duration,
            time_step=time_step,
            seed=seed,
            ar_order=ar_order,
        )
    return synthesize_multivariate_history(
        frequency=frequency,
        auto_density=auto_density,
        coherence=coherence,
        duration=duration,
        time_step=time_step,
        seed=seed,
    )



def create_even_points(length: float, count: int) -> np.ndarray:
    return np.linspace(0.0, length, count)
