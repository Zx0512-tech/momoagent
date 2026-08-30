import numpy as np

from app.api.schemas import WindRequest
from app.services.exporter import build_export_manifest
from app.services.load_conversion import convert_girder_loads, convert_tower_loads, summarize_validation
from app.services.normative import compute_normative_parameters
from app.services.simulation import create_even_points, generate_multivariate_history
from app.services.spectrum import (
    build_coherence_matrix,
    build_longitudinal_spectrum,
    build_vertical_spectrum,
    estimate_simulated_spectrum,
    spectrum_log_relative_error,
)
from app.services.validation import resolve_roughness_length


DEFAULT_K1 = 7.0
MODEL_LABELS = {
    'davenport': 'Davenport',
    'kaimal': 'Kaimal',
    'panofsky': 'Panofsky',
    'lumley_panofsky': 'Lumley-Panofsky',
    'harmonic': '谐波合成法',
    'ar': 'AR 线性滤波法',
}



def build_point_validation(target_mean: np.ndarray, history: np.ndarray, target_std: np.ndarray) -> dict[str, float]:
    target_mean = np.asarray(target_mean, dtype=float)
    target_std = np.asarray(target_std, dtype=float)
    while target_mean.ndim < history.ndim:
        target_mean = target_mean[:, None]
    while target_std.ndim < history.ndim:
        target_std = target_std[:, None]

    mean_errors = history.mean(axis=1, keepdims=True) - target_mean
    std_errors = history.std(axis=1, keepdims=True) - target_std
    return {
        'maxAbsMeanError': float(np.max(np.abs(mean_errors))),
        'maxAbsStdError': float(np.max(np.abs(std_errors))),
        'meanAbsMeanError': float(np.mean(np.abs(mean_errors))),
        'meanAbsStdError': float(np.mean(np.abs(std_errors))),
    }


def build_summary(
    normative,
    payload: WindRequest,
    girder_points: np.ndarray,
    tower_points: np.ndarray,
    girder_history: np.ndarray,
    tower_history: np.ndarray,
) -> dict:
    tower_mean_speeds = np.asarray([level.ud for level in normative.tower.levels], dtype=float)
    tower_target_stds = np.asarray([level.sigma_u for level in normative.tower.levels], dtype=float)
    return {
        'projectName': payload.project.name,
        'girder': {
            'ud': normative.girder.ud,
            'iu': normative.girder.iu,
            'iv': normative.girder.iv,
            'iw': normative.girder.iw,
            'pointCount': int(len(girder_points)),
            'validation': summarize_validation(normative.girder.ud, girder_history[0], normative.girder.sigma_u),
            'pointValidation': build_point_validation(
                np.full(len(girder_points), normative.girder.ud),
                girder_history,
                np.full(len(girder_points), normative.girder.sigma_u),
            ),
        },
        'tower': {
            'levelCount': int(len(tower_points)),
            'topUd': normative.tower.levels[-1].ud,
            'validation': summarize_validation(tower_mean_speeds[0], tower_history[0], tower_target_stds[0]),
            'pointValidation': build_point_validation(tower_mean_speeds, tower_history, tower_target_stds),
        },
        'simulation': {
            'spectrumModel': payload.time_history.spectrum_model,
            'verticalSpectrumModel': payload.time_history.vertical_spectrum_model,
            'simulationMethod': payload.time_history.simulation_method,
            'loadModel': payload.time_history.load_model,
        },
    }



def build_chart_payload(
    girder_freq: np.ndarray,
    girder_density: np.ndarray,
    girder_simulated_density: np.ndarray,
    girder_vertical_density: np.ndarray,
    girder_vertical_simulated_density: np.ndarray,
    tower_freq: np.ndarray,
    tower_density: np.ndarray,
    tower_simulated_density: np.ndarray,
    payload: WindRequest,
) -> list[dict[str, list[float] | str]]:
    return [
        {
            'name': f"主梁顺风目标谱（{MODEL_LABELS[payload.time_history.spectrum_model]}）",
            'frequency': girder_freq.tolist(),
            'values': girder_density.tolist(),
        },
        {
            'name': '主梁顺风模拟谱',
            'frequency': girder_freq.tolist(),
            'values': girder_simulated_density.tolist(),
        },
        {
            'name': f"主梁竖向目标谱（{MODEL_LABELS[payload.time_history.vertical_spectrum_model]}）",
            'frequency': girder_freq.tolist(),
            'values': girder_vertical_density.tolist(),
        },
        {
            'name': '主梁竖向模拟谱',
            'frequency': girder_freq.tolist(),
            'values': girder_vertical_simulated_density.tolist(),
        },
        {
            'name': f"桥塔顺风目标谱（{MODEL_LABELS[payload.time_history.spectrum_model]}）",
            'frequency': tower_freq.tolist(),
            'values': tower_density.tolist(),
        },
        {
            'name': '桥塔顺风模拟谱',
            'frequency': tower_freq.tolist(),
            'values': tower_simulated_density.tolist(),
        },
    ]


def build_spectrum_validation_payload(errors: list[float]) -> dict[str, float]:
    return {
        'meanLogRelativeError': float(np.mean(errors)) if errors else 0.0,
        'maxLogRelativeError': float(np.max(errors)) if errors else 0.0,
    }


def estimate_pair_coherence(history: np.ndarray, first_index: int, second_index: int, frequency: np.ndarray, time_step: float) -> np.ndarray:
    first = np.asarray(history[first_index], dtype=float)
    second = np.asarray(history[second_index], dtype=float)
    first = first - first.mean()
    second = second - second.mean()
    sample_count = len(first)
    if sample_count < 2:
        return np.ones_like(frequency)

    first_fft = np.fft.rfft(first)
    second_fft = np.fft.rfft(second)
    fft_frequency = np.fft.rfftfreq(sample_count, d=time_step)
    cross_density = (time_step / sample_count) * first_fft * np.conj(second_fft)
    first_density = (time_step / sample_count) * np.square(np.abs(first_fft))
    second_density = (time_step / sample_count) * np.square(np.abs(second_fft))
    coherence = np.square(np.abs(cross_density)) / np.maximum(first_density * second_density, 1e-18)
    coherence = np.clip(np.real(coherence), 0.0, 1.0)
    return np.interp(frequency, fft_frequency, coherence, left=0.0, right=0.0)


def coherence_abs_error(frequency: np.ndarray, target: np.ndarray, simulated: np.ndarray) -> dict[str, float]:
    frequency = np.asarray(frequency, dtype=float)
    target = np.asarray(target, dtype=float)
    simulated = np.asarray(simulated, dtype=float)
    mask = frequency > 0.0
    if not np.any(mask):
        return {'meanAbsError': 0.0, 'maxAbsError': 0.0}
    errors = np.abs(simulated[mask] - target[mask])
    return {
        'meanAbsError': float(np.mean(errors)),
        'maxAbsError': float(np.max(errors)),
    }


def build_coherence_validation_payload(
    frequency: np.ndarray,
    target_coherence: np.ndarray,
    history: np.ndarray,
    time_step: float,
) -> dict[str, float]:
    if target_coherence.shape[1] <= 1:
        return {'meanAbsError': 0.0, 'maxAbsError': 0.0}
    pair_errors = []
    for index in range(1, target_coherence.shape[1]):
        simulated = estimate_pair_coherence(history, 0, index, frequency, time_step)
        target = target_coherence[:, 0, index]
        pair_errors.append(coherence_abs_error(frequency, target, simulated))
    return {
        'meanAbsError': float(np.mean([item['meanAbsError'] for item in pair_errors])),
        'maxAbsError': float(np.max([item['maxAbsError'] for item in pair_errors])),
    }


def summarize_extreme_statistics(values: np.ndarray) -> dict[str, float]:
    values = np.asarray(values, dtype=float)
    return {
        'min': float(np.min(values)),
        'max': float(np.max(values)),
        'mean': float(np.mean(values)),
        'std': float(np.std(values)),
        'p05': float(np.percentile(values, 5)),
        'p95': float(np.percentile(values, 95)),
    }


def summarize_peak_factor(fluctuating: np.ndarray, target_std: np.ndarray) -> dict[str, float]:
    target_std = np.asarray(target_std, dtype=float)
    peak_values = np.max(np.abs(fluctuating), axis=1)
    factors = peak_values / np.maximum(target_std, 1e-12)
    return {
        'mean': float(np.mean(factors)),
        'max': float(np.max(factors)),
        'min': float(np.min(factors)),
    }


def build_extreme_validation_payload(
    girder_fluctuating: np.ndarray,
    tower_fluctuating: np.ndarray,
    girder_loads: dict,
    tower_loads: dict,
    girder_sigma_u: float,
    tower_sigma_u: np.ndarray,
) -> dict:
    return {
        'peakFactor': {
            'girderVelocity': summarize_peak_factor(
                girder_fluctuating,
                np.full(girder_fluctuating.shape[0], girder_sigma_u),
            ),
            'towerVelocity': summarize_peak_factor(tower_fluctuating, tower_sigma_u),
        },
        'extremes': {
            'girderVelocity': summarize_extreme_statistics(girder_fluctuating),
            'towerVelocity': summarize_extreme_statistics(tower_fluctuating),
            'girderFh': summarize_extreme_statistics(girder_loads['fh']),
            'girderFv': summarize_extreme_statistics(girder_loads['fv']),
            'girderM': summarize_extreme_statistics(girder_loads['m']),
            'towerDrag': summarize_extreme_statistics(tower_loads['drag']),
        },
    }



def build_coherence_payload(girder_freq: np.ndarray, girder_coherence: np.ndarray, tower_freq: np.ndarray, tower_coherence: np.ndarray) -> list[dict[str, list[float] | str]]:
    girder_index = 1 if girder_coherence.shape[1] > 1 else 0
    tower_index = 1 if tower_coherence.shape[1] > 1 else 0
    return [
        {'name': '主梁相干', 'frequency': girder_freq.tolist(), 'values': girder_coherence[:, 0, girder_index].tolist()},
        {'name': '桥塔相干', 'frequency': tower_freq.tolist(), 'values': tower_coherence[:, 0, tower_index].tolist()},
    ]


def build_tower_auto_density(normative, roughness: float, payload: WindRequest) -> tuple[np.ndarray, np.ndarray]:
    tower_density_rows = []
    tower_freq = None
    for level in normative.tower.levels:
        level_freq, level_density = build_longitudinal_spectrum(
            ud=level.ud,
            sigma_u=level.sigma_u,
            height=level.height,
            roughness=roughness,
            reference_speed=payload.site.u10,
            frequency_count=payload.time_history.frequency_count,
            time_step=payload.time_history.time_step,
            model=payload.time_history.spectrum_model,
        )
        tower_freq = level_freq
        tower_density_rows.append(level_density)

    return np.asarray(tower_freq), np.vstack(tower_density_rows)



def build_history_payload(time_axis: np.ndarray, girder_total: np.ndarray, tower_total: np.ndarray, girder_loads: dict, tower_loads: dict) -> dict:
    return {
        'time': time_axis.tolist(),
        'girder': {
            'velocity': girder_total[0].tolist(),
            'fh': girder_loads['fh'][0].tolist(),
            'fv': girder_loads['fv'][0].tolist(),
            'm': girder_loads['m'][0].tolist(),
        },
        'tower': {
            'velocity': tower_total[0].tolist(),
            'drag': tower_loads['drag'][0].tolist(),
        },
    }


def build_point_payload(points: np.ndarray, velocity: np.ndarray, loads: dict) -> dict:
    return {
        'points': points.tolist(),
        'velocity': velocity.tolist(),
        **{name: values.tolist() for name, values in loads.items()},
    }



def run_full_workflow(payload: WindRequest) -> dict:
    normative = compute_normative_parameters(payload)
    roughness = resolve_roughness_length(payload.site.surface_class)
    girder_points = create_even_points(payload.girder.length, payload.girder.segment_count)
    tower_points = np.asarray(payload.site.tower_heights, dtype=float)
    tower_top = normative.tower.levels[-1]

    girder_freq, girder_density = build_longitudinal_spectrum(
        ud=normative.girder.ud,
        sigma_u=normative.girder.sigma_u,
        height=normative.girder.height,
        roughness=roughness,
        reference_speed=payload.site.u10,
        frequency_count=payload.time_history.frequency_count,
        time_step=payload.time_history.time_step,
        model=payload.time_history.spectrum_model,
    )
    girder_vertical_freq, girder_vertical_density = build_vertical_spectrum(
        ud=normative.girder.ud,
        sigma_w=normative.girder.sigma_w,
        height=normative.girder.height,
        roughness=roughness,
        frequency_count=payload.time_history.frequency_count,
        time_step=payload.time_history.time_step,
        model=payload.time_history.vertical_spectrum_model,
    )
    tower_freq, tower_auto = build_tower_auto_density(normative, roughness, payload)
    tower_density = tower_auto[-1]

    k1 = payload.overrides.k1 or DEFAULT_K1
    girder_coherence = build_coherence_matrix(girder_freq, girder_points, normative.girder.ud, k1)
    tower_mean_speeds = np.asarray([level.ud for level in normative.tower.levels], dtype=float)
    tower_coherence = build_coherence_matrix(tower_freq, tower_points, tower_mean_speeds, k1)

    girder_auto = np.repeat(girder_density[np.newaxis, :], len(girder_points), axis=0)
    girder_vertical_auto = np.repeat(girder_vertical_density[np.newaxis, :], len(girder_points), axis=0)

    girder_fluctuating = generate_multivariate_history(
        frequency=girder_freq,
        auto_density=girder_auto,
        coherence=girder_coherence,
        duration=payload.time_history.duration,
        time_step=payload.time_history.time_step,
        seed=payload.time_history.seed,
        method=payload.time_history.simulation_method,
        ar_order=payload.time_history.ar_order,
    )
    girder_vertical_fluctuating = generate_multivariate_history(
        frequency=girder_vertical_freq,
        auto_density=girder_vertical_auto,
        coherence=girder_coherence,
        duration=payload.time_history.duration,
        time_step=payload.time_history.time_step,
        seed=payload.time_history.seed + 17,
        method=payload.time_history.simulation_method,
        ar_order=payload.time_history.ar_order,
    )
    tower_fluctuating = generate_multivariate_history(
        frequency=tower_freq,
        auto_density=tower_auto,
        coherence=tower_coherence,
        duration=payload.time_history.duration,
        time_step=payload.time_history.time_step,
        seed=payload.time_history.seed + 1,
        method=payload.time_history.simulation_method,
        ar_order=payload.time_history.ar_order,
    )

    girder_total = girder_fluctuating + normative.girder.ud
    tower_total = tower_fluctuating + tower_mean_speeds[:, None]
    time_axis = np.arange(0.0, payload.time_history.duration, payload.time_history.time_step)

    girder_loads = convert_girder_loads(
        mean_velocity=normative.girder.ud,
        along_fluctuating=girder_fluctuating,
        vertical_fluctuating=girder_vertical_fluctuating,
        air_density=payload.site.air_density,
        ch=payload.girder.ch,
        cv=payload.girder.cv,
        cm=payload.girder.cm,
        width=payload.girder.width,
        depth=payload.girder.depth,
        load_model=payload.time_history.load_model,
    )
    tower_loads = convert_tower_loads(
        mean_velocity=tower_mean_speeds,
        along_fluctuating=tower_fluctuating,
        air_density=payload.site.air_density,
        cd=payload.tower.cd,
        width=payload.tower.width,
        load_model=payload.time_history.load_model,
    )

    girder_simulated_density = estimate_simulated_spectrum(
        target_frequency=girder_freq,
        history=girder_fluctuating[0],
        time_step=payload.time_history.time_step,
        target_density=girder_density,
    )
    girder_vertical_simulated_density = estimate_simulated_spectrum(
        target_frequency=girder_vertical_freq,
        history=girder_vertical_fluctuating[0],
        time_step=payload.time_history.time_step,
        target_density=girder_vertical_density,
    )
    tower_simulated_density = estimate_simulated_spectrum(
        target_frequency=tower_freq,
        history=tower_fluctuating[0],
        time_step=payload.time_history.time_step,
        target_density=tower_density,
    )
    spectrum_errors = [
        spectrum_log_relative_error(girder_freq, girder_density, girder_simulated_density),
        spectrum_log_relative_error(girder_vertical_freq, girder_vertical_density, girder_vertical_simulated_density),
        spectrum_log_relative_error(tower_freq, tower_density, tower_simulated_density),
    ]
    coherence_validation = {
        'girder': build_coherence_validation_payload(
            girder_freq,
            girder_coherence,
            girder_fluctuating,
            payload.time_history.time_step,
        ),
        'tower': build_coherence_validation_payload(
            tower_freq,
            tower_coherence,
            tower_fluctuating,
            payload.time_history.time_step,
        ),
    }
    extreme_validation = build_extreme_validation_payload(
        girder_fluctuating,
        tower_fluctuating,
        girder_loads,
        tower_loads,
        normative.girder.sigma_u,
        np.asarray([level.sigma_u for level in normative.tower.levels], dtype=float),
    )

    return {
        'summary': build_summary(normative, payload, girder_points, tower_points, girder_total, tower_total),
        'spectra': build_chart_payload(
            girder_freq,
            girder_density,
            girder_simulated_density,
            girder_vertical_density,
            girder_vertical_simulated_density,
            tower_freq,
            tower_density,
            tower_simulated_density,
            payload,
        ),
        'spectrumValidation': build_spectrum_validation_payload(spectrum_errors),
        'coherenceValidation': coherence_validation,
        'extremeValidation': extreme_validation,
        'coherence': build_coherence_payload(girder_freq, girder_coherence, tower_freq, tower_coherence),
        'histories': build_history_payload(time_axis, girder_total, tower_total, girder_loads, tower_loads),
        'pointHistories': {
            'girder': build_point_payload(girder_points, girder_total, girder_loads),
            'tower': build_point_payload(tower_points, tower_total, tower_loads),
        },
        'exports': build_export_manifest(payload.project.name),
    }
