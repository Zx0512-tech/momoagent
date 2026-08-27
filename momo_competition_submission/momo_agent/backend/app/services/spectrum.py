import math

import numpy as np


def build_frequency_axis(frequency_count: int, time_step: float) -> np.ndarray:
    return np.linspace(0.0, 1.0 / (2.0 * time_step), frequency_count)



def compute_friction_velocity(ud: float, roughness: float, height: float) -> float:
    safe_height = max(height, roughness * math.e)
    safe_ratio = max(safe_height / max(roughness, 1e-6), math.e)
    return 0.4 * ud / math.log(safe_ratio)



def normalize_spectrum_variance(frequency: np.ndarray, density: np.ndarray, target_sigma: float) -> np.ndarray:
    target_variance = max(target_sigma, 0.0) ** 2
    current_variance = float(np.trapezoid(density, frequency))
    if current_variance <= 0.0 or target_variance <= 0.0:
        return np.zeros_like(density)
    return density * (target_variance / current_variance)


def smooth_density(density: np.ndarray, window_size: int = 5) -> np.ndarray:
    if window_size <= 1 or len(density) < 3:
        return density
    safe_window = min(window_size, len(density))
    if safe_window % 2 == 0:
        safe_window -= 1
    if safe_window < 3:
        return density
    pad = safe_window // 2
    padded = np.pad(density, pad, mode='edge')
    kernel = np.ones(safe_window) / safe_window
    return np.convolve(padded, kernel, mode='valid')


def estimate_simulated_spectrum(
    target_frequency: np.ndarray,
    history: np.ndarray,
    time_step: float,
    target_density: np.ndarray | None = None,
) -> np.ndarray:
    target_frequency = np.asarray(target_frequency, dtype=float)
    samples = np.asarray(history, dtype=float)
    centered = samples - samples.mean()
    sample_count = len(centered)
    if sample_count < 2:
        return np.zeros_like(target_frequency)

    fft_values = np.fft.rfft(centered)
    fft_frequency = np.fft.rfftfreq(sample_count, d=time_step)
    density = (time_step / sample_count) * np.square(np.abs(fft_values))
    if len(density) > 2:
        density[1:-1] *= 2.0
    density = smooth_density(density, window_size=5)

    simulated = np.interp(target_frequency, fft_frequency, density, left=0.0, right=0.0)
    simulated = np.maximum(simulated, 0.0)
    if target_density is None:
        return simulated

    target_density = np.asarray(target_density, dtype=float)
    target_variance = float(np.trapezoid(target_density, target_frequency))
    simulated_variance = float(np.trapezoid(simulated, target_frequency))
    if target_variance > 0.0 and simulated_variance > 0.0:
        simulated = simulated * (target_variance / simulated_variance)
    return simulated


def spectrum_log_relative_error(frequency: np.ndarray, target_density: np.ndarray, simulated_density: np.ndarray) -> float:
    frequency = np.asarray(frequency, dtype=float)
    target_density = np.asarray(target_density, dtype=float)
    simulated_density = np.asarray(simulated_density, dtype=float)
    positive = (frequency > 0.0) & (target_density > 0.0) & (simulated_density > 0.0)
    if not np.any(positive):
        return 0.0

    significant = target_density >= np.nanmax(target_density[positive]) * 1e-4
    mask = positive & significant
    if not np.any(mask):
        mask = positive
    return float(np.mean(np.abs(np.log10(simulated_density[mask] / target_density[mask]))))



def build_longitudinal_spectrum(
    ud: float,
    sigma_u: float,
    height: float,
    roughness: float,
    reference_speed: float,
    frequency_count: int,
    time_step: float,
    model: str = 'davenport',
) -> tuple[np.ndarray, np.ndarray]:
    frequency = build_frequency_axis(frequency_count, time_step)
    safe_ud = max(ud, 1e-6)
    u_star = compute_friction_velocity(ud, roughness, height)

    if model == 'kaimal':
        reduced = frequency * height / safe_ud
        density = 200.0 * u_star**2 * height / safe_ud / np.power(1.0 + 50.0 * reduced, 5.0 / 3.0)
    else:
        safe_reference = max(reference_speed, 1e-6)
        reduced = 1200.0 * frequency / safe_reference
        density = np.zeros_like(frequency)
        valid = frequency > 0.0
        density[valid] = (
            4.0
            * u_star**2
            * np.square(reduced[valid])
            / (frequency[valid] * np.power(1.0 + np.square(reduced[valid]), 4.0 / 3.0))
        )

    return frequency, normalize_spectrum_variance(frequency, density, sigma_u)



def build_vertical_spectrum(
    ud: float,
    sigma_w: float,
    height: float,
    roughness: float,
    frequency_count: int,
    time_step: float,
    model: str = 'panofsky',
) -> tuple[np.ndarray, np.ndarray]:
    frequency = build_frequency_axis(frequency_count, time_step)
    safe_ud = max(ud, 1e-6)
    u_star = compute_friction_velocity(ud, roughness, height)
    reduced = frequency * height / safe_ud

    if model == 'lumley_panofsky':
        density = 3.36 * u_star**2 * height / safe_ud / np.power(1.0 + 10.0 * reduced, 5.0 / 3.0)
    else:
        density = 6.0 * u_star**2 * height / safe_ud / np.square(1.0 + 4.0 * reduced)

    return frequency, normalize_spectrum_variance(frequency, density, sigma_w)



def build_girder_spectrum(ud: float, sigma_u: float, frequency_count: int, time_step: float) -> tuple[np.ndarray, np.ndarray]:
    return build_longitudinal_spectrum(
        ud=ud,
        sigma_u=sigma_u,
        height=50.0,
        roughness=0.05,
        reference_speed=ud,
        frequency_count=frequency_count,
        time_step=time_step,
        model='davenport',
    )



def build_tower_spectrum(ud: float, sigma_u: float, frequency_count: int, time_step: float) -> tuple[np.ndarray, np.ndarray]:
    return build_longitudinal_spectrum(
        ud=ud,
        sigma_u=sigma_u,
        height=100.0,
        roughness=0.05,
        reference_speed=ud,
        frequency_count=frequency_count,
        time_step=time_step,
        model='davenport',
    )



def build_coherence_matrix(frequency: np.ndarray, points: list[float] | np.ndarray, ud: float | np.ndarray, k1: float) -> np.ndarray:
    point_array = np.asarray(points, dtype=float)
    matrix = np.zeros((len(frequency), len(point_array), len(point_array)))
    speed = np.asarray(ud, dtype=float)
    if speed.ndim == 0:
        speed_matrix = np.full((len(point_array), len(point_array)), max(float(speed), 1e-6))
    else:
        if len(speed) != len(point_array):
            raise ValueError("Point wind speed count must match point count.")
        speed_matrix = 0.5 * (speed[:, None] + speed[None, :])
        speed_matrix = np.maximum(speed_matrix, 1e-6)

    for idx, freq in enumerate(frequency):
        for i, xi in enumerate(point_array):
            for j, xj in enumerate(point_array):
                distance = abs(xi - xj)
                matrix[idx, i, j] = math.exp(-k1 * freq * distance / speed_matrix[i, j])
    return matrix
