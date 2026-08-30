"""Gaussian process surrogate model."""

from __future__ import annotations

import numpy as np
from sklearn.gaussian_process import GaussianProcessRegressor as SklearnGaussianProcessRegressor
from sklearn.gaussian_process.kernels import Matern, RBF, WhiteKernel

from .base import SurrogateModel


class GaussianProcessRegressor(SurrogateModel):
    """sklearn GaussianProcessRegressor wrapper with normalized inputs."""

    model_name = "gpr"

    def __init__(
        self,
        length_scale: float = 1.0,
        noise: float | str = 1.0e-8,
        *,
        kernel: str | object = "rbf",
        matern_nu: float = 1.5,
        optimize_hyperparameters: bool = False,
        n_restarts_optimizer: int = 0,
        random_state: int | None = None,
    ) -> None:
        self.length_scale = length_scale
        self.noise = noise
        self.kernel = kernel
        self.matern_nu = matern_nu
        self.optimize_hyperparameters = optimize_hyperparameters
        self.n_restarts_optimizer = n_restarts_optimizer
        self.random_state = random_state
        self.estimator: SklearnGaussianProcessRegressor | None = None
        self._x_offset: np.ndarray | None = None
        self._x_scale: np.ndarray | None = None

    def fit(self, x: np.ndarray, y: np.ndarray) -> "GaussianProcessRegressor":
        x = np.asarray(x, dtype=float)
        self._x_offset = np.mean(x, axis=0)
        self._x_scale = np.std(x, axis=0)
        self._x_scale[self._x_scale == 0.0] = 1.0
        self.estimator = SklearnGaussianProcessRegressor(
            kernel=self._build_kernel(),
            alpha=self._alpha(),
            normalize_y=True,
            optimizer="fmin_l_bfgs_b" if self.optimize_hyperparameters else None,
            n_restarts_optimizer=self.n_restarts_optimizer,
            random_state=self.random_state,
        )
        self.estimator.fit(self._normalize(x), np.asarray(y, dtype=float))
        return self

    def predict(self, x: np.ndarray, return_std: bool = False):
        if self.estimator is None:
            raise RuntimeError("GaussianProcessRegressor must be fitted before predict")
        x = self._normalize(np.asarray(x, dtype=float))
        return self.estimator.predict(x, return_std=return_std)

    def _normalize(self, x: np.ndarray) -> np.ndarray:
        if self._x_offset is None or self._x_scale is None:
            raise RuntimeError("GaussianProcessRegressor must be fitted before normalizing features")
        return (x - self._x_offset) / self._x_scale

    def _build_kernel(self):
        if not isinstance(self.kernel, str):
            return self.kernel
        normalized = self.kernel.lower()
        if normalized.startswith("matern"):
            base_kernel = Matern(length_scale=self.length_scale, nu=self.matern_nu)
        elif normalized.startswith("rbf"):
            base_kernel = RBF(length_scale=self.length_scale)
        else:
            raise ValueError(f"Unsupported GPR kernel: {self.kernel}")
        if normalized.endswith("_white") or self.noise == "learned":
            return base_kernel + WhiteKernel(noise_level=1.0e-6)
        return base_kernel

    def _alpha(self) -> float:
        if self.noise == "learned":
            return 0.0
        return float(self.noise)
