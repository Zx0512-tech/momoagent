"""Active learning loop."""

from __future__ import annotations

from collections.abc import Callable

import numpy as np

from pyansys_bridge.active_learning.acquisition import expected_improvement, weighted_expected_improvement
from pyansys_bridge.surrogate.gpr_model import GaussianProcessRegressor
from pyansys_bridge.surrogate.sampling import lhs_sample


class ActiveLearner:
    """Active learner using GPR and expected improvement."""

    def __init__(
        self,
        bounds: dict[str, tuple[float, float]],
        evaluator: Callable[[np.ndarray], float],
        n_iterations: int = 5,
        batch_size: int = 1,
        seed: int | None = None,
        objective_weights: np.ndarray | list[float] | tuple[float, ...] | None = None,
        objective_names: tuple[str, ...] | list[str] | None = None,
    ) -> None:
        self.bounds = bounds
        self.evaluator = evaluator
        self.n_iterations = n_iterations
        self.batch_size = batch_size
        self.seed = seed
        self.objective_weights = None if objective_weights is None else np.asarray(objective_weights, dtype=float)
        self.objective_names = None if objective_names is None else tuple(str(name) for name in objective_names)
        self.history: list[dict[str, float]] = []

    def optimize(self, initial_x: np.ndarray, initial_y: np.ndarray) -> tuple[np.ndarray, float]:
        x = np.asarray(initial_x, dtype=float)
        y = _as_objective_matrix(initial_y)
        single_objective = y.shape[1] == 1
        weights = self._objective_weights(y.shape[1])
        names = self._objective_names(y.shape[1])
        for _ in range(self.n_iterations):
            scaled_x = self._scale(x)
            models = [
                GaussianProcessRegressor(length_scale=1.0).fit(scaled_x, y[:, objective_index])
                for objective_index in range(y.shape[1])
            ]
            candidates = lhs_sample(self.bounds, max(100, 20 * self.batch_size), seed=self.seed)
            scaled_candidates = self._scale(candidates)
            acquisition = _acquisition_scores(models, scaled_candidates, y, weights)
            chosen_idx = np.argsort(acquisition)[-self.batch_size :]
            for candidate in candidates[chosen_idx]:
                value = np.asarray(self.evaluator(candidate), dtype=float).reshape(-1)
                if value.shape[0] != y.shape[1]:
                    raise ValueError("evaluator objective count must match initial_y")
                x = np.vstack([x, candidate])
                y = np.vstack([y, value])
                self.history.append(
                    {
                        **{name: float(item) for name, item in zip(names, value)},
                        **dict(zip(self.bounds.keys(), candidate)),
                    }
                )
        best_idx = _best_objective_index(y, weights)
        best_y = y[best_idx]
        if single_objective:
            return x[best_idx], float(best_y[0])
        return x[best_idx], best_y

    def _scale(self, x: np.ndarray) -> np.ndarray:
        scaled = np.asarray(x, dtype=float).copy()
        for col, (_, (low, high)) in enumerate(self.bounds.items()):
            scaled[:, col] = (scaled[:, col] - low) / (high - low)
        return scaled

    def _objective_weights(self, objective_count: int) -> np.ndarray:
        if self.objective_weights is None:
            return np.ones(objective_count) / objective_count
        if self.objective_weights.shape[0] != objective_count:
            raise ValueError("objective_weights length must match objective count")
        total = float(np.sum(self.objective_weights))
        if total <= 0.0:
            raise ValueError("objective_weights must sum to a positive value")
        return self.objective_weights / total

    def _objective_names(self, objective_count: int) -> tuple[str, ...]:
        if self.objective_names is None:
            return ("value",) if objective_count == 1 else tuple(f"objective_{index + 1}" for index in range(objective_count))
        if len(self.objective_names) != objective_count:
            raise ValueError("objective_names length must match objective count")
        return self.objective_names


def _as_objective_matrix(values) -> np.ndarray:
    matrix = np.asarray(values, dtype=float)
    if matrix.ndim == 1:
        return matrix.reshape(-1, 1)
    if matrix.ndim != 2:
        raise ValueError("initial_y must be a 1D or 2D objective array")
    return matrix


def _acquisition_scores(
    models: list[GaussianProcessRegressor],
    candidates: np.ndarray,
    y: np.ndarray,
    weights: np.ndarray,
) -> np.ndarray:
    if len(models) == 1:
        mean, std = models[0].predict(candidates, return_std=True)
        return expected_improvement(mean, std, best_value=float(np.min(y[:, 0])), minimize=True)
    predictions = [model.predict(candidates, return_std=True) for model in models]
    mean = np.column_stack([item[0] for item in predictions])
    std = np.column_stack([item[1] for item in predictions])
    return weighted_expected_improvement(mean, std, np.min(y, axis=0), weights=weights, minimize=True)


def _best_objective_index(y: np.ndarray, weights: np.ndarray) -> int:
    if y.shape[1] == 1:
        return int(np.argmin(y[:, 0]))
    span = np.maximum(y.max(axis=0) - y.min(axis=0), 1.0e-12)
    normalized = (y - y.min(axis=0)) / span
    scores = normalized @ weights
    return int(np.argmin(scores))
