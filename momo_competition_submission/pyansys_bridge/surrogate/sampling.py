"""Sampling utilities."""

from __future__ import annotations

import numpy as np


DEFAULT_TOTAL_DAMPER_BOUNDS = {"c": (5.0e4, 2.0e6), "alpha": (0.3, 1.0)}
DEFAULT_TOTAL_DAMPER_STEPS = {"c": 1.0e4, "alpha": 0.05}


def lhs_sample(
    bounds: dict[str, tuple[float, float]],
    n_samples: int,
    seed: int | None = None,
    steps: dict[str, float] | None = None,
) -> np.ndarray:
    """Generate Latin hypercube samples for ordered parameter bounds."""

    if n_samples <= 0:
        raise ValueError("n_samples must be positive")
    rng = np.random.default_rng(seed)
    samples = np.zeros((n_samples, len(bounds)), dtype=float)
    for col, (_, (low, high)) in enumerate(bounds.items()):
        if high <= low:
            raise ValueError("Each bound must satisfy high > low")
        cut = np.linspace(0.0, 1.0, n_samples + 1)
        points = cut[:-1] + rng.random(n_samples) * (1.0 / n_samples)
        rng.shuffle(points)
        samples[:, col] = low + points * (high - low)
    if steps:
        samples = quantize_to_steps(samples, bounds, steps)
    return samples


def quantize_to_steps(
    samples: np.ndarray,
    bounds: dict[str, tuple[float, float]],
    steps: dict[str, float],
) -> np.ndarray:
    """Snap sampled design points to configured engineering step sizes."""

    values = np.asarray(samples, dtype=float).copy()
    if values.ndim != 2:
        raise ValueError("samples must be a 2D array")
    if values.shape[1] != len(bounds):
        raise ValueError("bounds must match the sample column count")

    for col, (name, (low, high)) in enumerate(bounds.items()):
        if high <= low:
            raise ValueError("Each bound must satisfy high > low")
        step = steps.get(name)
        if step is None:
            continue
        if step <= 0:
            raise ValueError("Step sizes must be positive")
        snapped = low + np.round((values[:, col] - low) / step) * step
        values[:, col] = np.clip(snapped, low, high)
    return values
