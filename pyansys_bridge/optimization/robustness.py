"""Robustness scoring."""

from __future__ import annotations

import numpy as np

from pyansys_bridge.optimization.decision import (
    ScenarioDecisionResult,
    TopsisResult,
    scenario_entropy_topsis,
    topsis_details,
)


def robustness_score(samples: np.ndarray) -> float:
    """Higher score means lower response variance."""

    values = np.asarray(samples, dtype=float)
    variance = float(np.var(values))
    return 1.0 / (1.0 + variance)


def topsis_weight_sensitivity(
    objectives: np.ndarray,
    objective_names: tuple[str, ...],
    base_weights: np.ndarray | None = None,
    *,
    relative_delta: float = 0.1,
) -> dict:
    """Audit how TOPSIS ranking changes when objective weights are perturbed."""

    matrix = np.asarray(objectives, dtype=float)
    if matrix.ndim != 2:
        raise ValueError("objectives must be a 2D array")
    if len(objective_names) != matrix.shape[1]:
        raise ValueError("objective_names must match objective columns")
    if relative_delta < 0:
        raise ValueError("relative_delta cannot be negative")

    weights = _normalize_vector(
        np.ones(matrix.shape[1]) if base_weights is None else np.asarray(base_weights, dtype=float),
        label="base_weights",
    )
    base = topsis_details(matrix, weights=weights)
    perturbations = []
    best_index_frequency = {str(base.best_index): 1}
    changed_count = 0
    max_rank_shift = 0

    for column, name in enumerate(objective_names):
        for direction, factor in _perturbation_factors(relative_delta):
            perturbed_weights = weights.copy()
            perturbed_weights[column] *= factor
            perturbed_weights = _normalize_vector(perturbed_weights, label="perturbed_weights")
            details = topsis_details(matrix, weights=perturbed_weights)
            base_rank = _rank_position(details.ranking, base.best_index)
            rank_shift = abs(base_rank - _rank_position(base.ranking, base.best_index))
            max_rank_shift = max(max_rank_shift, rank_shift)
            changed = details.best_index != base.best_index
            changed_count += int(changed)
            best_index_frequency[str(details.best_index)] = best_index_frequency.get(str(details.best_index), 0) + 1
            perturbations.append(
                {
                    "objective": name,
                    "direction": direction,
                    "weights": _named_values(objective_names, perturbed_weights),
                    "best_index": details.best_index,
                    "ranking": _int_list(details.ranking),
                    "best_index_changed": changed,
                    "base_best_rank": base_rank,
                    "rank_shift": rank_shift,
                }
            )

    return {
        "method": "topsis_weight_sensitivity",
        "relative_delta": float(relative_delta),
        "objective_names": list(objective_names),
        "base": _topsis_summary(base, objective_names),
        "perturbations": perturbations,
        "changed_perturbation_count": changed_count,
        "max_rank_shift": max_rank_shift,
        "best_index_frequency": best_index_frequency,
    }


def scenario_weight_sensitivity(
    scenarios: dict[str, np.ndarray],
    scenario_weights: dict[str, float],
    objective_names: dict[str, tuple[str, ...]] | None = None,
    *,
    relative_delta: float = 0.1,
) -> dict:
    """Audit decision sensitivity to scenario weights while preserving local objective weights."""

    if relative_delta < 0:
        raise ValueError("relative_delta cannot be negative")

    base = scenario_entropy_topsis(scenarios, scenario_weights, objective_names=objective_names)
    objective_matrix = _scenario_objective_matrix(scenarios)
    base_topsis = topsis_details(objective_matrix, weights=base.objective_weights)
    scenario_order = tuple(scenarios.keys())
    perturbations = []
    best_index_frequency = {str(base.best_index): 1}
    changed_count = 0
    max_rank_shift = 0

    for scenario in scenario_order:
        for direction, factor in _perturbation_factors(relative_delta):
            perturbed_weights = dict(scenario_weights)
            perturbed_weights[scenario] = float(perturbed_weights[scenario]) * factor
            details = scenario_entropy_topsis(scenarios, perturbed_weights, objective_names=objective_names)
            topsis_result = topsis_details(objective_matrix, weights=details.objective_weights)
            base_rank = _rank_position(topsis_result.ranking, base.best_index)
            rank_shift = abs(base_rank - _rank_position(base_topsis.ranking, base.best_index))
            max_rank_shift = max(max_rank_shift, rank_shift)
            changed = details.best_index != base.best_index
            changed_count += int(changed)
            best_index_frequency[str(details.best_index)] = best_index_frequency.get(str(details.best_index), 0) + 1
            perturbations.append(
                {
                    "scenario": scenario,
                    "direction": direction,
                    "scenario_weights": dict(details.scenario_weights),
                    "objective_weights": _named_values(details.objective_names, details.objective_weights),
                    "best_index": details.best_index,
                    "best_index_changed": changed,
                    "base_best_rank": base_rank,
                    "rank_shift": rank_shift,
                }
            )

    return {
        "method": "scenario_weight_sensitivity",
        "relative_delta": float(relative_delta),
        "base": _scenario_summary(base, base_topsis),
        "perturbations": perturbations,
        "changed_perturbation_count": changed_count,
        "max_rank_shift": max_rank_shift,
        "best_index_frequency": best_index_frequency,
    }


def _normalize_vector(values: np.ndarray, *, label: str) -> np.ndarray:
    weights = np.asarray(values, dtype=float)
    if weights.ndim != 1:
        raise ValueError(f"{label} must be a 1D array")
    if np.any(weights < 0):
        raise ValueError(f"{label} cannot contain negative values")
    total = float(np.sum(weights))
    if total <= 0:
        raise ValueError(f"{label} must have a positive sum")
    return weights / total


def _perturbation_factors(relative_delta: float) -> tuple[tuple[str, float], tuple[str, float]]:
    decrease = max(1.0 - float(relative_delta), 1.0e-12)
    increase = 1.0 + float(relative_delta)
    return (("decrease", decrease), ("increase", increase))


def _topsis_summary(result: TopsisResult, objective_names: tuple[str, ...]) -> dict:
    return {
        "best_index": result.best_index,
        "ranking": _int_list(result.ranking),
        "weights": _named_values(objective_names, result.weights),
        "closeness": _float_list(result.closeness),
        "normalization": {
            "method": result.normalization_method,
            "objective_min": _named_values(objective_names, result.normalization_min),
            "objective_span": _named_values(objective_names, result.normalization_span),
        },
    }


def _scenario_summary(result: ScenarioDecisionResult, topsis_result: TopsisResult) -> dict:
    return {
        "best_index": result.best_index,
        "ranking": _int_list(topsis_result.ranking),
        "scenario_weights": dict(result.scenario_weights),
        "objective_names": list(result.objective_names),
        "objective_weights": _named_values(result.objective_names, result.objective_weights),
    }


def _named_values(names: tuple[str, ...], values: np.ndarray) -> dict[str, float]:
    return {name: float(value) for name, value in zip(names, values)}


def _int_list(values: np.ndarray) -> list[int]:
    return [int(value) for value in values.tolist()]


def _float_list(values: np.ndarray) -> list[float]:
    return [float(value) for value in values.tolist()]


def _scenario_objective_matrix(scenarios: dict[str, np.ndarray]) -> np.ndarray:
    return np.hstack([np.asarray(values, dtype=float) for values in scenarios.values()])


def _rank_position(ranking: np.ndarray, index: int) -> int:
    matches = np.where(ranking == index)[0]
    if len(matches) == 0:
        raise ValueError(f"index {index} is not present in ranking")
    return int(matches[0])
