"""Active-learning infill point selection strategies."""

from __future__ import annotations

import numpy as np


def allocate_infill_budget_by_uncertainty(
    surrogates,
    candidates,
    *,
    total_budget: int,
) -> tuple[int, ...]:
    """按各代理模型的不确定性分配主动学习加点预算。"""

    surrogate_list = list(surrogates)
    if not surrogate_list:
        raise ValueError("At least one surrogate is required")
    if total_budget <= 0:
        raise ValueError("total_budget must be positive")

    candidate_matrix = np.asarray(candidates, dtype=float)
    if candidate_matrix.ndim != 2:
        raise ValueError("candidates must be a 2D array")

    scores = np.array([_mean_prediction_std(surrogate, candidate_matrix) for surrogate in surrogate_list], dtype=float)
    if total_budget < len(surrogate_list):
        allocation = np.zeros(len(surrogate_list), dtype=int)
        for index in np.argsort(scores)[::-1][:total_budget]:
            allocation[index] = 1
        return tuple(int(value) for value in allocation)

    allocation = np.ones(len(surrogate_list), dtype=int)
    remaining = total_budget - len(surrogate_list)
    if remaining == 0:
        return tuple(int(value) for value in allocation)

    if np.all(scores <= 0.0):
        weights = np.ones(len(surrogate_list), dtype=float) / len(surrogate_list)
    else:
        weights = scores / float(np.sum(scores))
    raw = weights * remaining
    increments = np.floor(raw).astype(int)
    allocation += increments
    leftovers = remaining - int(np.sum(increments))
    if leftovers:
        for index in np.argsort(raw - increments)[::-1][:leftovers]:
            allocation[index] += 1
    return tuple(int(value) for value in allocation)


def select_active_learning_points(
    candidates,
    *,
    n_points: int,
    surrogate=None,
    pareto_designs=None,
    seed: int | None = None,
    random_fraction: float = 0.2,
) -> np.ndarray:
    """Select infill points from uncertainty, Pareto-nearby, and random exploration."""

    candidate_matrix = np.asarray(candidates, dtype=float)
    if candidate_matrix.ndim != 2:
        raise ValueError("candidates must be a 2D array")
    if n_points <= 0:
        raise ValueError("n_points must be positive")
    if n_points > candidate_matrix.shape[0]:
        raise ValueError("n_points cannot exceed candidate count")
    if not 0.0 <= random_fraction <= 1.0:
        raise ValueError("random_fraction must be between 0 and 1")

    selected: list[np.ndarray] = []
    seen: set[tuple[float, ...]] = set()

    random_count = min(n_points, max(1, int(round(n_points * random_fraction)))) if random_fraction > 0 else 0
    pareto_count = 1 if pareto_designs is not None and n_points - random_count > 0 else 0
    uncertainty_count = n_points - random_count - pareto_count

    for index in _uncertainty_order(candidate_matrix, surrogate)[:uncertainty_count]:
        _append_unique(selected, seen, candidate_matrix[index])

    if pareto_count:
        for index in _pareto_nearby_order(candidate_matrix, np.asarray(pareto_designs, dtype=float)):
            if _append_unique(selected, seen, candidate_matrix[index]) and len(selected) >= uncertainty_count + pareto_count:
                break

    rng = np.random.default_rng(seed)
    for index in rng.permutation(candidate_matrix.shape[0]):
        if len(selected) >= n_points:
            break
        _append_unique(selected, seen, candidate_matrix[index])

    if len(selected) < n_points:
        raise ValueError("Unable to select enough unique active-learning candidates")
    return np.vstack(selected)


def _uncertainty_order(candidates: np.ndarray, surrogate) -> np.ndarray:
    if surrogate is None:
        return np.arange(candidates.shape[0])
    try:
        _, std = surrogate.predict(candidates, return_std=True)
    except TypeError:
        return np.arange(candidates.shape[0])
    return np.argsort(np.asarray(std, dtype=float).reshape(-1))[::-1]


def _mean_prediction_std(surrogate, candidates: np.ndarray) -> float:
    try:
        _, std = surrogate.predict(candidates, return_std=True)
    except TypeError as exc:
        raise ValueError("Surrogate must support predict(..., return_std=True)") from exc
    std_values = np.asarray(std, dtype=float)
    if std_values.shape[0] != candidates.shape[0]:
        raise ValueError("Surrogate uncertainty must align with candidate count")
    return float(np.mean(np.maximum(std_values.reshape(candidates.shape[0], -1), 0.0)))


def _pareto_nearby_order(candidates: np.ndarray, pareto_designs: np.ndarray) -> np.ndarray:
    pareto = np.asarray(pareto_designs, dtype=float)
    if pareto.ndim == 1:
        pareto = pareto.reshape(1, -1)
    if pareto.shape[1] != candidates.shape[1]:
        raise ValueError("pareto_designs must have the same column count as candidates")
    distances = np.min(
        np.linalg.norm(candidates[:, None, :] - pareto[None, :, :], axis=2),
        axis=1,
    )
    return np.argsort(distances)


def _append_unique(selected: list[np.ndarray], seen: set[tuple[float, ...]], point: np.ndarray) -> bool:
    key = tuple(float(value) for value in point)
    if key in seen:
        return False
    seen.add(key)
    selected.append(np.asarray(point, dtype=float).copy())
    return True
