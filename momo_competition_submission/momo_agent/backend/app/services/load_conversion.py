import numpy as np


def broadcast_mean_velocity(mean_velocity, history: np.ndarray) -> np.ndarray:
    mean = np.asarray(mean_velocity, dtype=float)
    if mean.ndim == 0 or mean.shape == history.shape:
        return mean
    while mean.ndim < history.ndim:
        mean = np.expand_dims(mean, axis=-1)
    return mean


def convert_girder_loads(
    mean_velocity,
    along_fluctuating: np.ndarray,
    vertical_fluctuating: np.ndarray,
    air_density: float,
    ch: float,
    cv: float,
    cm: float,
    width: float,
    depth: float,
    load_model: str = 'linearized',
):
    mean_velocity = np.asarray(mean_velocity, dtype=float)
    along_fluctuating = np.asarray(along_fluctuating, dtype=float)
    vertical_fluctuating = np.asarray(vertical_fluctuating, dtype=float)
    mean_velocity = broadcast_mean_velocity(mean_velocity, along_fluctuating)

    if load_model == 'full_velocity_squared':
        along_velocity = mean_velocity + along_fluctuating
        vertical_velocity = vertical_fluctuating
        along_pressure = 0.5 * air_density * np.square(along_velocity)
        vertical_pressure = 0.5 * air_density * np.square(vertical_velocity)
        return {
            'fh': along_pressure * ch * depth,
            'fv': along_pressure * cv * width + vertical_pressure * ch * width,
            'm': along_pressure * cm * width**2,
        }

    static_pressure = 0.5 * air_density * np.square(mean_velocity)
    dynamic_factor = air_density * mean_velocity
    return {
        'fh': static_pressure * ch * depth + dynamic_factor * ch * depth * along_fluctuating,
        'fv': (
            static_pressure * cv * width
            + dynamic_factor * cv * width * along_fluctuating
            + 0.5 * dynamic_factor * ch * width * vertical_fluctuating
        ),
        'm': static_pressure * cm * width**2 + dynamic_factor * cm * width**2 * along_fluctuating,
    }


def convert_tower_loads(
    mean_velocity,
    along_fluctuating: np.ndarray,
    air_density: float,
    cd: float,
    width: float,
    load_model: str = 'linearized',
):
    mean_velocity = np.asarray(mean_velocity, dtype=float)
    along_fluctuating = np.asarray(along_fluctuating, dtype=float)
    mean_velocity = broadcast_mean_velocity(mean_velocity, along_fluctuating)

    if load_model == 'full_velocity_squared':
        velocity = mean_velocity + along_fluctuating
        pressure = 0.5 * air_density * np.square(velocity)
        return {'drag': pressure * cd * width}

    static_pressure = 0.5 * air_density * np.square(mean_velocity)
    dynamic_factor = air_density * mean_velocity
    return {'drag': static_pressure * cd * width + dynamic_factor * cd * width * along_fluctuating}


def summarize_validation(target_ud: float, sample_history: np.ndarray, target_sigma: float) -> dict[str, float]:
    sample_mean = float(sample_history.mean())
    sample_std = float(sample_history.std())
    return {
        'meanError': sample_mean - target_ud,
        'stdError': sample_std - target_sigma,
    }
