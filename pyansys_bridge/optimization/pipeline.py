"""End-to-end surrogate optimization pipeline helpers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from pyansys_bridge.models import BridgeModel, LoadCase
from pyansys_bridge.optimization.fem_review import (
    FEMReviewRecord,
    FEMValidationRecord,
    is_verified_solver_output,
    run_fem_review,
    run_validation_designs,
)
from pyansys_bridge.optimization.review import FEMReviewCandidate, select_fem_review_candidates
from pyansys_bridge.optimization.workflow import ScenarioSurrogate, SurrogateOptimizationResult, optimize_with_surrogates
from pyansys_bridge.surrogate.dataset import dataset_from_result_store
from pyansys_bridge.surrogate.evaluator import validation_report
from pyansys_bridge.surrogate.selector import (
    SurrogateSelection,
    select_best_surrogate,
    select_high_value_validation_points,
)

if TYPE_CHECKING:
    from pyansys_bridge.batch import BatchAnalyzer


@dataclass(frozen=True)
class SurrogateObjectiveSpec:
    """Objective to train from one batch result store.

    ``weight`` is a decision-layer scenario weight, not a surrogate scale.
    ``decision_scenario`` can group several load cases under one decision
    state, for example wind and traffic as operation.
    """

    scenario: str
    result_root: str | Path
    objective: str
    weight: float = 1.0
    decision_scenario: str | None = None
    case_ids: tuple[str, ...] | None = None


@dataclass(frozen=True)
class OptimizationPipelineResult:
    """Trained surrogates, optimization result, and FEM review candidates."""

    selections: tuple[SurrogateSelection, ...]
    surrogates: tuple[ScenarioSurrogate, ...]
    optimization: SurrogateOptimizationResult
    review_candidates: tuple[FEMReviewCandidate, ...]
    validation_points_by_objective: dict[str, np.ndarray]
    validation_designs: np.ndarray
    validation_records: tuple[FEMValidationRecord, ...] = ()
    validation_reports: dict[str, "FEMValidationReport"] = field(default_factory=dict)
    review_records: tuple[FEMReviewRecord, ...] = ()


@dataclass(frozen=True)
class FEMValidationReport:
    """Surrogate-vs-FEM validation metrics for one scenario objective."""

    objective_name: str
    true_values: tuple[float, ...]
    predicted_values: tuple[float, ...]
    metrics: dict[str, float | bool | None]
    verified_execution: bool = False
    case_ids: tuple[str, ...] = ()
    verification_evidence: tuple[dict[str, object], ...] = ()

    @property
    def design_count(self) -> int:
        return len(self.true_values)

    @property
    def accepted(self) -> bool:
        return self.verified_execution and bool(self.metrics["accepted"])


def optimize_from_result_stores(
    specs: list[SurrogateObjectiveSpec],
    bounds: dict[str, tuple[float, float]],
    n_candidates: int = 200,
    seed: int | None = None,
    steps: dict[str, float] | None = None,
    objective_limits: dict[str, float] | None = None,
    objective_limit_relative_tolerance: float = 0.0,
    max_normalized_doe_distance: float | None = None,
    feature_names: tuple[str, ...] = ("c", "alpha"),
    n_validation_points: int = 3,
    max_validation_designs: int | None = None,
    min_surrogate_r2: float | None = None,
    surrogate_cv: int | str = "auto",
    min_validation_r2: float | None = None,
    max_validation_peak_relative_error: float | None = None,
    validation_analyzer: BatchAnalyzer | None = None,
    validation_analyzer_factory: Callable[[dict[str, float]], BatchAnalyzer] | None = None,
    validation_bridge_model: BridgeModel | None = None,
    validation_load_cases: tuple[LoadCase, ...] | list[LoadCase] | None = None,
    review_analyzer: BatchAnalyzer | None = None,
    review_analyzer_factory: Callable[[FEMReviewCandidate], BatchAnalyzer] | None = None,
    review_bridge_model: BridgeModel | None = None,
    review_load_cases: tuple[LoadCase, ...] | list[LoadCase] | None = None,
    review_relative_error_limit: float = 0.05,
    review_objective_scales: dict[str, float] | None = None,
    review_include_knee: bool = True,
    review_include_robust: bool = True,
    parallel_workers: int = 1,
    parallel_license_limit: int | None = None,
    parallel_mode: str = "thread",
) -> OptimizationPipelineResult:
    """Train objective surrogates from batch summaries and optimize them."""

    if not specs:
        raise ValueError("At least one surrogate objective spec is required")

    validation_enabled = validation_analyzer is not None or validation_analyzer_factory is not None
    selections = []
    surrogates = []
    validation_points_by_objective = {}
    training_designs = None
    for spec in specs:
        dataset = dataset_from_result_store(
            spec.result_root,
            spec.objective,
            feature_names=feature_names,
            load_case_name=spec.scenario,
            case_ids=spec.case_ids,
        )
        if training_designs is None:
            training_designs = dataset.x.copy()
        elif max_normalized_doe_distance is not None and not _same_design_rows(training_designs, dataset.x):
            raise ValueError("DOE support filtering requires identical training designs for every objective")
        selection = select_best_surrogate(dataset, cv=surrogate_cv)
        label = f"{spec.scenario}:{spec.objective}"
        selections.append(selection)
        if validation_enabled:
            validation_points_by_objective[label] = select_high_value_validation_points(
                selection.model,
                bounds,
                n_points=n_validation_points,
                seed=seed,
                steps=steps,
            )
        surrogates.append(
            ScenarioSurrogate(
                scenario=spec.scenario,
                objective=spec.objective,
                model=selection.model,
                weight=spec.weight,
                decision_scenario=spec.decision_scenario,
            )
        )

    optimization = optimize_with_surrogates(
        bounds,
        surrogates,
        n_candidates=n_candidates,
        seed=seed,
        steps=steps,
        objective_limits=objective_limits,
        objective_limit_relative_tolerance=objective_limit_relative_tolerance,
        training_designs=training_designs,
        max_normalized_doe_distance=max_normalized_doe_distance,
    )
    review_candidates = select_fem_review_candidates(
        optimization,
        include_knee=review_include_knee,
        include_robust=review_include_robust,
    )
    validation_designs = (
        _unique_validation_designs(
            validation_points_by_objective,
            max_designs=max_validation_designs,
        )
        if validation_enabled
        else np.empty((0, len(feature_names)), dtype=float)
    )
    validation_records = _maybe_run_fem_validation(
        validation_analyzer,
        validation_analyzer_factory,
        validation_bridge_model,
        validation_load_cases,
        validation_designs,
        feature_names,
        parallel_workers,
        parallel_license_limit,
        parallel_mode,
    )
    validation_reports = _validation_reports(
        validation_records,
        surrogates,
        min_r2=min_validation_r2,
        max_peak_relative_error=max_validation_peak_relative_error,
    )
    review_records = _maybe_run_fem_review(
        review_analyzer,
        review_analyzer_factory,
        review_bridge_model,
        review_load_cases,
        review_candidates,
        review_relative_error_limit,
        review_objective_scales,
        parallel_workers,
        parallel_license_limit,
        parallel_mode,
    )
    return OptimizationPipelineResult(
        selections=tuple(selections),
        surrogates=tuple(surrogates),
        optimization=optimization,
        review_candidates=review_candidates,
        validation_points_by_objective=validation_points_by_objective,
        validation_designs=validation_designs,
        validation_records=validation_records,
        validation_reports=validation_reports,
        review_records=review_records,
    )


def _same_design_rows(left: np.ndarray, right: np.ndarray) -> bool:
    left_values = np.asarray(left, dtype=float)
    right_values = np.asarray(right, dtype=float)
    if left_values.shape != right_values.shape:
        return False
    if left_values.ndim != 2:
        return False
    columns = tuple(range(left_values.shape[1] - 1, -1, -1))
    left_order = np.lexsort(tuple(left_values[:, column] for column in columns))
    right_order = np.lexsort(tuple(right_values[:, column] for column in columns))
    return bool(np.allclose(left_values[left_order], right_values[right_order]))


def _unique_validation_designs(
    points_by_objective: dict[str, np.ndarray],
    *,
    max_designs: int | None = None,
) -> np.ndarray:
    if max_designs is not None and max_designs <= 0:
        raise ValueError("max_validation_designs must be positive")
    rows = []
    seen = set()
    for label in sorted(points_by_objective):
        points = np.asarray(points_by_objective[label], dtype=float)
        if points.ndim != 2:
            raise ValueError("validation points must be 2D arrays")
        for point in points:
            key = tuple(float(value) for value in point)
            if key in seen:
                continue
            seen.add(key)
            rows.append(point.copy())
            if max_designs is not None and len(rows) >= max_designs:
                return np.vstack(rows)
    if not rows:
        return np.empty((0, 0), dtype=float)
    return np.vstack(rows)


def _maybe_run_fem_review(
    analyzer: BatchAnalyzer | None,
    analyzer_factory: Callable[[FEMReviewCandidate], BatchAnalyzer] | None,
    bridge_model: BridgeModel | None,
    load_cases: tuple[LoadCase, ...] | list[LoadCase] | None,
    candidates: tuple[FEMReviewCandidate, ...],
    relative_error_limit: float,
    objective_scales: dict[str, float] | None,
    parallel_workers: int,
    parallel_license_limit: int | None,
    parallel_mode: str,
) -> tuple[FEMReviewRecord, ...]:
    analyzer_provided = analyzer is not None or analyzer_factory is not None
    provided = [analyzer_provided, bridge_model is not None, load_cases is not None]
    if not any(provided):
        return ()
    if analyzer is not None and analyzer_factory is not None:
        raise ValueError("review_analyzer and review_analyzer_factory cannot both be provided")
    if not all(provided):
        raise ValueError(
            "review_analyzer or review_analyzer_factory, review_bridge_model, and review_load_cases must be provided together"
        )
    assert bridge_model is not None
    assert load_cases is not None
    return run_fem_review(
        analyzer,
        bridge_model,
        load_cases,
        candidates,
        relative_error_limit=relative_error_limit,
        objective_scales=objective_scales,
        analyzer_factory=analyzer_factory,
        parallel_workers=parallel_workers,
        parallel_license_limit=parallel_license_limit,
        parallel_mode=parallel_mode,
    )


def _maybe_run_fem_validation(
    analyzer: BatchAnalyzer | None,
    analyzer_factory: Callable[[dict[str, float]], BatchAnalyzer] | None,
    bridge_model: BridgeModel | None,
    load_cases: tuple[LoadCase, ...] | list[LoadCase] | None,
    validation_designs: np.ndarray,
    feature_names: tuple[str, ...],
    parallel_workers: int,
    parallel_license_limit: int | None,
    parallel_mode: str,
) -> tuple[FEMValidationRecord, ...]:
    analyzer_provided = analyzer is not None or analyzer_factory is not None
    provided = [analyzer_provided, bridge_model is not None, load_cases is not None]
    if not any(provided):
        return ()
    if analyzer is not None and analyzer_factory is not None:
        raise ValueError("validation_analyzer and validation_analyzer_factory cannot both be provided")
    if not all(provided):
        raise ValueError(
            "validation_analyzer or validation_analyzer_factory, validation_bridge_model, "
            "and validation_load_cases must be provided together"
        )
    assert bridge_model is not None
    assert load_cases is not None
    return run_validation_designs(
        analyzer,
        bridge_model,
        load_cases,
        validation_designs,
        feature_names=feature_names,
        analyzer_factory=analyzer_factory,
        parallel_workers=parallel_workers,
        parallel_license_limit=parallel_license_limit,
        parallel_mode=parallel_mode,
    )


def _validation_reports(
    records: tuple[FEMValidationRecord, ...],
    surrogates: list[ScenarioSurrogate],
    min_r2: float | None,
    max_peak_relative_error: float | None,
) -> dict[str, FEMValidationReport]:
    enforce_report = max_peak_relative_error is not None
    if not records:
        if enforce_report:
            raise ValueError("FEM validation accuracy gate requires validation records")
        return {}

    reports = {}
    threshold = 0.95 if min_r2 is None else float(min_r2)
    for surrogate in surrogates:
        label = surrogate.label
        true_values = []
        predicted_values = []
        scenario_results = []
        missing_objective = False
        for record in records:
            result = _validation_result_for_scenario(record, surrogate.scenario)
            scenario_results.append(result)
            if surrogate.objective not in result.objectives:
                missing_objective = True
                continue
            true_values.append(float(result.objectives[surrogate.objective]))
            prediction = surrogate.model.predict(record.design.reshape(1, -1))
            predicted_values.append(float(np.asarray(prediction, dtype=float).reshape(-1)[0]))

        if missing_objective:
            metrics = _missing_validation_metrics(threshold, max_peak_relative_error)
        else:
            metrics = validation_report(
                true_values,
                predicted_values,
                min_r2=threshold,
                max_peak_relative_error=max_peak_relative_error,
            )
        report = FEMValidationReport(
            objective_name=label,
            true_values=tuple(true_values),
            predicted_values=tuple(predicted_values),
            metrics=metrics,
            verified_execution=all(
                is_verified_solver_output(result, surrogate.objective)
                for result in scenario_results
            ),
            case_ids=tuple(result.case_id for result in scenario_results),
            verification_evidence=tuple(
                _verification_evidence(result)
                for result in scenario_results
            ),
        )
        if enforce_report and not report.verified_execution:
            raise ValueError(
                "FEM validation accuracy gate requires run-mode solver output "
                f"with a loaded summary for {label}"
            )
        reports[label] = report
    return reports


def _missing_validation_metrics(
    min_r2: float,
    max_peak_relative_error: float | None,
) -> dict[str, float | bool | None]:
    return {
        "r2": float("nan"),
        "rmse": float("nan"),
        "mae": float("nan"),
        "max_relative_error": float("nan"),
        "mean_relative_error": float("nan"),
        "peak_relative_error": float("nan"),
        "truth_range_relative": float("nan"),
        "min_r2": float(min_r2),
        "r2_used_for_acceptance": False,
        "acceptance_metric": "max_relative_error",
        "max_peak_relative_error": max_peak_relative_error,
        "near_constant_relative_tolerance": 1.0e-3,
        "near_constant_truth": False,
        "accepted_by_peak_relative_error": False,
        "accepted": False,
    }


def _verification_evidence(result) -> dict[str, object]:
    metadata = result.metadata
    load_case = metadata.get("load_case", {})
    load_metadata = load_case.get("metadata", {})
    solver_design = metadata.get("solver_design", {})
    return {
        "case_id": result.case_id,
        "solver": result.solver,
        "status": result.status,
        "command_stream": _artifact_evidence(metadata.get("command_stream", {})),
        "solver_summary": _artifact_evidence(metadata.get("solver_summary", {})),
        "load_calibration": dict(load_metadata.get("load_calibration", {})),
        "component_load_calibrations": _component_load_calibrations(load_metadata),
        "damper_calibration": dict(solver_design.get("damper_calibration", {})),
    }


def _component_load_calibrations(load_metadata: dict) -> list[dict[str, object]]:
    calibrations = []
    for component in load_metadata.get("component_load_cases", ()):
        component_metadata = component.get("metadata", {})
        calibration = component_metadata.get("load_calibration")
        if calibration:
            calibrations.append(
                {
                    "name": component.get("name"),
                    "load_type": component.get("load_type"),
                    "load_calibration": dict(calibration),
                }
            )
    return calibrations


def _artifact_evidence(metadata: dict) -> dict[str, object]:
    return {
        key: metadata[key]
        for key in ("path", "sha256", "dry_run", "objective_names")
        if key in metadata
    }


def _validation_result_for_scenario(record: FEMValidationRecord, scenario: str):
    for result in record.results:
        load_case = result.metadata.get("load_case", {})
        if load_case.get("name") == scenario:
            return result
    raise ValueError(f"Missing FEM validation result for scenario: {scenario}")
