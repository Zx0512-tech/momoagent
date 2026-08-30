"""Candidate selection for high-fidelity FEM review."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from pyansys_bridge.optimization.workflow import SurrogateOptimizationResult


@dataclass(frozen=True)
class FEMReviewCandidate:
    """One unique Pareto design selected for high-fidelity FEM re-run."""

    kind: str
    parameter_names: tuple[str, ...]
    objective_names: tuple[str, ...]
    design: np.ndarray
    objectives: np.ndarray
    pareto_index: int
    reasons: tuple[str, ...]

    def design_parameters(self) -> dict[str, float]:
        return {name: float(value) for name, value in zip(self.parameter_names, self.design)}

    def objective_values(self) -> dict[str, float]:
        return {name: float(value) for name, value in zip(self.objective_names, self.objectives)}


def select_fem_review_candidates(
    result: SurrogateOptimizationResult,
    include_knee: bool = True,
    include_robust: bool = True,
    local_neighbor_count: int = 5,
) -> tuple[FEMReviewCandidate, ...]:
    """Select unique Pareto candidates worth re-running with ANSYS/OpenSees FEM."""

    pareto_designs, pareto_objectives = _pareto_arrays(result)
    selections = [("entropy_topsis", _best_pareto_index(result, pareto_designs, pareto_objectives))]
    if include_knee:
        selections.append(("pareto_knee", pareto_knee_index(pareto_objectives)))
    if include_robust:
        selections.append(("robust", robust_pareto_index(result, local_neighbor_count)))

    ordered: list[int] = []
    reasons_by_index: dict[int, list[str]] = {}
    for reason, index in selections:
        if index not in reasons_by_index:
            ordered.append(index)
            reasons_by_index[index] = []
        reasons_by_index[index].append(reason)

    return tuple(
        FEMReviewCandidate(
            kind=reasons_by_index[index][0],
            parameter_names=result.parameter_names,
            objective_names=result.objective_names,
            design=pareto_designs[index].copy(),
            objectives=pareto_objectives[index].copy(),
            pareto_index=index,
            reasons=tuple(reasons_by_index[index]),
        )
        for index in ordered
    )


def pareto_knee_index(pareto_objectives: np.ndarray) -> int:
    """Return the index of the balanced knee point on a minimization Pareto front."""

    objectives = np.asarray(pareto_objectives, dtype=float)
    if objectives.ndim != 2:
        raise ValueError("pareto_objectives must be a 2D array")
    if objectives.shape[0] == 0:
        raise ValueError("At least one Pareto objective row is required")
    if objectives.shape[0] == 1:
        return 0
    if objectives.shape[1] == 1:
        return int(np.argmin(objectives[:, 0]))

    normalized = _normalize(objectives)
    end_a, end_b = _max_distance_pair(normalized)
    start = normalized[end_a]
    direction = normalized[end_b] - start
    length_squared = float(direction @ direction)
    if length_squared <= 1.0e-12:
        return int(np.argmin(np.linalg.norm(normalized - 0.5, axis=1)))

    projection = ((normalized - start) @ direction) / length_squared
    nearest_line_points = start + projection[:, None] * direction
    distances = np.linalg.norm(normalized - nearest_line_points, axis=1)
    if float(np.max(distances)) <= 1.0e-12:
        return int(np.argmin(np.linalg.norm(normalized - 0.5, axis=1)))
    return int(np.argmax(distances))


def robust_pareto_index(result: SurrogateOptimizationResult, local_neighbor_count: int = 5) -> int:
    """Return the Pareto index with the lowest local objective variation."""

    if local_neighbor_count <= 0:
        raise ValueError("local_neighbor_count must be positive")

    pareto_designs, pareto_objectives = _pareto_arrays(result)
    candidates = np.asarray(result.candidates, dtype=float)
    objectives = np.asarray(result.objectives, dtype=float)
    if candidates.ndim != 2 or objectives.ndim != 2:
        raise ValueError("candidates and objectives must be 2D arrays")
    if candidates.shape[0] != objectives.shape[0]:
        raise ValueError("candidates and objectives must have the same row count")
    if candidates.shape[1] != pareto_designs.shape[1] or objectives.shape[1] != pareto_objectives.shape[1]:
        raise ValueError("candidate/objective columns must match Pareto columns")
    if candidates.shape[0] == 0:
        raise ValueError("At least one candidate is required for robust selection")
    if pareto_designs.shape[0] == 1:
        return 0

    normalized_candidates = _normalize(candidates)
    normalized_objectives = _normalize(objectives)
    normalized_pareto_designs = _normalize_with_reference(pareto_designs, candidates)
    normalized_pareto_objectives = _normalize_with_reference(pareto_objectives, objectives)
    neighbor_count = min(max(2, local_neighbor_count), candidates.shape[0])

    best_index = 0
    best_key = (float("inf"), float("inf"), float("inf"))
    for index, design in enumerate(normalized_pareto_designs):
        distances = np.linalg.norm(normalized_candidates - design, axis=1)
        nearest = np.argsort(distances)[:neighbor_count]
        local_spread = float(np.linalg.norm(np.std(normalized_objectives[nearest], axis=0)))
        objective_balance = float(np.max(normalized_pareto_objectives[index]))
        objective_sum = float(np.sum(normalized_pareto_objectives[index]))
        key = (local_spread, objective_balance, objective_sum)
        if key < best_key:
            best_index = index
            best_key = key
    return best_index


def _pareto_arrays(result: SurrogateOptimizationResult) -> tuple[np.ndarray, np.ndarray]:
    designs = np.asarray(result.pareto_designs, dtype=float)
    objectives = np.asarray(result.pareto_objectives, dtype=float)
    if designs.ndim != 2 or objectives.ndim != 2:
        raise ValueError("Pareto designs and objectives must be 2D arrays")
    if designs.shape[0] == 0:
        raise ValueError("At least one Pareto design is required")
    if designs.shape[0] != objectives.shape[0]:
        raise ValueError("Pareto designs and objectives must have the same row count")
    return designs, objectives


def _best_pareto_index(
    result: SurrogateOptimizationResult,
    pareto_designs: np.ndarray,
    pareto_objectives: np.ndarray,
) -> int:
    best_design = np.asarray(result.best_design, dtype=float)
    best_objectives = np.asarray(result.best_objectives, dtype=float)
    design_distances = np.linalg.norm(
        _normalize(pareto_designs) - _normalize_with_reference(best_design[None, :], pareto_designs),
        axis=1,
    )
    objective_distances = np.linalg.norm(
        _normalize(pareto_objectives) - _normalize_with_reference(best_objectives[None, :], pareto_objectives),
        axis=1,
    )
    return int(np.argmin(design_distances + objective_distances))


def _normalize(values: np.ndarray) -> np.ndarray:
    values = np.asarray(values, dtype=float)
    span = np.maximum(values.max(axis=0) - values.min(axis=0), 1.0e-12)
    return (values - values.min(axis=0)) / span


def _normalize_with_reference(values: np.ndarray, reference: np.ndarray) -> np.ndarray:
    values = np.asarray(values, dtype=float)
    reference = np.asarray(reference, dtype=float)
    span = np.maximum(reference.max(axis=0) - reference.min(axis=0), 1.0e-12)
    return (values - reference.min(axis=0)) / span


def _max_distance_pair(points: np.ndarray) -> tuple[int, int]:
    best = (0, 1)
    best_distance = -1.0
    for i in range(points.shape[0]):
        for j in range(i + 1, points.shape[0]):
            distance = float(np.linalg.norm(points[i] - points[j]))
            if distance > best_distance:
                best = (i, j)
                best_distance = distance
    return best
