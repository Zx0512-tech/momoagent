"""Positive power-law surrogate for damping response objectives."""

from __future__ import annotations

import numpy as np
from sklearn.linear_model import LinearRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from .base import SurrogateModel


class PowerLawRegressor(SurrogateModel):
    """Fit a log-linear response using the damping coefficient as a power-law feature."""

    model_name = "power_law"

    def __init__(self) -> None:
        self.estimator: Pipeline | None = None
        self.c_index: int | None = None
        self.n_features_in_: int | None = None

    def fit(
        self,
        x: np.ndarray,
        y: np.ndarray,
        *,
        feature_names: tuple[str, ...] | None = None,
    ) -> "PowerLawRegressor":
        array = self._validated_x(x)
        target = np.asarray(y, dtype=float).reshape(-1)
        if target.shape[0] != array.shape[0]:
            raise ValueError("x and y must contain the same number of samples")
        if not np.all(np.isfinite(target)) or np.any(target <= 0.0):
            raise ValueError("target values must be positive and finite")

        self.c_index = self._c_feature_index(array.shape[1], feature_names)
        if np.any(array[:, self.c_index] <= 0.0):
            raise ValueError("c feature must be positive")
        self.n_features_in_ = array.shape[1]
        self.estimator = Pipeline(
            [
                ("scale", StandardScaler()),
                ("model", LinearRegression()),
            ]
        )
        self.estimator.fit(self._transform_x(array), np.log(target))
        return self

    def predict(self, x: np.ndarray) -> np.ndarray:
        if self.estimator is None or self.c_index is None or self.n_features_in_ is None:
            raise RuntimeError("PowerLawRegressor must be fitted before predict")
        array = self._validated_x(x)
        if array.shape[1] != self.n_features_in_:
            raise ValueError("x has incompatible feature shape")
        if np.any(array[:, self.c_index] <= 0.0):
            raise ValueError("c feature must be positive")
        return np.exp(self.estimator.predict(self._transform_x(array)))

    def _transform_x(self, x: np.ndarray) -> np.ndarray:
        transformed = np.array(x, dtype=float, copy=True)
        transformed[:, self.c_index] = np.log(transformed[:, self.c_index])
        return transformed

    @staticmethod
    def _validated_x(x: np.ndarray) -> np.ndarray:
        array = np.asarray(x, dtype=float)
        if array.ndim != 2:
            raise ValueError("x must be a 2D array")
        if not np.all(np.isfinite(array)):
            raise ValueError("x must contain finite values")
        return array

    @staticmethod
    def _c_feature_index(
        n_features: int,
        feature_names: tuple[str, ...] | None,
    ) -> int:
        if feature_names is None:
            return 0
        if len(feature_names) != n_features:
            raise ValueError("feature_names must match x columns")
        try:
            return feature_names.index("c")
        except ValueError as exc:
            raise ValueError("PowerLawRegressor requires a c feature") from exc
