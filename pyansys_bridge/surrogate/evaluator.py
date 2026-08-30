"""Surrogate evaluation metrics."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from pyansys_bridge.models import BridgeModel, DamperParams, LoadCase


def regression_metrics(y_true, y_pred) -> dict[str, float]:
    true = np.asarray(y_true, dtype=float)
    pred = np.asarray(y_pred, dtype=float)
    err = pred - true
    rmse = float(np.sqrt(np.mean(err * err)))
    mae = float(np.mean(np.abs(err)))
    relative_errors = np.abs(err) / np.maximum(np.abs(true), 1.0e-12)
    max_relative_error = float(np.max(relative_errors))
    mean_relative_error = float(np.mean(relative_errors))
    denom = float(np.sum((true - np.mean(true)) ** 2))
    if true.size < 2:
        r2 = float("nan")
    elif denom == 0.0:
        r2 = 1.0 if rmse == 0.0 else float("-inf")
    else:
        r2 = 1.0 - float(np.sum(err * err)) / denom
    peak_relative_error = float(abs(np.max(np.abs(pred)) - np.max(np.abs(true))) / max(np.max(np.abs(true)), 1.0e-12))
    truth_range_relative = float((np.max(true) - np.min(true)) / max(np.max(np.abs(true)), 1.0e-12))
    return {
        "r2": r2,
        "rmse": rmse,
        "mae": mae,
        "max_relative_error": max_relative_error,
        "mean_relative_error": mean_relative_error,
        "peak_relative_error": peak_relative_error,
        "truth_range_relative": truth_range_relative,
    }


def validation_report(
    y_true,
    y_pred,
    min_r2: float = 0.95,
    max_peak_relative_error: float | None = None,
    near_constant_relative_tolerance: float = 1.0e-3,
    near_zero_absolute_tolerance: float = 1.0e-8,
) -> dict[str, float | bool | None]:
    """Return surrogate validation metrics and an acceptance flag."""

    metrics = regression_metrics(y_true, y_pred)
    true = np.asarray(y_true, dtype=float)
    pred = np.asarray(y_pred, dtype=float)
    relative_error_accepted = (
        max_peak_relative_error is None
        or metrics["max_relative_error"] <= max_peak_relative_error
    )
    near_constant_truth = (
        true.size >= 2
        and metrics["truth_range_relative"] <= near_constant_relative_tolerance
    )
    accepted_by_peak_relative_error = (
        max_peak_relative_error is not None
        and relative_error_accepted
    )
    near_zero_absolute = (
        true.size >= 2
        and float(np.max(np.abs(true))) <= near_zero_absolute_tolerance
        and float(np.max(np.abs(pred))) <= near_zero_absolute_tolerance
    )
    accepted = accepted_by_peak_relative_error or near_zero_absolute
    return {
        **metrics,
        "min_r2": float(min_r2),
        "r2_used_for_acceptance": False,
        "acceptance_metric": "max_relative_error",
        "max_peak_relative_error": max_peak_relative_error,
        "near_constant_relative_tolerance": float(near_constant_relative_tolerance),
        "near_zero_absolute_tolerance": float(near_zero_absolute_tolerance),
        "near_constant_truth": bool(near_constant_truth),
        "accepted_by_peak_relative_error": bool(accepted_by_peak_relative_error),
        "accepted_by_near_zero_absolute": bool(near_zero_absolute),
        "accepted": bool(accepted),
    }


@dataclass
class DesignEvaluator:
    """Evaluate a damper design through either a solver or a surrogate."""

    mode: str
    objective_name: str
    feature_names: tuple[str, ...] = ("c", "alpha")
    solver: object | None = None
    surrogate: object | None = None
    bridge_model: BridgeModel | None = None
    load_case: LoadCase | None = None

    def evaluate_design(self, design) -> dict[str, float]:
        values = np.asarray(design, dtype=float).reshape(-1)
        if values.shape[0] != len(self.feature_names):
            raise ValueError("design length must match feature_names")
        normalized = self.mode.strip().lower()
        if normalized == "surrogate":
            return {self.objective_name: self._evaluate_surrogate(values)}
        if normalized == "solver":
            return {self.objective_name: self._evaluate_solver(values)}
        raise ValueError("mode must be 'solver' or 'surrogate'")

    def _evaluate_surrogate(self, values: np.ndarray) -> float:
        if self.surrogate is None:
            raise ValueError("surrogate is required for surrogate evaluation")
        prediction = self.surrogate.predict(values.reshape(1, -1))
        if isinstance(prediction, tuple):
            prediction = prediction[0]
        return float(np.asarray(prediction, dtype=float).reshape(-1)[0])

    def _evaluate_solver(self, values: np.ndarray) -> float:
        if self.solver is None:
            raise ValueError("solver is required for solver evaluation")
        if self.bridge_model is None or self.load_case is None:
            raise ValueError("bridge_model and load_case are required for solver evaluation")
        result = self.solver.run_analysis(
            self.bridge_model,
            self.load_case,
            _damper_params_from_design(values, self.feature_names),
        )
        if result.status != "completed":
            raise RuntimeError(result.metadata.get("error", "solver evaluation failed"))
        try:
            return float(result.objectives[self.objective_name])
        except KeyError as exc:
            raise KeyError(f"Solver result is missing objective: {self.objective_name}") from exc


def _damper_params_from_design(values: np.ndarray, feature_names: tuple[str, ...]) -> DamperParams:
    payload = {name: float(value) for name, value in zip(feature_names, values)}
    return DamperParams(
        c=payload.get("c", payload.get("damping_coefficient", 0.0)),
        alpha=payload.get("alpha", 1.0),
        stiffness=payload.get("stiffness"),
    )
