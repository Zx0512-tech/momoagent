"""Adaptive surrogate model selection and validation point helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np

from .dataset import SurrogateDataset
from .evaluator import regression_metrics
from .model_zoo import baseline_surrogate_model_names, create_surrogate_model
from .sampling import lhs_sample


class _Regressor(Protocol):
    def fit(self, x: np.ndarray, y: np.ndarray): ...

    def predict(self, x: np.ndarray): ...


@dataclass(frozen=True)
class SurrogateSelection:
    """Selected surrogate model and validation metrics."""

    name: str
    model: _Regressor
    metrics: dict[str, float]
    cv_metrics: dict[str, dict[str, float]] | None = None
    candidate_metrics: dict[str, dict[str, dict[str, float]]] | None = None

    @property
    def accuracy(self) -> float:
        """Return historical CV R² for backward-compatible diagnostics only."""

        if self.cv_metrics and self.name in self.cv_metrics:
            return float(self.cv_metrics[self.name].get("r2", 0.0))
        return float(self.metrics.get("r2", 0.0))

    @property
    def selection_error(self) -> float:
        if self.cv_metrics and self.name in self.cv_metrics:
            return float(self.cv_metrics[self.name].get("max_relative_error", float("inf")))
        return float(self.metrics.get("max_relative_error", float("inf")))

    def meets_accuracy_target(self, min_r2: float = 0.95) -> bool:
        """保留旧接口；生产评价不再调用该 R² 门槛。"""

        return self.accuracy >= min_r2


def select_best_surrogate(
    dataset: SurrogateDataset,
    validation_fraction: float = 0.25,
    *,
    candidate_names: tuple[str, ...] | None = None,
    cv: int | str = "auto",
) -> SurrogateSelection:
    """Fit candidate surrogates and return the best cross-validated performer."""

    if not 0.0 < validation_fraction < 1.0:
        raise ValueError("validation_fraction must be between 0 and 1")
    if dataset.x.shape[0] < 4:
        raise ValueError("At least four samples are required for surrogate selection")

    selected_candidates = (
        baseline_surrogate_model_names()
        if candidate_names is None
        else candidate_names
    )
    scored = []
    candidate_metrics = {}
    for name in selected_candidates:
        if name == "power_law" and not _power_law_applicable(dataset):
            continue
        if name == "log_c_cubic_ridge" and not _log_c_cubic_ridge_applicable(dataset):
            continue
        fit_metrics = _fit_metrics(dataset, name)
        cv_metrics = _cross_validated_metrics(
            dataset,
            name,
            cv=cv,
            validation_fraction=validation_fraction,
        )
        scored.append((name, fit_metrics, cv_metrics))
        candidate_metrics[name] = {
            "fit": fit_metrics,
            "cross_validation": cv_metrics,
        }

    if not scored:
        raise ValueError("No surrogate candidate can fit the dataset")

    best_name, best_metrics, _ = sorted(
        scored,
        key=lambda item: (
            item[2]["max_relative_error"],
            item[2]["rmse"],
            item[2]["mae"],
            item[0],
        ),
    )[0]
    best_model = create_surrogate_model(best_name)
    _fit_model(best_model, dataset.x, dataset.y, dataset.feature_names)
    return SurrogateSelection(
        best_name,
        best_model,
        best_metrics,
        cv_metrics={name: metrics for name, _, metrics in scored},
        candidate_metrics=candidate_metrics,
    )


def select_high_value_validation_points(
    model,
    bounds: dict[str, tuple[float, float]],
    n_points: int = 3,
    seed: int | None = None,
    steps: dict[str, float] | None = None,
) -> np.ndarray:
    """Choose candidate FEM validation points near optimum and uncertainty zones."""

    if n_points <= 0:
        raise ValueError("n_points must be positive")

    candidates = lhs_sample(bounds, max(80, n_points * 30), seed=seed, steps=steps)
    mean, std = _predict_mean_std(model, candidates)
    normalized_mean = _normalize(mean)
    normalized_std = _normalize(std)
    score = normalized_mean - normalized_std
    order = np.argsort(score)

    selected = []
    seen = set()
    for index in order:
        point = tuple(float(value) for value in candidates[index])
        if point in seen:
            continue
        selected.append(candidates[index])
        seen.add(point)
        if len(selected) == n_points:
            break
    if len(selected) < n_points:
        raise ValueError("Unable to select enough unique high-value validation points after step quantization")
    return np.array(selected, dtype=float)


def _deterministic_split(dataset: SurrogateDataset, validation_fraction: float):
    n_samples = dataset.x.shape[0]
    n_valid = max(2, int(round(n_samples * validation_fraction)))
    n_valid = min(n_valid, n_samples - 2)
    indices = np.arange(n_samples)
    valid_indices = indices[-n_valid:]
    train_indices = indices[:-n_valid]
    return dataset.x[train_indices], dataset.y[train_indices], dataset.x[valid_indices], dataset.y[valid_indices]


def _cross_validated_metrics(
    dataset: SurrogateDataset,
    model_name: str,
    *,
    cv: int | str,
    validation_fraction: float,
) -> dict[str, float]:
    order = (
        np.arange(dataset.x.shape[0])
        if cv == "holdout"
        else _stable_cross_validation_order(dataset)
    )
    x = dataset.x[order]
    y = dataset.y[order]
    predictions = []
    truths = []
    for train_indices, valid_indices in _cv_splits(x.shape[0], cv=cv, validation_fraction=validation_fraction):
        model = create_surrogate_model(model_name)
        _fit_model(model, x[train_indices], y[train_indices], dataset.feature_names)
        predictions.extend(np.asarray(model.predict(x[valid_indices]), dtype=float).reshape(-1).tolist())
        truths.extend(y[valid_indices].tolist())
    return regression_metrics(np.asarray(truths), np.asarray(predictions))


def _stable_cross_validation_order(dataset: SurrogateDataset) -> np.ndarray:
    keys = [dataset.y]
    keys.extend(dataset.x[:, index] for index in range(dataset.x.shape[1] - 1, -1, -1))
    canonical = np.lexsort(tuple(keys))
    permutation = np.random.default_rng(20260712).permutation(canonical.size)
    return canonical[permutation]


def _fit_metrics(dataset: SurrogateDataset, model_name: str) -> dict[str, float]:
    model = create_surrogate_model(model_name)
    _fit_model(model, dataset.x, dataset.y, dataset.feature_names)
    predictions = np.asarray(model.predict(dataset.x), dtype=float).reshape(-1)
    return regression_metrics(dataset.y, predictions)


def _fit_model(model, x: np.ndarray, y: np.ndarray, feature_names: tuple[str, ...]):
    try:
        return model.fit(x, y, feature_names=feature_names)
    except TypeError:
        return model.fit(x, y)


def _power_law_applicable(dataset: SurrogateDataset) -> bool:
    if "c" not in dataset.feature_names or np.any(dataset.y <= 0.0):
        return False
    c_index = dataset.feature_names.index("c")
    return bool(np.all(dataset.x[:, c_index] > 0.0))


def _log_c_cubic_ridge_applicable(dataset: SurrogateDataset) -> bool:
    if "c" not in dataset.feature_names:
        return False
    c_index = dataset.feature_names.index("c")
    return bool(np.all(dataset.x[:, c_index] > 0.0))


def _cv_splits(n_samples: int, *, cv: int | str, validation_fraction: float):
    indices = np.arange(n_samples)
    if cv == "loo" or (cv == "auto" and n_samples <= 8):
        for index in indices:
            valid = np.asarray([index])
            train = indices[indices != index]
            yield train, valid
        return
    if cv == "holdout":
        n_valid = max(2, int(round(n_samples * validation_fraction)))
        n_valid = min(n_valid, n_samples - 2)
        train = indices[:-n_valid]
        valid = indices[-n_valid:]
        yield train, valid
        return
    n_folds = 5 if cv == "auto" else int(cv)
    if n_folds < 2 or n_folds > n_samples:
        raise ValueError("cv must be between 2 and the number of samples")
    for valid in np.array_split(indices, n_folds):
        train = np.setdiff1d(indices, valid, assume_unique=True)
        yield train, valid


def _predict_mean_std(model, x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    try:
        mean, std = model.predict(x, return_std=True)
        return np.asarray(mean, dtype=float), np.asarray(std, dtype=float)
    except TypeError:
        mean = np.asarray(model.predict(x), dtype=float)
        return mean, np.zeros_like(mean)


def _normalize(values: np.ndarray) -> np.ndarray:
    array = np.asarray(values, dtype=float)
    span = float(np.max(array) - np.min(array))
    if span <= 1.0e-12:
        return np.zeros_like(array)
    return (array - np.min(array)) / span
