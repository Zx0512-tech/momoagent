"""受冻结预算约束的主动学习补点选择。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import numpy as np

from pyansys_bridge.active_learning.strategy import select_active_learning_points


@dataclass(frozen=True)
class ActiveLearningRequest:
    candidates: np.ndarray
    existing_designs: np.ndarray
    batch_size: int = 2
    max_iterations: int = 2
    seed: int = 20260712

    def __post_init__(self) -> None:
        candidates = np.asarray(self.candidates, dtype=float)
        existing = np.asarray(self.existing_designs, dtype=float)
        if candidates.ndim != 2 or existing.ndim != 2:
            raise ValueError("candidates 和 existing_designs 必须是二维矩阵")
        if candidates.shape[1] != existing.shape[1]:
            raise ValueError("候选点和已有设计的列数必须一致")
        if self.batch_size <= 0 or self.max_iterations < 0:
            raise ValueError("batch_size 必须大于 0，max_iterations 不得为负")


@dataclass(frozen=True)
class ActiveLearningResult:
    designs: np.ndarray
    iteration_count: int
    real_solve_count: int
    budget_exhausted: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "designs": self.designs.tolist(),
            "iterationCount": self.iteration_count,
            "realSolveCount": self.real_solve_count,
            "budgetExhausted": self.budget_exhausted,
        }


def select_infill_designs(
    request: ActiveLearningRequest,
    *,
    precision_satisfied: bool,
    surrogate: object | None = None,
    pareto_designs: np.ndarray | None = None,
    evaluate: Callable[[np.ndarray], object] | None = None,
) -> ActiveLearningResult:
    """精度不足时最多补两轮、每轮两个不重复设计点。"""

    if precision_satisfied or request.max_iterations == 0:
        return ActiveLearningResult(np.empty((0, request.candidates.shape[1])), 0, 0, False)
    seen = {tuple(float(value) for value in row) for row in np.asarray(request.existing_designs)}
    remaining = [
        row for row in np.asarray(request.candidates, dtype=float)
        if tuple(float(value) for value in row) not in seen
    ]
    selected: list[np.ndarray] = []
    iteration_count = 0
    for iteration in range(request.max_iterations):
        if not remaining:
            break
        count = min(request.batch_size, len(remaining))
        batch = select_active_learning_points(
            np.asarray(remaining, dtype=float),
            n_points=count,
            surrogate=surrogate,
            pareto_designs=pareto_designs,
            seed=request.seed + iteration,
        )
        for point in batch:
            key = tuple(float(value) for value in point)
            if key in seen:
                continue
            seen.add(key)
            selected.append(np.asarray(point, dtype=float).copy())
            if evaluate is not None:
                evaluate(selected[-1])
        chosen = {tuple(float(value) for value in row) for row in batch}
        remaining = [row for row in remaining if tuple(float(value) for value in row) not in chosen]
        iteration_count += 1
    matrix = np.asarray(selected, dtype=float)
    if matrix.size == 0:
        matrix = np.empty((0, request.candidates.shape[1]))
    return ActiveLearningResult(
        designs=matrix,
        iteration_count=iteration_count,
        real_solve_count=len(selected),
        budget_exhausted=iteration_count >= request.max_iterations,
    )
