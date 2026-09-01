"""RBF surrogate model."""

from __future__ import annotations

import numpy as np

from .base import SurrogateModel


class RBFRegressor(SurrogateModel):
    """Small radial basis function regressor."""

    model_name = "rbf"

    def __init__(self, epsilon: float = 1.0, ridge: float = 1.0e-8) -> None:
        self.epsilon = epsilon
        self.ridge = ridge
        self.x_train: np.ndarray | None = None
        self.weights: np.ndarray | None = None
        self._x_offset: np.ndarray | None = None
        self._x_scale: np.ndarray | None = None
        self._y_offset: float = 0.0

    def fit(self, x: np.ndarray, y: np.ndarray) -> "RBFRegressor":
        x = np.asarray(x, dtype=float)
        self._x_offset = np.mean(x, axis=0)
        self._x_scale = np.std(x, axis=0)
        self._x_scale[self._x_scale == 0.0] = 1.0
        self.x_train = self._normalize(x)
        y = np.asarray(y, dtype=float)
        self._y_offset = float(np.mean(y))
        phi = self._kernel(self.x_train, self.x_train)
        phi += self.ridge * np.eye(phi.shape[0])
        self.weights = np.linalg.solve(phi, y - self._y_offset)
        return self

    def predict(self, x: np.ndarray) -> np.ndarray:
        if self.x_train is None or self.weights is None:
            raise RuntimeError("RBFRegressor must be fitted before predict")
        return self._kernel(self._normalize(np.asarray(x, dtype=float)), self.x_train) @ self.weights + self._y_offset

    def _kernel(self, a: np.ndarray, b: np.ndarray) -> np.ndarray:
        diff = a[:, None, :] - b[None, :, :]
        dist2 = np.sum(diff * diff, axis=2)
        return np.exp(-self.epsilon * dist2)

    def _normalize(self, x: np.ndarray) -> np.ndarray:
        if self._x_offset is None or self._x_scale is None:
            raise RuntimeError("RBFRegressor must be fitted before normalizing features")
        return (x - self._x_offset) / self._x_scale
