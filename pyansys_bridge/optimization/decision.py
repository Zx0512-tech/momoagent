"""Decision helpers."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from pyansys_bridge.models import AnalysisResult


@dataclass(frozen=True)
class ScenarioDecisionResult:
    """Decision result with scenario-level and objective-level weights."""

    best_index: int
    objective_names: tuple[str, ...]
    objective_weights: np.ndarray
    weighted_objectives: np.ndarray
    scenario_objective_weights: dict[str, np.ndarray]
    scenario_objective_names: dict[str, tuple[str, ...]]
    scenario_weights: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class TopsisResult:
    """Auditable TOPSIS result for minimization objectives."""

    best_index: int
    ranking: np.ndarray
    closeness: np.ndarray
    weights: np.ndarray
    normalized: np.ndarray
    normalization_method: str
    normalization_min: np.ndarray
    normalization_span: np.ndarray
    weighted: np.ndarray
    distance_to_ideal: np.ndarray
    distance_to_nadir: np.ndarray


@dataclass(frozen=True)
class VerifiedSolverDecisionResult:
    """TOPSIS result built only from verified real-solver outputs."""

    best_case_id: str
    case_ids: tuple[str, ...]
    objective_names: tuple[str, ...]
    objective_matrix: np.ndarray
    topsis: TopsisResult


def entropy_weights(objectives: np.ndarray) -> np.ndarray:
    """Compute entropy weights from minimization objective values."""

    matrix = np.asarray(objectives, dtype=float)
    if matrix.ndim != 2:
        raise ValueError("objectives must be a 2D array")
    if matrix.shape[0] < 2:
        return np.ones(matrix.shape[1]) / matrix.shape[1]

    span = np.maximum(matrix.max(axis=0) - matrix.min(axis=0), 1.0e-12)
    benefit = (matrix.max(axis=0) - matrix) / span
    benefit = benefit + 1.0e-12
    column_sum = benefit.sum(axis=0)
    probability = benefit / column_sum
    entropy = -np.sum(probability * np.log(probability), axis=0) / np.log(matrix.shape[0])
    diversity = 1.0 - entropy
    if float(np.sum(diversity)) <= 1.0e-12:
        return np.ones(matrix.shape[1]) / matrix.shape[1]
    return diversity / np.sum(diversity)


def topsis(objectives: np.ndarray, weights: np.ndarray | None = None) -> int:
    """Return the best row index for minimization objectives."""

    return topsis_details(objectives, weights=weights).best_index


def topsis_details(objectives: np.ndarray, weights: np.ndarray | None = None) -> TopsisResult:
    """Return full TOPSIS ranking details for minimization objectives."""

    matrix = np.asarray(objectives, dtype=float)
    if matrix.ndim != 2:
        raise ValueError("objectives must be a 2D array")
    if weights is None:
        weights = np.ones(matrix.shape[1]) / matrix.shape[1]
    weights = np.asarray(weights, dtype=float)
    weights = weights / np.sum(weights)
    objective_min = matrix.min(axis=0)
    span = np.maximum(matrix.max(axis=0) - objective_min, 1.0e-12)
    normalized = (matrix - objective_min) / span
    weighted = normalized * weights
    ideal = weighted.min(axis=0)
    nadir = weighted.max(axis=0)
    dist_ideal = np.linalg.norm(weighted - ideal, axis=1)
    dist_nadir = np.linalg.norm(weighted - nadir, axis=1)
    score = dist_nadir / np.maximum(dist_ideal + dist_nadir, 1.0e-12)
    ranking = np.argsort(-score)
    return TopsisResult(
        best_index=int(ranking[0]),
        ranking=ranking,
        closeness=score,
        weights=weights,
        normalized=normalized,
        normalization_method="min_max_minimization",
        normalization_min=objective_min,
        normalization_span=span,
        weighted=weighted,
        distance_to_ideal=dist_ideal,
        distance_to_nadir=dist_nadir,
    )


def entropy_topsis(objectives: np.ndarray) -> tuple[int, np.ndarray]:
    """Return the best row index and entropy-derived objective weights."""

    weights = entropy_weights(objectives)
    return topsis(objectives, weights=weights), weights


def topsis_verified_solver_outputs(
    results: list[AnalysisResult] | tuple[AnalysisResult, ...],
    objective_names: tuple[str, ...],
    weights: np.ndarray | None = None,
) -> VerifiedSolverDecisionResult:
    """Rank completed results only when every row is a verified solver output."""

    if not results:
        raise ValueError("At least one verified solver output is required")
    if not objective_names:
        raise ValueError("At least one objective is required")
    matrix = []
    case_ids = []
    from pyansys_bridge.optimization.fem_review import is_verified_solver_output

    for result in results:
        missing = [name for name in objective_names if name not in result.objectives]
        if missing:
            raise ValueError(f"Result {result.case_id} is missing objective(s): {', '.join(missing)}")
        unverified = [
            name
            for name in objective_names
            if not is_verified_solver_output(result, name)
        ]
        if unverified:
            raise ValueError(
                f"Result {result.case_id} is not a verified solver output for objective(s): "
                f"{', '.join(unverified)}"
            )
        case_ids.append(result.case_id)
        matrix.append([float(result.objectives[name]) for name in objective_names])
    objective_matrix = np.asarray(matrix, dtype=float)
    details = topsis_details(objective_matrix, weights=weights)
    return VerifiedSolverDecisionResult(
        best_case_id=case_ids[details.best_index],
        case_ids=tuple(case_ids),
        objective_names=tuple(objective_names),
        objective_matrix=objective_matrix,
        topsis=details,
    )


def combine_scenario_objectives(
    scenarios: dict[str, np.ndarray],
    scenario_weights: dict[str, float],
) -> np.ndarray:
    """Concatenate weighted objective matrices from multiple load scenarios."""

    if not scenarios:
        raise ValueError("At least one scenario objective matrix is required")

    matrices = []
    row_count = None
    for name, objectives in scenarios.items():
        if name not in scenario_weights:
            raise ValueError(f"Missing scenario weight: {name}")
        matrix = np.asarray(objectives, dtype=float)
        if matrix.ndim != 2:
            raise ValueError("Each scenario objective matrix must be 2D")
        if row_count is None:
            row_count = matrix.shape[0]
        elif matrix.shape[0] != row_count:
            raise ValueError("Scenario objective matrices must have the same row count")
        matrices.append(matrix * float(scenario_weights[name]))

    return np.hstack(matrices)


def scenario_entropy_topsis(
    scenarios: dict[str, np.ndarray],
    scenario_weights: dict[str, float],
    objective_names: dict[str, tuple[str, ...]] | None = None,
) -> ScenarioDecisionResult:
    """Apply entropy weights within each scenario, then TOPSIS across scenarios."""

    if not scenarios:
        raise ValueError("At least one scenario objective matrix is required")

    normalized_scenario_weights = _normalize_scenario_weights(scenario_weights)
    matrices = []
    weights = []
    labels = []
    local_weights_by_scenario = {}
    objective_names_by_scenario = {}
    row_count = None
    for scenario, values in scenarios.items():
        if scenario not in normalized_scenario_weights:
            raise ValueError(f"Missing scenario weight: {scenario}")
        matrix = np.asarray(values, dtype=float)
        if matrix.ndim != 2:
            raise ValueError("Each scenario objective matrix must be 2D")
        if row_count is None:
            row_count = matrix.shape[0]
        elif matrix.shape[0] != row_count:
            raise ValueError("Scenario objective matrices must have the same row count")
        names = _objective_names_for_scenario(scenario, matrix.shape[1], objective_names)
        objective_names_by_scenario[scenario] = names
        local_weights = entropy_weights(matrix)
        local_weights_by_scenario[scenario] = local_weights
        matrices.append(matrix)
        weights.extend((local_weights * normalized_scenario_weights[scenario]).tolist())
        labels.extend(f"{scenario}:{name}" for name in names)

    objective_matrix = np.hstack(matrices)
    objective_weights = np.asarray(weights, dtype=float)
    objective_weights = objective_weights / np.sum(objective_weights)
    return ScenarioDecisionResult(
        best_index=topsis(objective_matrix, objective_weights),
        objective_names=tuple(labels),
        objective_weights=objective_weights,
        weighted_objectives=objective_matrix * objective_weights,
        scenario_objective_weights=local_weights_by_scenario,
        scenario_objective_names={
            scenario: _objective_labels_for_scenario(scenario, names)
            for scenario, names in objective_names_by_scenario.items()
        },
        scenario_weights=normalized_scenario_weights,
    )


def _normalize_scenario_weights(scenario_weights: dict[str, float]) -> dict[str, float]:
    if not scenario_weights:
        raise ValueError("At least one scenario weight is required")
    total = 0.0
    normalized = {}
    for name, value in scenario_weights.items():
        weight = float(value)
        if weight <= 0:
            raise ValueError("Scenario weights must be positive")
        normalized[name] = weight
        total += weight
    return {name: weight / total for name, weight in normalized.items()}


def _objective_names_for_scenario(
    scenario: str,
    column_count: int,
    objective_names: dict[str, tuple[str, ...]] | None,
) -> tuple[str, ...]:
    if objective_names is None or scenario not in objective_names:
        return tuple(f"objective_{index + 1}" for index in range(column_count))
    names = objective_names[scenario]
    if len(names) != column_count:
        raise ValueError(f"Objective names for scenario {scenario!r} do not match matrix columns")
    return names


def _objective_labels_for_scenario(scenario: str, names: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(f"{scenario}:{name}" for name in names)
