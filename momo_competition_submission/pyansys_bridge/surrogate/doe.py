"""Design-of-experiments generation utilities."""

from __future__ import annotations

import csv
from pathlib import Path

import numpy as np

from .sampling import lhs_sample, quantize_to_steps


def generate_doe(
    method: str,
    bounds: dict[str, tuple[float, float]],
    *,
    n_samples: int | None = None,
    seed: int | None = None,
    steps: dict[str, float] | None = None,
    levels: int | dict[str, int] | None = None,
    csv_path: str | Path | None = None,
) -> np.ndarray:
    """Generate a DOE matrix using LHS, Sobol, grid, or CSV input."""

    normalized = method.strip().lower().replace("-", "_")
    if normalized == "lhs":
        if n_samples is None:
            raise ValueError("n_samples is required for LHS DOE")
        return lhs_sample(bounds, n_samples, seed=seed, steps=steps)
    if normalized == "sobol":
        if n_samples is None:
            raise ValueError("n_samples is required for Sobol DOE")
        samples = _sobol_unit(n_samples, len(bounds), seed=seed)
        return _scale_unit_samples(samples, bounds, steps)
    if normalized == "grid":
        return grid_sample(bounds, levels=levels, steps=steps)
    if normalized == "csv":
        if csv_path is None:
            raise ValueError("csv_path is required for CSV DOE")
        return read_csv_designs(csv_path, tuple(bounds.keys()))
    raise ValueError("method must be one of: lhs, sobol, grid, csv")


def grid_sample(
    bounds: dict[str, tuple[float, float]],
    *,
    levels: int | dict[str, int] | None = None,
    steps: dict[str, float] | None = None,
) -> np.ndarray:
    axes = []
    for name, (low, high) in bounds.items():
        if high <= low:
            raise ValueError("Each bound must satisfy high > low")
        if steps and name in steps:
            step = steps[name]
            if step <= 0.0:
                raise ValueError("Step sizes must be positive")
            count = int(np.floor((high - low) / step)) + 1
            axis = low + np.arange(count, dtype=float) * step
            if axis[-1] < high:
                axis = np.append(axis, high)
        else:
            level_count = levels.get(name, 3) if isinstance(levels, dict) else (levels or 3)
            if int(level_count) <= 0:
                raise ValueError("Grid levels must be positive")
            axis = np.linspace(low, high, int(level_count))
        axes.append(axis)
    mesh = np.meshgrid(*axes, indexing="ij")
    return np.column_stack([item.reshape(-1) for item in mesh])


def read_csv_designs(path: str | Path, feature_names: tuple[str, ...]) -> np.ndarray:
    source = Path(path)
    with source.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError("CSV DOE file must contain a header row")
        missing = [name for name in feature_names if name not in reader.fieldnames]
        if missing:
            raise ValueError(f"CSV DOE file is missing feature column(s): {', '.join(missing)}")
        rows = [[float(row[name]) for name in feature_names] for row in reader]
    if not rows:
        raise ValueError("CSV DOE file does not contain design rows")
    return np.asarray(rows, dtype=float)


def _scale_unit_samples(
    samples: np.ndarray,
    bounds: dict[str, tuple[float, float]],
    steps: dict[str, float] | None,
) -> np.ndarray:
    values = np.asarray(samples, dtype=float).copy()
    for col, (_, (low, high)) in enumerate(bounds.items()):
        if high <= low:
            raise ValueError("Each bound must satisfy high > low")
        values[:, col] = low + values[:, col] * (high - low)
    if steps:
        return quantize_to_steps(values, bounds, steps)
    return values


def _sobol_unit(n_samples: int, n_dimensions: int, seed: int | None = None) -> np.ndarray:
    if n_samples <= 0:
        raise ValueError("n_samples must be positive")
    try:
        from scipy.stats import qmc

        sampler = qmc.Sobol(d=n_dimensions, scramble=True, seed=seed)
        return sampler.random(n_samples)
    except Exception:
        return _halton_fallback(n_samples, n_dimensions, seed=seed)


def _halton_fallback(n_samples: int, n_dimensions: int, seed: int | None = None) -> np.ndarray:
    bases = _first_primes(n_dimensions)
    samples = np.column_stack(
        [_van_der_corput(np.arange(1, n_samples + 1), base) for base in bases]
    )
    if seed is None:
        return samples
    rng = np.random.default_rng(seed)
    shifts = rng.random(n_dimensions)
    return (samples + shifts) % 1.0


def _van_der_corput(indices: np.ndarray, base: int) -> np.ndarray:
    values = np.zeros(indices.shape[0], dtype=float)
    denominator = 1.0
    remaining = indices.copy()
    while np.any(remaining > 0):
        denominator *= base
        values += (remaining % base) / denominator
        remaining //= base
    return values


def _first_primes(count: int) -> list[int]:
    primes = []
    candidate = 2
    while len(primes) < count:
        if all(candidate % prime for prime in primes if prime * prime <= candidate):
            primes.append(candidate)
        candidate += 1
    return primes
