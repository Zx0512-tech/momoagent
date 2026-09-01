"""基于 scikit-learn 的生产代理模型适配器。"""

from __future__ import annotations

from itertools import product

import numpy as np
from sklearn.base import BaseEstimator, TransformerMixin
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import ConstantKernel, RBF, WhiteKernel
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import MinMaxScaler, PolynomialFeatures, StandardScaler
from sklearn.svm import SVR

from .base import SurrogateModel


class SklearnSurrogateRegressor(SurrogateModel):
    """将 sklearn 回归器适配到统一代理模型接口。"""

    def __init__(self, model_name: str, estimator) -> None:
        self.model_name = model_name
        self.estimator = estimator

    def fit(self, x: np.ndarray, y: np.ndarray, **kwargs) -> "SklearnSurrogateRegressor":
        self.estimator.fit(np.asarray(x, dtype=float), np.asarray(y, dtype=float))
        return self

    def predict(self, x: np.ndarray, **kwargs) -> np.ndarray:
        return np.asarray(self.estimator.predict(np.asarray(x, dtype=float), **kwargs), dtype=float)


class LogCPolynomialRidgeRegressor(SurrogateModel):
    """对阻尼系数取对数后拟合三次 Ridge 响应面。"""

    model_name = "log_c_cubic_ridge"

    def __init__(self, *, degree: int = 3, ridge_alpha: float = 1.0) -> None:
        self.estimator = Pipeline(
            [
                ("scale", StandardScaler()),
                ("poly", PolynomialFeatures(degree=degree, include_bias=False)),
                ("model", Ridge(alpha=ridge_alpha)),
            ]
        )

    def fit(
        self,
        x: np.ndarray,
        y: np.ndarray,
        *,
        feature_names: tuple[str, ...] | None = None,
        **kwargs,
    ) -> "LogCPolynomialRidgeRegressor":
        del kwargs
        names = tuple(feature_names or ())
        if "c" not in names:
            raise ValueError("feature_names must include c")
        self.c_index_ = names.index("c")
        self.estimator.fit(self._transformed_x(x), np.asarray(y, dtype=float))
        return self

    def predict(self, x: np.ndarray, **kwargs) -> np.ndarray:
        return np.asarray(self.estimator.predict(self._transformed_x(x), **kwargs), dtype=float)

    def _transformed_x(self, x: np.ndarray) -> np.ndarray:
        if not hasattr(self, "c_index_"):
            raise RuntimeError("LogCPolynomialRidgeRegressor must be fitted before predict")
        array = np.asarray(x, dtype=float).copy()
        if array.ndim != 2 or self.c_index_ >= array.shape[1]:
            raise ValueError("x has incompatible feature shape")
        if np.any(array[:, self.c_index_] <= 0.0):
            raise ValueError("c feature must be positive")
        array[:, self.c_index_] = np.log10(array[:, self.c_index_])
        return array


class LegendrePolynomialFeatures(BaseEstimator, TransformerMixin):
    """Legendre 多项式混沌展开基函数。"""

    def __init__(self, degree: int = 3) -> None:
        self.degree = degree

    def fit(self, x: np.ndarray, y: np.ndarray | None = None) -> "LegendrePolynomialFeatures":
        del y
        if self.degree < 1:
            raise ValueError("degree must be at least 1")
        array = np.asarray(x, dtype=float)
        if array.ndim != 2:
            raise ValueError("x must be a 2D array")
        self.n_features_in_ = array.shape[1]
        self.multi_indices_ = tuple(
            index
            for index in product(range(self.degree + 1), repeat=self.n_features_in_)
            if 0 < sum(index) <= self.degree
        )
        return self

    def transform(self, x: np.ndarray) -> np.ndarray:
        if not hasattr(self, "multi_indices_"):
            raise RuntimeError("LegendrePolynomialFeatures must be fitted before transform")
        array = np.asarray(x, dtype=float)
        if array.ndim != 2 or array.shape[1] != self.n_features_in_:
            raise ValueError("x has incompatible feature shape")

        basis_by_feature = []
        for feature_index in range(self.n_features_in_):
            column = array[:, feature_index]
            terms = [np.ones_like(column)]
            if self.degree >= 1:
                terms.append(column)
            for order in range(2, self.degree + 1):
                # Legendre 递推避免额外依赖，并保持 PCE 基函数正交形式。
                terms.append(((2 * order - 1) * column * terms[-1] - (order - 1) * terms[-2]) / order)
            basis_by_feature.append(terms)

        transformed = np.empty((array.shape[0], len(self.multi_indices_)), dtype=float)
        for column_index, multi_index in enumerate(self.multi_indices_):
            values = np.ones(array.shape[0], dtype=float)
            for feature_index, order in enumerate(multi_index):
                if order:
                    values *= basis_by_feature[feature_index][order]
            transformed[:, column_index] = values
        return transformed


class HingeBasisFeatures(BaseEstimator, TransformerMixin):
    """MARS 风格的分段线性 hinge 基函数。"""

    def __init__(self, n_knots: int = 4, degree: int = 1) -> None:
        self.n_knots = n_knots
        self.degree = degree

    def fit(self, x: np.ndarray, y: np.ndarray | None = None) -> "HingeBasisFeatures":
        del y
        if self.n_knots < 0:
            raise ValueError("n_knots must be non-negative")
        if self.degree < 1:
            raise ValueError("degree must be at least 1")
        array = np.asarray(x, dtype=float)
        if array.ndim != 2:
            raise ValueError("x must be a 2D array")
        self.n_features_in_ = array.shape[1]
        levels = np.linspace(0.0, 1.0, self.n_knots + 2)[1:-1]
        self.knots_ = []
        for feature_index in range(self.n_features_in_):
            values = np.unique(array[:, feature_index])
            if values.size <= 2 or self.n_knots == 0:
                self.knots_.append(np.asarray([], dtype=float))
                continue
            self.knots_.append(np.unique(np.quantile(values, levels)))
        return self

    def transform(self, x: np.ndarray) -> np.ndarray:
        if not hasattr(self, "knots_"):
            raise RuntimeError("HingeBasisFeatures must be fitted before transform")
        array = np.asarray(x, dtype=float)
        if array.ndim != 2 or array.shape[1] != self.n_features_in_:
            raise ValueError("x has incompatible feature shape")

        columns = [array[:, feature_index] for feature_index in range(self.n_features_in_)]
        hinge_terms: list[tuple[int, np.ndarray]] = []
        for feature_index, knots in enumerate(self.knots_):
            column = array[:, feature_index]
            for knot in knots:
                positive = np.maximum(0.0, column - knot)
                negative = np.maximum(0.0, knot - column)
                columns.extend([positive, negative])
                hinge_terms.extend([(feature_index, positive), (feature_index, negative)])

        if self.degree >= 2:
            for left_index, (left_feature, left_values) in enumerate(hinge_terms):
                for right_feature, right_values in hinge_terms[left_index + 1 :]:
                    if left_feature != right_feature:
                        columns.append(left_values * right_values)

        return np.column_stack(columns)


def create_kriging_model(
    *,
    random_state: int = 42,
    n_restarts_optimizer: int = 5,
) -> SklearnSurrogateRegressor:
    kernel = ConstantKernel(1.0, (1.0e-3, 1.0e3)) * RBF(
        length_scale=1.0,
        length_scale_bounds=(1.0e-3, 1.0e3),
    ) + WhiteKernel(noise_level=1.0e-6, noise_level_bounds=(1.0e-10, 1.0e-1))
    estimator = Pipeline(
        [
            ("scale", StandardScaler()),
            (
                "model",
                GaussianProcessRegressor(
                    kernel=kernel,
                    normalize_y=True,
                    n_restarts_optimizer=n_restarts_optimizer,
                    random_state=random_state,
                ),
            ),
        ]
    )
    return SklearnSurrogateRegressor("kriging", estimator)


def create_response_surface_model(
    *,
    degree: int = 2,
    ridge_alpha: float = 1.0e-8,
    random_state: int | None = None,
) -> SklearnSurrogateRegressor:
    del random_state
    estimator = Pipeline(
        [
            ("scale", StandardScaler()),
            ("poly", PolynomialFeatures(degree=degree, include_bias=False)),
            ("model", Ridge(alpha=ridge_alpha)),
        ]
    )
    return SklearnSurrogateRegressor("response_surface", estimator)


def create_pce_model(
    *,
    degree: int = 3,
    ridge_alpha: float = 1.0e-8,
    random_state: int | None = None,
) -> SklearnSurrogateRegressor:
    del random_state
    estimator = Pipeline(
        [
            ("scale", MinMaxScaler(feature_range=(-1.0, 1.0))),
            ("basis", LegendrePolynomialFeatures(degree=degree)),
            ("model", Ridge(alpha=ridge_alpha)),
        ]
    )
    return SklearnSurrogateRegressor("pce", estimator)


def create_mars_model(
    *,
    n_knots: int = 4,
    degree: int = 1,
    ridge_alpha: float = 1.0e-6,
    random_state: int | None = None,
) -> SklearnSurrogateRegressor:
    del random_state
    estimator = Pipeline(
        [
            ("scale", StandardScaler()),
            ("basis", HingeBasisFeatures(n_knots=n_knots, degree=degree)),
            ("model", Ridge(alpha=ridge_alpha)),
        ]
    )
    return SklearnSurrogateRegressor("mars", estimator)


def create_svr_model(
    *,
    C: float = 20.0,
    epsilon: float = 0.02,
    gamma: str | float = "scale",
    random_state: int | None = None,
) -> SklearnSurrogateRegressor:
    del random_state
    estimator = Pipeline(
        [
            ("scale", StandardScaler()),
            ("model", SVR(kernel="rbf", C=C, epsilon=epsilon, gamma=gamma)),
        ]
    )
    return SklearnSurrogateRegressor("svr", estimator)


def create_log_c_cubic_ridge_model(
    *,
    degree: int = 3,
    ridge_alpha: float = 1.0,
) -> LogCPolynomialRidgeRegressor:
    return LogCPolynomialRidgeRegressor(degree=degree, ridge_alpha=ridge_alpha)


__all__ = [
    "HingeBasisFeatures",
    "LegendrePolynomialFeatures",
    "LogCPolynomialRidgeRegressor",
    "SklearnSurrogateRegressor",
    "create_kriging_model",
    "create_log_c_cubic_ridge_model",
    "create_mars_model",
    "create_pce_model",
    "create_response_surface_model",
    "create_svr_model",
]
