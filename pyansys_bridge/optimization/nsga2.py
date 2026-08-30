"""Small Pareto search utilities."""

from __future__ import annotations

from collections.abc import Callable
import warnings

import numpy as np


def dominates(a: np.ndarray, b: np.ndarray) -> bool:
    return bool(np.all(a <= b) and np.any(a < b))


def feasibility_first_dominates(
    a: np.ndarray,
    b: np.ndarray,
    violation_a: float = 0.0,
    violation_b: float = 0.0,
) -> bool:
    """Dominance rule with feasibility prioritized before Pareto comparison."""

    feasible_a = violation_a <= 0.0
    feasible_b = violation_b <= 0.0
    if feasible_a and not feasible_b:
        return True
    if feasible_b and not feasible_a:
        return False
    if not feasible_a and not feasible_b:
        return violation_a < violation_b
    return dominates(a, b)


def pareto_front(points: np.ndarray) -> np.ndarray:
    if points.size == 0:
        return np.array([], dtype=int)
    keep = []
    for i, point in enumerate(points):
        if not any(dominates(other, point) for j, other in enumerate(points) if i != j):
            keep.append(i)
    return np.array(keep, dtype=int)


def random_pareto_search(
    bounds: dict[str, tuple[float, float]],
    evaluator: Callable[[np.ndarray], list[float]],
    n_samples: int = 100,
    seed: int | None = None,
) -> dict[str, np.ndarray]:
    """Sample a design space and return nondominated designs."""

    rng = np.random.default_rng(seed)
    names = tuple(bounds.keys())
    designs = np.zeros((n_samples, len(names)))
    for col, name in enumerate(names):
        low, high = bounds[name]
        designs[:, col] = rng.uniform(low, high, n_samples)
    objectives = np.array([evaluator(row) for row in designs], dtype=float)
    front_idx = pareto_front(objectives)
    return {"designs": designs[front_idx], "objectives": objectives[front_idx], "parameter_names": np.array(names)}


def nsga2_pareto_search(
    bounds: dict[str, tuple[float, float]],
    evaluator,
    objective_names: tuple[str, ...],
    *,
    constraints: dict[str, float] | None = None,
    population_size: int = 100,
    generations: int = 100,
    seed: int | None = None,
    output_dir=None,
) -> dict[str, np.ndarray]:
    """Compatibility wrapper around the pymoo NSGA-II adapter."""

    warnings.warn(
        "nsga2_pareto_search is deprecated; use optimize_with_pymoo_nsga2 instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    from pyansys_bridge.optimization.pymoo_adapter import optimize_with_pymoo_nsga2

    result = optimize_with_pymoo_nsga2(
        bounds=bounds,
        evaluator=evaluator,
        objective_names=objective_names,
        constraints=constraints,
        population_size=population_size,
        generations=generations,
        seed=seed,
        output_dir=output_dir,
    )
    return result.to_legacy_dict()
