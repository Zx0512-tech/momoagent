"""Surrogate-based multi-scenario optimization workflow."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Protocol

import numpy as np

from pyansys_bridge.optimization.decision import ScenarioDecisionResult, entropy_weights, topsis
from pyansys_bridge.optimization.nsga2 import pareto_front
from pyansys_bridge.optimization.pymoo_adapter import optimize_with_production_nsga2
from pyansys_bridge.surrogate.sampling import quantize_to_steps


class _Predictor(Protocol):
    def predict(self, x: np.ndarray): ...


NO_FEASIBLE_CANDIDATES_MESSAGE = "No feasible candidates remain after applying objective limits"


class NoFeasibleCandidatesError(ValueError):
    """Raised when surrogate optimization cannot satisfy objective limits."""

    def __init__(self, message: str, diagnostics: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.diagnostics = diagnostics or {}


@dataclass(frozen=True)
class ScenarioSurrogate:
    """One surrogate objective for one load scenario.

    ``weight`` is a decision-layer scenario weight. It does not scale surrogate
    predictions before objective limits or Pareto filtering.
    ``decision_scenario`` can group several load cases, such as wind and
    traffic, into one operation-state decision weight.
    """

    scenario: str
    objective: str
    model: _Predictor
    weight: float = 1.0
    decision_scenario: str | None = None

    @property
    def label(self) -> str:
        return f"{self.scenario}:{self.objective}"

    @property
    def decision_label(self) -> str:
        return self.decision_scenario or self.scenario


@dataclass(frozen=True)
class SurrogateOptimizationResult:
    """Pareto candidates and decision output from surrogate optimization."""

    parameter_names: tuple[str, ...]
    objective_names: tuple[str, ...]
    candidates: np.ndarray
    objectives: np.ndarray
    pareto_designs: np.ndarray
    pareto_objectives: np.ndarray
    decision_weights: np.ndarray
    best_design: np.ndarray
    best_objectives: np.ndarray
    scenario_decision: ScenarioDecisionResult | None = None
    candidate_filter: dict[str, object] | None = None


def optimize_with_surrogates(
    bounds: dict[str, tuple[float, float]],
    surrogates: list[ScenarioSurrogate],
    n_candidates: int = 200,
    seed: int | None = None,
    steps: dict[str, float] | None = None,
    objective_limits: dict[str, float] | None = None,
    objective_limit_relative_tolerance: float = 0.0,
    training_designs: np.ndarray | None = None,
    max_normalized_doe_distance: float | None = None,
) -> SurrogateOptimizationResult:
    """Generate candidates, evaluate surrogate objectives, and decide Pareto best."""

    if not surrogates:
        raise ValueError("At least one scenario surrogate is required")
    if n_candidates <= 0:
        raise ValueError("n_candidates must be positive")
    if objective_limit_relative_tolerance < 0:
        raise ValueError("objective_limit_relative_tolerance cannot be negative")
    if max_normalized_doe_distance is not None and max_normalized_doe_distance <= 0:
        raise ValueError("max_normalized_doe_distance must be positive")
    if max_normalized_doe_distance is not None and training_designs is None:
        raise ValueError("training_designs are required when DOE support filtering is enabled")
    objective_names = tuple(surrogate.label for surrogate in surrogates)
    constraints, constraint_columns = _pymoo_constraints(
        objective_names,
        objective_limits or {},
        relative_tolerance=objective_limit_relative_tolerance,
    )

    def evaluator(design: np.ndarray) -> dict[str, dict[str, float]]:
        design_matrix = np.asarray(design, dtype=float).reshape(1, -1)
        if steps:
            design_matrix = quantize_to_steps(design_matrix, bounds, steps)
        _, objective_matrix = evaluate_scenario_surrogates(design_matrix, surrogates)
        row = objective_matrix[0]
        return {
            "objectives": {
                name: float(value)
                for name, value in zip(objective_names, row)
            },
            "constraints": {
                constraint_name: float(row[column])
                for constraint_name, column in constraint_columns.items()
            },
        }

    candidates = _discrete_design_grid(bounds, steps, max_points=n_candidates)
    if candidates is None:
        population_size = max(4, min(int(n_candidates), max(8, len(bounds) * 4)))
        generations = max(2, int(math.ceil(n_candidates / population_size)))
        pymoo_result = optimize_with_production_nsga2(
            bounds=bounds,
            evaluator=evaluator,
            objective_names=objective_names,
            constraints=constraints,
            population_size=population_size,
            generations=generations,
            seed=seed,
        )
        candidates = _quantized_unique_designs(pymoo_result.candidates, bounds, steps)
    _, objectives = evaluate_scenario_surrogates(candidates, surrogates)
    candidate_filter = _candidate_filter_diagnostics(
        candidates,
        objectives,
        objective_names,
        objective_limits or {},
        relative_tolerance=objective_limit_relative_tolerance,
    )
    candidates, objectives = _filter_by_limits(
        candidates,
        objectives,
        objective_names,
        objective_limits or {},
        relative_tolerance=objective_limit_relative_tolerance,
    )
    if max_normalized_doe_distance is not None:
        physical_accepted_count = len(candidates)
        support_keep, support_filter = _doe_support_filter(
            candidates,
            training_designs,
            bounds,
            max_distance=max_normalized_doe_distance,
        )
        candidates = candidates[support_keep]
        objectives = objectives[support_keep]
        candidate_filter = {
            **candidate_filter,
            "physical_accepted_candidate_count": int(physical_accepted_count),
            "accepted_candidate_count": int(len(candidates)),
            "rejected_candidate_count": int(candidate_filter["total_candidate_count"] - len(candidates)),
            "doe_support_filter": support_filter,
        }
    if len(candidates) == 0:
        raise NoFeasibleCandidatesError(NO_FEASIBLE_CANDIDATES_MESSAGE, diagnostics=candidate_filter)

    front = pareto_front(objectives)
    pareto_designs = candidates[front]
    pareto_objectives = objectives[front]
    scenario_decision = scenario_decision_for_surrogates(pareto_objectives, surrogates)
    decision_weights = scenario_decision.objective_weights
    best_front_index = scenario_decision.best_index

    return SurrogateOptimizationResult(
        parameter_names=tuple(bounds.keys()),
        objective_names=objective_names,
        candidates=candidates,
        objectives=objectives,
        pareto_designs=pareto_designs,
        pareto_objectives=pareto_objectives,
        decision_weights=decision_weights,
        best_design=pareto_designs[best_front_index],
        best_objectives=pareto_objectives[best_front_index],
        scenario_decision=scenario_decision,
        candidate_filter=candidate_filter,
    )


def evaluate_scenario_surrogates(
    designs: np.ndarray,
    surrogates: list[ScenarioSurrogate],
) -> tuple[tuple[str, ...], np.ndarray]:
    """Evaluate raw minimization objectives for all surrogate models."""

    x = np.asarray(designs, dtype=float)
    if x.ndim != 2:
        raise ValueError("designs must be a 2D array")
    columns = []
    names = []
    for surrogate in surrogates:
        if surrogate.weight <= 0:
            raise ValueError("Scenario surrogate weights must be positive")
        prediction = np.asarray(surrogate.model.predict(x), dtype=float)
        if prediction.ndim != 1 or prediction.shape[0] != x.shape[0]:
            raise ValueError("Each surrogate prediction must be a 1D array aligned with designs")
        columns.append(prediction)
        names.append(surrogate.label)
    return tuple(names), np.column_stack(columns)


def decision_weights_for_surrogates(
    objectives: np.ndarray,
    surrogates: list[ScenarioSurrogate],
) -> np.ndarray:
    """Build TOPSIS weights from scenario weights and within-scenario entropy."""

    return scenario_decision_for_surrogates(objectives, surrogates).objective_weights


def scenario_decision_for_surrogates(
    objectives: np.ndarray,
    surrogates: list[ScenarioSurrogate],
) -> ScenarioDecisionResult:
    """Build the joint scenario decision record for surrogate objectives."""

    matrix = np.asarray(objectives, dtype=float)
    if matrix.ndim != 2:
        raise ValueError("objectives must be a 2D array")
    if matrix.shape[1] != len(surrogates):
        raise ValueError("objectives column count must match surrogate count")

    columns_by_scenario: dict[str, list[int]] = {}
    scenario_weights: dict[str, float] = {}
    objective_names_by_scenario: dict[str, list[str]] = {}
    for index, surrogate in enumerate(surrogates):
        weight = float(surrogate.weight)
        if weight <= 0:
            raise ValueError("Scenario surrogate weights must be positive")
        decision_scenario = surrogate.decision_label
        if decision_scenario in scenario_weights and not np.isclose(scenario_weights[decision_scenario], weight):
            raise ValueError("Scenario surrogate weights must be consistent within a scenario")
        scenario_weights[decision_scenario] = weight
        columns_by_scenario.setdefault(decision_scenario, []).append(index)
        objective_names_by_scenario.setdefault(decision_scenario, []).append(surrogate.label)

    total_weight = sum(scenario_weights.values())
    normalized_scenario_weights = {
        scenario: weight / total_weight
        for scenario, weight in scenario_weights.items()
    }
    decision_weights = np.zeros(len(surrogates), dtype=float)
    local_weights_by_scenario: dict[str, np.ndarray] = {}
    names_by_scenario: dict[str, tuple[str, ...]] = {}
    for scenario, columns in columns_by_scenario.items():
        local_weights = entropy_weights(matrix[:, columns])
        local_weights_by_scenario[scenario] = local_weights
        names_by_scenario[scenario] = tuple(objective_names_by_scenario[scenario])
        decision_weights[columns] = local_weights * normalized_scenario_weights[scenario]
    return ScenarioDecisionResult(
        best_index=topsis(matrix, decision_weights),
        objective_names=tuple(surrogate.label for surrogate in surrogates),
        objective_weights=decision_weights,
        weighted_objectives=matrix * decision_weights,
        scenario_objective_weights=local_weights_by_scenario,
        scenario_objective_names=names_by_scenario,
        scenario_weights=normalized_scenario_weights,
    )


def _pymoo_constraints(
    objective_names: tuple[str, ...],
    objective_limits: dict[str, float],
    *,
    relative_tolerance: float,
) -> tuple[dict[str, float], dict[str, int]]:
    constraints = {}
    columns = {}
    for index, (column, limit) in enumerate(
        _objective_limit_columns(objective_names, objective_limits)
    ):
        constraint_name = f"limit_{index}"
        limit_value = float(limit)
        constraints[constraint_name] = limit_value + abs(limit_value) * relative_tolerance
        columns[constraint_name] = column
    return constraints, columns


def _objective_limit_columns(
    objective_names: tuple[str, ...],
    objective_limits: dict[str, float],
) -> list[tuple[int, float]]:
    name_to_col = {name: col for col, name in enumerate(objective_names)}
    short_name_to_cols: dict[str, list[int]] = {}
    for col, full_name in enumerate(objective_names):
        _, _, short_name = full_name.partition(":")
        short_name_to_cols.setdefault(short_name or full_name, []).append(col)

    resolved = []
    for name, limit in objective_limits.items():
        columns = [name_to_col[name]] if name in name_to_col else short_name_to_cols.get(name, [])
        if not columns:
            raise ValueError(f"Unknown objective limit: {name}")
        resolved.extend((column, float(limit)) for column in columns)
    return resolved


def _quantized_unique_designs(
    designs: np.ndarray,
    bounds: dict[str, tuple[float, float]],
    steps: dict[str, float] | None,
) -> np.ndarray:
    values = np.asarray(designs, dtype=float)
    if values.size == 0:
        return np.empty((0, len(bounds)), dtype=float)
    if values.ndim == 1:
        values = values.reshape(1, -1)
    if steps:
        values = quantize_to_steps(values, bounds, steps)
    _, indexes = np.unique(values, axis=0, return_index=True)
    return values[np.sort(indexes)]


def _discrete_design_grid(
    bounds: dict[str, tuple[float, float]],
    steps: dict[str, float] | None,
    *,
    max_points: int,
) -> np.ndarray | None:
    if not steps or any(name not in steps for name in bounds):
        return None
    axes = []
    point_count = 1
    for name, (low, high) in bounds.items():
        step = float(steps[name])
        if step <= 0:
            raise ValueError("Step sizes must be positive")
        count = int(np.floor((high - low) / step + 1.0e-12)) + 1
        point_count *= count
        if point_count > max_points:
            return None
        axes.append(low + np.arange(count, dtype=float) * step)
    mesh = np.meshgrid(*axes, indexing="ij")
    return np.column_stack([axis.reshape(-1) for axis in mesh])


def _doe_support_filter(
    candidates: np.ndarray,
    training_designs: np.ndarray,
    bounds: dict[str, tuple[float, float]],
    *,
    max_distance: float,
) -> tuple[np.ndarray, dict[str, object]]:
    candidate_values = np.asarray(candidates, dtype=float)
    training_values = np.asarray(training_designs, dtype=float)
    if training_values.ndim != 2 or training_values.shape[1] != len(bounds):
        raise ValueError("training_designs must be a 2D matrix aligned with bounds")
    if training_values.shape[0] == 0:
        raise ValueError("training_designs cannot be empty")
    spans = np.asarray([high - low for low, high in bounds.values()], dtype=float)
    if np.any(spans <= 0.0):
        raise ValueError("bounds must have positive spans for DOE support filtering")
    lows = np.asarray([low for low, _high in bounds.values()], dtype=float)
    normalized_candidates = (candidate_values - lows) / spans
    normalized_training = (training_values - lows) / spans
    nearest_distances = np.min(
        np.linalg.norm(
            normalized_candidates[:, None, :] - normalized_training[None, :, :],
            axis=2,
        ),
        axis=1,
    )
    keep = nearest_distances <= float(max_distance) + 1.0e-12
    return keep, {
        "enabled": True,
        "metric": "normalized_euclidean_nearest_doe",
        "max_distance": float(max_distance),
        "accepted_count": int(np.count_nonzero(keep)),
        "rejected_count": int(len(keep) - np.count_nonzero(keep)),
        "candidate_min_distance": float(np.min(nearest_distances)) if nearest_distances.size else None,
        "candidate_max_distance": float(np.max(nearest_distances)) if nearest_distances.size else None,
    }


def _filter_by_limits(
    candidates: np.ndarray,
    objectives: np.ndarray,
    objective_names: tuple[str, ...],
    objective_limits: dict[str, float],
    *,
    relative_tolerance: float = 0.0,
) -> tuple[np.ndarray, np.ndarray]:
    if not objective_limits:
        return candidates, objectives
    keep = np.ones(objectives.shape[0], dtype=bool)
    for col, limit in _objective_limit_columns(objective_names, objective_limits):
        limit_value = float(limit)
        allowed_value = limit_value + abs(limit_value) * relative_tolerance
        keep &= objectives[:, col] <= allowed_value
    return candidates[keep], objectives[keep]


def _candidate_filter_diagnostics(
    candidates: np.ndarray,
    objectives: np.ndarray,
    objective_names: tuple[str, ...],
    objective_limits: dict[str, float],
    *,
    relative_tolerance: float = 0.0,
) -> dict[str, object]:
    total_count = int(np.asarray(candidates).shape[0])
    keep = np.ones(total_count, dtype=bool)
    objective_ranges = {}
    limit_checks = []
    for column, name in enumerate(objective_names):
        values = np.asarray(objectives[:, column], dtype=float) if total_count else np.asarray([], dtype=float)
        objective_ranges[name] = {
            "min": float(np.min(values)) if values.size else None,
            "max": float(np.max(values)) if values.size else None,
        }
    for col, limit in _objective_limit_columns(objective_names, objective_limits):
        limit_value = float(limit)
        allowed_value = limit_value + abs(limit_value) * relative_tolerance
        column_values = np.asarray(objectives[:, col], dtype=float) if total_count else np.asarray([], dtype=float)
        column_keep = column_values <= allowed_value if column_values.size else np.asarray([], dtype=bool)
        keep &= column_keep if column_keep.size else keep
        limit_checks.append(
            {
                "objective": objective_names[col],
                "limit": limit_value,
                "allowed": float(allowed_value),
                "candidate_min": float(np.min(column_values)) if column_values.size else None,
                "candidate_max": float(np.max(column_values)) if column_values.size else None,
                "accepted_count": int(np.count_nonzero(column_keep)) if column_keep.size else 0,
                "rejected_count": int(total_count - np.count_nonzero(column_keep)) if column_keep.size else total_count,
            }
        )
    accepted_count = int(np.count_nonzero(keep)) if total_count else 0
    return {
        "total_candidate_count": total_count,
        "accepted_candidate_count": accepted_count,
        "rejected_candidate_count": int(total_count - accepted_count),
        "objective_limits": dict(objective_limits),
        "objective_limit_relative_tolerance": float(relative_tolerance),
        "objective_ranges": objective_ranges,
        "limit_checks": limit_checks,
    }
