"""Engineering constraints."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Constraint:
    """Upper-bound scalar constraint."""

    name: str
    limit: float

    def is_satisfied(self, objectives: dict[str, float]) -> bool:
        return objectives.get(self.name, float("inf")) <= self.limit

    def violation(self, objectives: dict[str, float]) -> float:
        return max(0.0, float(objectives.get(self.name, float("inf"))) - float(self.limit))


def check_constraints(objectives: dict[str, float], constraints: list[Constraint]) -> bool:
    return all(constraint.is_satisfied(objectives) for constraint in constraints)


def constraint_violation(objectives: dict[str, float], constraints: list[Constraint]) -> float:
    """Return total positive constraint violation for upper-bound constraints."""

    return sum(constraint.violation(objectives) for constraint in constraints)


def feasibility_first_compare(
    a: dict[str, float],
    b: dict[str, float],
    constraints: list[Constraint],
    *,
    objective_names: tuple[str, ...] | None = None,
) -> int:
    """Compare two candidates with feasibility-first constraint handling.

    Returns ``-1`` when ``a`` is preferred, ``1`` when ``b`` is preferred, and
    ``0`` when neither candidate dominates under the strategy.
    """

    violation_a = constraint_violation(a, constraints)
    violation_b = constraint_violation(b, constraints)
    feasible_a = violation_a <= 0.0
    feasible_b = violation_b <= 0.0
    if feasible_a and not feasible_b:
        return -1
    if feasible_b and not feasible_a:
        return 1
    if not feasible_a and not feasible_b:
        if violation_a < violation_b:
            return -1
        if violation_b < violation_a:
            return 1
        return 0

    names = objective_names or tuple(sorted(set(a) & set(b)))
    values_a = np.asarray([float(a[name]) for name in names], dtype=float)
    values_b = np.asarray([float(b[name]) for name in names], dtype=float)
    a_dominates = bool(np.all(values_a <= values_b) and np.any(values_a < values_b))
    b_dominates = bool(np.all(values_b <= values_a) and np.any(values_b < values_a))
    if a_dominates and not b_dominates:
        return -1
    if b_dominates and not a_dominates:
        return 1
    return 0
