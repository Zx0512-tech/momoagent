"""Acquisition functions."""

from __future__ import annotations

import math

import numpy as np


def _normal_pdf(z: np.ndarray) -> np.ndarray:
    return np.exp(-0.5 * z * z) / math.sqrt(2.0 * math.pi)


def _normal_cdf(z: np.ndarray) -> np.ndarray:
    erf = np.vectorize(math.erf)
    return 0.5 * (1.0 + erf(z / math.sqrt(2.0)))


def expected_improvement(mean, std, best_value: float, minimize: bool = True, xi: float = 0.0) -> np.ndarray:
    """Compute expected improvement for minimization or maximization."""

    mean = np.asarray(mean, dtype=float)
    std = np.asarray(std, dtype=float)
    std = np.maximum(std, 1.0e-12)
    improvement = best_value - mean - xi if minimize else mean - best_value - xi
    z = improvement / std
    return improvement * _normal_cdf(z) + std * _normal_pdf(z)


def weighted_expected_improvement(mean, std, best_values, weights=None, minimize: bool = True, xi: float = 0.0) -> np.ndarray:
    """Combine per-objective expected improvements with normalized weights."""

    mean_matrix = np.asarray(mean, dtype=float)
    std_matrix = np.asarray(std, dtype=float)
    best_vector = np.asarray(best_values, dtype=float).reshape(-1)
    if mean_matrix.ndim != 2 or std_matrix.ndim != 2:
        raise ValueError("mean and std must be 2D arrays for weighted expected improvement")
    if mean_matrix.shape != std_matrix.shape:
        raise ValueError("mean and std must have the same shape")
    if mean_matrix.shape[1] != best_vector.shape[0]:
        raise ValueError("best_values length must match objective count")
    objective_weights = _normalized_weights(weights, mean_matrix.shape[1])
    columns = [
        expected_improvement(
            mean_matrix[:, index],
            std_matrix[:, index],
            best_value=float(best_vector[index]),
            minimize=minimize,
            xi=xi,
        )
        for index in range(mean_matrix.shape[1])
    ]
    return np.column_stack(columns) @ objective_weights


def _normalized_weights(weights, objective_count: int) -> np.ndarray:
    if weights is None:
        return np.ones(objective_count) / objective_count
    values = np.asarray(weights, dtype=float).reshape(-1)
    if values.shape[0] != objective_count:
        raise ValueError("objective_weights length must match objective count")
    total = float(np.sum(values))
    if total <= 0.0:
        raise ValueError("objective_weights must sum to a positive value")
    return values / total
