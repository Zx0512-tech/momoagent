"""DOE batch execution plus surrogate optimization orchestration."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import json
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from pyansys_bridge.models import (
    BridgeModel,
    DamperParams,
    DamperPlacement,
    LoadCase,
    common_physical_count,
    physical_count_as_int,
    split_total_damper_params,
    tower_girder_layout,
    with_physical_count,
)
from pyansys_bridge.surrogate.sampling import lhs_sample

from .damper_plan import OptimizedDamperPlan, realize_optimized_damper_plan
from .damper_cost import normalize_damper_costs
from .decision_explainability import explain_topsis_decision
from .fem_review import FEMReviewRecord, FEMValidationRecord, damper_params_from_design
from .pipeline import OptimizationPipelineResult, SurrogateObjectiveSpec, optimize_from_result_stores
from .workflow import NoFeasibleCandidatesError

if TYPE_CHECKING:
    from pyansys_bridge.batch import DamperDOERecord


@dataclass(frozen=True)
class DamperDOEObjectiveSpec:
    """Objective spec for DOE-driven surrogate optimization."""

    scenario: str
    objective: str
    weight: float = 1.0
    decision_scenario: str | None = None


@dataclass(frozen=True)
class DamperDOEOptimizationResult:
    """DOE batch records plus the downstream surrogate optimization result."""

    doe_records: tuple["DamperDOERecord", ...]
    pipeline: OptimizationPipelineResult
    damper_plan: OptimizedDamperPlan
    damper_placements: tuple[DamperPlacement, ...] | None = None
    objective_limits: dict[str, float] | None = None
    objective_limit_relative_tolerance: float = 0.0
    quality_gates: dict[str, object] | None = None
    decision_source: str = "surrogate"
    surrogate_damper_plan: OptimizedDamperPlan | None = None
    real_doe_fallback: dict[str, object] | None = None
    active_learning_records: tuple[FEMValidationRecord, ...] = ()
    active_learning_iterations: int = 0
    review_retry_count: int = 0

    def __getattr__(self, name: str):
        return getattr(self.pipeline, name)

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-friendly engineering summary for reporting."""

        optimization = self.pipeline.optimization
        doe_designs = [
            _doe_record_summary(record, self.damper_placements)
            for record in self.doe_records
        ]
        _attach_damper_cost_metrics(doe_designs)
        return {
            "doe_record_count": len(self.doe_records),
            "doe_designs": doe_designs,
            "optimization": {
                "parameter_names": list(optimization.parameter_names),
                "objective_names": list(optimization.objective_names),
                "best_design": _array_to_list(optimization.best_design),
                "best_objectives": _array_to_list(optimization.best_objectives),
                "decision_weights": _array_to_list(optimization.decision_weights),
                "pareto_design_count": int(optimization.pareto_designs.shape[0]),
                "pareto_solutions": _pareto_solution_summaries(optimization),
                "scenario_decision": _scenario_decision_summary(optimization.scenario_decision),
                "topsis": explain_topsis_decision(
                    optimization.pareto_objectives,
                    objective_names=optimization.objective_names,
                    weights=optimization.decision_weights,
                ),
            },
            "surrogate_candidate_filter": dict(optimization.candidate_filter or {}),
            "objective_limits": dict(self.objective_limits or {}),
            "objective_limit_relative_tolerance": float(self.objective_limit_relative_tolerance),
            "quality_gates": dict(self.quality_gates or {}),
            "decision_source": self.decision_source,
            "active_learning_status": _active_learning_status(
                self.active_learning_records,
                self.active_learning_iterations,
                self.quality_gates,
            ),
            "active_learning_records": [
                _validation_record_summary(record)
                for record in self.active_learning_records
            ],
            "review_retry_count": int(self.review_retry_count),
            "surrogate_selections": _surrogate_selection_summaries(self.pipeline),
            "surrogate_candidate_metrics": _surrogate_candidate_metric_summaries(self.pipeline),
            "validation_points_by_objective": {
                label: points.tolist()
                for label, points in self.pipeline.validation_points_by_objective.items()
            },
            "validation_designs": self.pipeline.validation_designs.tolist(),
            "damper_plan": self.damper_plan.to_dict(),
            "surrogate_damper_plan": (
                self.surrogate_damper_plan.to_dict()
                if self.surrogate_damper_plan is not None
                else self.damper_plan.to_dict()
            ),
            "real_doe_fallback": dict(self.real_doe_fallback or {}),
            "validation_status": _validation_status_summary(self.pipeline),
            "validation_records": [
                _validation_record_summary(record)
                for record in self.pipeline.validation_records
            ],
            "validation_reports": {
                label: _validation_report_summary(report)
                for label, report in self.pipeline.validation_reports.items()
            },
            "review_status": _review_status_summary(self.pipeline),
            "review_records": [
                _review_record_summary(record)
                for record in self.pipeline.review_records
            ],
        }

    def write_summary(self, path: str | Path) -> Path:
        """Write the engineering summary to a JSON file and return its path."""

        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8") as handle:
            json.dump(self.to_dict(), handle, ensure_ascii=False, indent=2)
        return target


@dataclass(frozen=True)
class DamperDOEOptimizationFailure:
    """Real DOE evidence from an optimization workflow that failed before a final design."""

    doe_records: tuple["DamperDOERecord", ...]
    objective_specs: tuple[DamperDOEObjectiveSpec, ...]
    feature_names: tuple[str, ...]
    objective_limits: dict[str, float] | None = None
    objective_limit_relative_tolerance: float = 0.0
    quality_gates: dict[str, object] | None = None
    damper_placements: tuple[DamperPlacement, ...] | None = None
    failure_reason: str = ""
    failure_stage: str = "surrogate_candidate_filter"
    decision_source: str = "surrogate_no_feasible_candidates"
    real_doe_fallback: dict[str, object] | None = None
    surrogate_candidate_filter: dict[str, object] | None = None
    active_learning_records: tuple[FEMValidationRecord, ...] = ()
    active_learning_iterations: int = 0
    review_retry_count: int = 0

    def to_dict(self) -> dict[str, object]:
        doe_designs = [
            _doe_record_summary(record, self.damper_placements)
            for record in self.doe_records
        ]
        _attach_damper_cost_metrics(doe_designs)
        objective_names = [
            f"{spec.scenario}:{spec.objective}"
            for spec in self.objective_specs
        ]
        return {
            "status": "failed",
            "failure_stage": self.failure_stage,
            "failure_reason": self.failure_reason,
            "doe_record_count": len(self.doe_records),
            "doe_designs": doe_designs,
            "optimization": {
                "parameter_names": list(self.feature_names),
                "objective_names": objective_names,
                "best_design": [],
                "best_objectives": [],
                "decision_weights": [],
                "pareto_design_count": 0,
                "pareto_solutions": [],
                "scenario_decision": None,
                "topsis": None,
            },
            "objective_limits": dict(self.objective_limits or {}),
            "objective_limit_relative_tolerance": float(self.objective_limit_relative_tolerance),
            "surrogate_candidate_filter": dict(self.surrogate_candidate_filter or {}),
            "quality_gates": dict(self.quality_gates or {}),
            "decision_source": self.decision_source,
            "active_learning_status": _active_learning_status(
                self.active_learning_records,
                self.active_learning_iterations,
                self.quality_gates,
            ),
            "active_learning_records": [
                _validation_record_summary(record)
                for record in self.active_learning_records
            ],
            "review_retry_count": int(self.review_retry_count),
            "damper_plan": None,
            "surrogate_damper_plan": None,
            "real_doe_fallback": dict(self.real_doe_fallback or {}),
            "validation_status": _empty_validation_status(),
            "validation_records": [],
            "validation_reports": {},
            "review_status": _empty_review_status(),
            "review_records": [],
        }

    def write_summary(self, path: str | Path) -> Path:
        """Write the engineering failure summary to a JSON file and return its path."""

        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8") as handle:
            json.dump(self.to_dict(), handle, ensure_ascii=False, indent=2)
        return target


def optimize_from_damper_doe_batch(
    bridge_model: BridgeModel,
    load_cases: Iterable[LoadCase],
    designs: np.ndarray,
    objective_specs: list[DamperDOEObjectiveSpec],
    bounds: dict[str, tuple[float, float]],
    output_dir: str | Path = "output/doe_optimization",
    feature_names: tuple[str, ...] = ("c", "alpha"),
    solver: str = "mock",
    solver_kwargs: dict | None = None,
    n_candidates: int = 200,
    seed: int | None = None,
    steps: dict[str, float] | None = None,
    objective_limits: dict[str, float] | None = None,
    objective_limit_relative_tolerance: float = 0.0,
    max_normalized_doe_distance: float | None = None,
    n_validation_points: int = 3,
    max_validation_designs: int | None = None,
    min_surrogate_r2: float | None = None,
    surrogate_cv: int | str = "auto",
    run_validation: bool = False,
    min_validation_r2: float | None = None,
    max_validation_peak_relative_error: float | None = None,
    run_review: bool = False,
    review_reuse_doe_results: bool = False,
    review_relative_error_limit: float = 0.05,
    review_objective_scales: dict[str, float] | None = None,
    max_active_learning_iterations: int = 1,
    max_review_iterations: int = 1,
    parallel_workers: int = 1,
    parallel_license_limit: int | None = None,
    parallel_mode: str = "thread",
    progress_dir: str | Path | None = None,
    progress_completed_offset: int = 0,
    progress_total_cases: int | None = None,
    progress_component: str | None = None,
    precomputed_doe_records: Iterable["DamperDOERecord"] | None = None,
) -> DamperDOEOptimizationResult:
    """Run DOE analyses and optimize surrogate objectives from their result store."""

    if not objective_specs:
        raise ValueError("At least one DOE objective spec is required")
    if max_active_learning_iterations < 0:
        raise ValueError("max_active_learning_iterations cannot be negative")
    if max_review_iterations < 0:
        raise ValueError("max_review_iterations cannot be negative")
    if not run_validation and max_validation_peak_relative_error is not None:
        raise ValueError("FEM validation accuracy gate requires run_validation=True")
    root = Path(output_dir)
    load_case_list = list(load_cases)
    if precomputed_doe_records is None:
        from pyansys_bridge.batch import run_damper_doe_batch

        doe_records = run_damper_doe_batch(
            bridge_model,
            load_case_list,
            designs,
            output_dir=root,
            feature_names=feature_names,
            solver=solver,
            solver_kwargs=solver_kwargs,
            parallel_workers=parallel_workers,
            parallel_license_limit=parallel_license_limit,
            parallel_mode=parallel_mode,
            progress_dir=progress_dir,
            progress_completed_offset=progress_completed_offset,
            progress_total_cases=progress_total_cases,
            progress_component=progress_component,
        )
    else:
        # 基线与 DOE 并发时，DOE 批次已经由工作流调度器执行，这里只做优化后处理。
        doe_records = list(precomputed_doe_records)
    doe_case_ids = tuple(result.case_id for record in doe_records for result in record.results)
    training_case_ids = set(doe_case_ids)
    _apply_damper_cost_objectives(doe_records, root)
    active_learning_kwargs = _validation_kwargs(
        run_validation=True,
        solver=solver,
        output_dir=root / "fem_validation",
        solver_kwargs=solver_kwargs,
        bridge_model=bridge_model,
        load_cases=load_case_list,
        feature_names=feature_names,
    )
    review_kwargs = _review_kwargs(
        run_review=run_review,
        solver=solver,
        output_dir=root / "fem_review",
        solver_kwargs=solver_kwargs,
        bridge_model=bridge_model,
        load_cases=load_case_list,
        feature_names=feature_names,
        reuse_doe_results=review_reuse_doe_results,
        review_relative_error_limit=review_relative_error_limit,
        review_objective_scales=review_objective_scales,
    )
    damper_placements = _configured_damper_placements(solver_kwargs)
    quality_gates = _quality_gates(
        min_surrogate_r2=min_surrogate_r2,
        run_validation=run_validation,
        min_validation_r2=min_validation_r2,
        max_validation_peak_relative_error=max_validation_peak_relative_error,
        run_review=run_review,
        review_reuse_doe_results=review_reuse_doe_results,
        review_relative_error_limit=review_relative_error_limit,
    )
    active_learning_records: list[FEMValidationRecord] = []
    active_learning_iterations = 0

    def run_pipeline(*, include_validation: bool, include_review: bool) -> OptimizationPipelineResult:
        return optimize_from_result_stores(
            _surrogate_objective_specs(objective_specs, root, tuple(sorted(training_case_ids))),
            bounds=bounds,
            n_candidates=n_candidates,
            seed=seed,
            steps=steps,
            objective_limits=objective_limits,
            objective_limit_relative_tolerance=objective_limit_relative_tolerance,
            max_normalized_doe_distance=max_normalized_doe_distance,
            feature_names=feature_names,
            n_validation_points=n_validation_points,
            max_validation_designs=max_validation_designs,
            min_surrogate_r2=None,
            surrogate_cv=surrogate_cv,
            min_validation_r2=min_validation_r2 if include_validation else None,
            max_validation_peak_relative_error=(
                max_validation_peak_relative_error if include_validation else None
            ),
            review_include_knee=False,
            review_include_robust=False,
            parallel_workers=parallel_workers,
            parallel_license_limit=parallel_license_limit,
            parallel_mode=parallel_mode,
            **(active_learning_kwargs if include_validation else {}),
            **(review_kwargs if include_review else {}),
        )

    try:
        pipeline = run_pipeline(include_validation=run_validation, include_review=False)
        while True:
            validation_rejected = _validation_gate_rejected(pipeline, quality_gates)
            if not validation_rejected:
                break
            if active_learning_iterations >= max_active_learning_iterations:
                break
            active_pipeline = run_pipeline(include_validation=True, include_review=False)
            active_learning_records.extend(active_pipeline.validation_records)
            training_case_ids.update(
                _save_validation_records_to_training_store(root, active_pipeline.validation_records)
            )
            active_learning_iterations += 1
            pipeline = run_pipeline(include_validation=run_validation, include_review=False)

        review_retry_count = 0
        pipeline = run_pipeline(include_validation=run_validation, include_review=run_review)
        while _verified_review_rejected(pipeline):
            if review_retry_count >= max_review_iterations:
                break
            training_case_ids.update(
                _save_review_records_to_training_store(root, _rejected_review_records(pipeline))
            )
            review_retry_count += 1
            pipeline = run_pipeline(include_validation=run_validation, include_review=run_review)
    except NoFeasibleCandidatesError as exc:
        fallback = _real_doe_fallback_decision(
            doe_records,
            objective_specs,
            feature_names,
            objective_limits=objective_limits,
            objective_limit_relative_tolerance=objective_limit_relative_tolerance,
        )
        if fallback:
            fallback = dict(fallback)
            fallback["status"] = "diagnostic"
            fallback["trigger"] = "surrogate_candidate_filter_no_feasible"
        return DamperDOEOptimizationFailure(
            doe_records=tuple(doe_records),
            objective_specs=tuple(objective_specs),
            feature_names=feature_names,
            objective_limits=objective_limits,
            objective_limit_relative_tolerance=objective_limit_relative_tolerance,
            quality_gates=quality_gates,
            damper_placements=damper_placements,
            failure_reason=str(exc),
            real_doe_fallback=fallback,
            surrogate_candidate_filter=getattr(exc, "diagnostics", {}),
            active_learning_records=tuple(active_learning_records),
            active_learning_iterations=active_learning_iterations,
        )
    surrogate_plan = realize_optimized_damper_plan(pipeline.optimization, placements=damper_placements)
    fallback = None
    decision_source = "surrogate"
    selected_plan = surrogate_plan
    if _validation_gate_rejected(pipeline, quality_gates):
        fallback = _real_doe_fallback_decision(
            doe_records,
            objective_specs,
            feature_names,
            objective_limits=objective_limits,
            objective_limit_relative_tolerance=objective_limit_relative_tolerance,
        )
        if fallback:
            selected_plan = _realize_damper_plan_from_design(
                feature_names,
                np.asarray(fallback["design"], dtype=float),
                damper_placements,
            )
            decision_source = "real_doe_fallback"
        else:
            decision_source = "surrogate_validation_failed_no_real_doe_fallback"
    elif _verified_review_rejected(pipeline):
        decision_source = "surrogate_final_review_failed"
    elif run_review and pipeline.review_records and all(record.accepted for record in pipeline.review_records):
        decision_source = "surrogate_fem_review_accepted"
    return DamperDOEOptimizationResult(
        doe_records=tuple(doe_records),
        pipeline=pipeline,
        damper_plan=selected_plan,
        damper_placements=damper_placements,
        objective_limits=objective_limits,
        objective_limit_relative_tolerance=objective_limit_relative_tolerance,
        quality_gates=quality_gates,
        decision_source=decision_source,
        surrogate_damper_plan=surrogate_plan,
        real_doe_fallback=fallback,
        active_learning_records=tuple(active_learning_records),
        active_learning_iterations=active_learning_iterations,
        review_retry_count=review_retry_count,
    )


def optimize_from_sampled_damper_doe(
    bridge_model: BridgeModel,
    load_cases: Iterable[LoadCase],
    objective_specs: list[DamperDOEObjectiveSpec],
    bounds: dict[str, tuple[float, float]],
    n_doe_samples: int,
    output_dir: str | Path = "output/doe_optimization",
    feature_names: tuple[str, ...] | None = None,
    solver: str = "mock",
    solver_kwargs: dict | None = None,
    n_candidates: int = 200,
    seed: int | None = None,
    steps: dict[str, float] | None = None,
    objective_limits: dict[str, float] | None = None,
    objective_limit_relative_tolerance: float = 0.0,
    max_normalized_doe_distance: float | None = None,
    n_validation_points: int = 3,
    max_validation_designs: int | None = None,
    min_surrogate_r2: float | None = None,
    surrogate_cv: int | str = "auto",
    run_validation: bool = False,
    min_validation_r2: float | None = None,
    max_validation_peak_relative_error: float | None = None,
    run_review: bool = False,
    review_reuse_doe_results: bool = False,
    review_relative_error_limit: float = 0.05,
    review_objective_scales: dict[str, float] | None = None,
    max_active_learning_iterations: int = 1,
    max_review_iterations: int = 1,
    parallel_workers: int = 1,
    parallel_license_limit: int | None = None,
    parallel_mode: str = "thread",
    progress_dir: str | Path | None = None,
    progress_completed_offset: int = 0,
    progress_total_cases: int | None = None,
    progress_component: str | None = None,
) -> DamperDOEOptimizationResult:
    """Sample DOE points from bounds, run analyses, and optimize surrogates."""

    designs = _sample_unique_doe_designs(
        bounds,
        n_doe_samples,
        seed=seed,
        steps=steps,
    )
    return optimize_from_damper_doe_batch(
        bridge_model,
        load_cases,
        designs,
        objective_specs=objective_specs,
        bounds=bounds,
        output_dir=output_dir,
        feature_names=tuple(bounds.keys()) if feature_names is None else feature_names,
        solver=solver,
        solver_kwargs=solver_kwargs,
        n_candidates=n_candidates,
        seed=seed,
        steps=steps,
        objective_limits=objective_limits,
        objective_limit_relative_tolerance=objective_limit_relative_tolerance,
        max_normalized_doe_distance=max_normalized_doe_distance,
        n_validation_points=n_validation_points,
        max_validation_designs=max_validation_designs,
        min_surrogate_r2=min_surrogate_r2,
        surrogate_cv=surrogate_cv,
        run_validation=run_validation,
        min_validation_r2=min_validation_r2,
        max_validation_peak_relative_error=max_validation_peak_relative_error,
        run_review=run_review,
        review_reuse_doe_results=review_reuse_doe_results,
        review_relative_error_limit=review_relative_error_limit,
        review_objective_scales=review_objective_scales,
        max_active_learning_iterations=max_active_learning_iterations,
        max_review_iterations=max_review_iterations,
        parallel_workers=parallel_workers,
        parallel_license_limit=parallel_license_limit,
        parallel_mode=parallel_mode,
        progress_dir=progress_dir,
        progress_completed_offset=progress_completed_offset,
        progress_total_cases=progress_total_cases,
        progress_component=progress_component,
    )


def _sample_unique_doe_designs(
    bounds: dict[str, tuple[float, float]],
    n_samples: int,
    seed: int | None,
    steps: dict[str, float] | None,
    oversample_factor: int = 5,
) -> np.ndarray:
    if oversample_factor < 1:
        raise ValueError("oversample_factor must be positive")
    n_candidates = n_samples if steps is None else n_samples * oversample_factor
    candidates = lhs_sample(bounds, n_candidates, seed=seed, steps=steps)
    unique_rows: list[np.ndarray] = []
    seen: set[tuple[float, ...]] = set()
    for row in candidates:
        key = tuple(float(value) for value in row)
        if key in seen:
            continue
        seen.add(key)
        unique_rows.append(row)
        if len(unique_rows) == n_samples:
            break
    if len(unique_rows) < n_samples:
        raise ValueError(
            "Unable to generate enough unique DOE designs after step quantization"
        )
    return np.asarray(unique_rows, dtype=float)


def _doe_record_summary(
    record,
    placements: tuple[DamperPlacement, ...] | None = None,
) -> dict[str, object]:
    summary = _design_summary(record.design, record.feature_names)
    summary["damper_plan"] = _doe_damper_plan_summary(record.design, record.feature_names, placements)
    summary["analysis_results"] = [
        _analysis_result_summary(result)
        for result in record.results
    ]
    return summary


def _design_summary(design: np.ndarray, feature_names: tuple[str, ...]) -> dict[str, object]:
    return {
        "feature_names": list(feature_names),
        "design": _array_to_list(design),
        "design_parameters": {
            name: float(value)
            for name, value in zip(feature_names, design)
        },
    }


def _doe_damper_plan_summary(
    design: np.ndarray,
    feature_names: tuple[str, ...],
    placements: tuple[DamperPlacement, ...] | None = None,
) -> dict[str, object]:
    params = damper_params_from_design(design, feature_names)
    design_parameters = {
        name: float(value)
        for name, value in zip(feature_names, design)
    }
    default_count = 1 if placements is None else common_physical_count(placements)
    physical_count = physical_count_as_int(design_parameters.get("physical_count_per_tower", default_count))
    selected_placements = (
        tower_girder_layout(physical_count)
        if placements is None
        else with_physical_count(placements, physical_count)
    )
    dampers = split_total_damper_params(params, selected_placements)
    return {
        "total_params": params.to_dict(),
        "physical_count_per_tower": physical_count,
        "realizable_dampers": [damper.to_dict() for damper in dampers],
    }


def _configured_damper_placements(
    solver_kwargs: dict | None,
) -> tuple[DamperPlacement, ...] | None:
    if solver_kwargs is None:
        return None
    placements = solver_kwargs.get("damper_placements")
    if placements is not None:
        return tuple(placements)
    physical_count = solver_kwargs.get("physical_count_per_tower")
    if physical_count is None:
        return None
    return tower_girder_layout(physical_count_as_int(physical_count))


def _surrogate_objective_specs(
    objective_specs: list[DamperDOEObjectiveSpec],
    result_root: Path,
    case_ids: tuple[str, ...],
) -> list[SurrogateObjectiveSpec]:
    return [
        SurrogateObjectiveSpec(
            scenario=spec.scenario,
            result_root=result_root,
            objective=spec.objective,
            weight=spec.weight,
            decision_scenario=spec.decision_scenario,
            case_ids=case_ids,
        )
        for spec in objective_specs
    ]


def _verified_review_rejected(pipeline: OptimizationPipelineResult) -> bool:
    records = tuple(pipeline.review_records)
    return bool(records) and all(record.verified_execution for record in records) and not all(
        record.accepted for record in records
    )


def _rejected_review_records(
    pipeline: OptimizationPipelineResult,
) -> tuple[FEMReviewRecord, ...]:
    return tuple(
        record
        for record in pipeline.review_records
        if record.verified_execution and not record.accepted
    )


def _save_validation_records_to_training_store(
    result_root: Path,
    records: tuple[FEMValidationRecord, ...] | list[FEMValidationRecord],
) -> tuple[str, ...]:
    from pyansys_bridge.batch import ResultStore

    store = ResultStore(result_root)
    case_ids = []
    for record in records:
        for result in record.results:
            store.save(result)
            case_ids.append(result.case_id)
    return tuple(case_ids)


def _save_review_records_to_training_store(
    result_root: Path,
    records: tuple[FEMReviewRecord, ...] | list[FEMReviewRecord],
) -> tuple[str, ...]:
    from pyansys_bridge.batch import ResultStore

    store = ResultStore(result_root)
    case_ids = []
    for record in records:
        for result in record.results:
            store.save(result)
            case_ids.append(result.case_id)
    return tuple(case_ids)


def _validation_gate_rejected(
    pipeline: OptimizationPipelineResult,
    quality_gates: dict[str, object],
) -> bool:
    fem_gate = quality_gates.get("fem_validation", {})
    if not isinstance(fem_gate, dict) or not bool(fem_gate.get("enforced")):
        return False
    reports = tuple(pipeline.validation_reports.values())
    return bool(reports) and all(report.verified_execution for report in reports) and not all(
        report.accepted for report in reports
    )


def _real_doe_fallback_decision(
    doe_records,
    objective_specs: list[DamperDOEObjectiveSpec],
    feature_names: tuple[str, ...],
    *,
    objective_limits: dict[str, float] | None,
    objective_limit_relative_tolerance: float,
) -> dict[str, object] | None:
    rows = []
    for index, record in enumerate(doe_records):
        values = _doe_objective_values(record, objective_specs)
        if values is None:
            continue
        feasible, violations = _objective_limit_status(
            values,
            objective_limits or {},
            objective_limit_relative_tolerance,
        )
        rows.append(
            {
                "record_index": index,
                "case_ids": [result.case_id for result in record.results],
                "design": _array_to_list(record.design),
                "design_parameters": record.design_parameters(),
                "objective_values": values,
                "feasible": feasible,
                "limit_violations": violations,
            }
        )
    if not rows:
        return None

    pool = [row for row in rows if row["feasible"]] or rows
    matrix = np.asarray(
        [
            [float(row["objective_values"][f"{spec.scenario}:{spec.objective}"]) for spec in objective_specs]
            for row in pool
        ],
        dtype=float,
    )
    weights = np.asarray([max(float(spec.weight), 0.0) for spec in objective_specs], dtype=float)
    if float(np.sum(weights)) <= 0.0:
        weights = np.ones(len(objective_specs), dtype=float)
    weights = weights / np.sum(weights)
    normalized = _normalize_minimization_matrix(matrix)
    scores = normalized @ weights
    best_pool_index = int(np.argmin(scores))
    selected = dict(pool[best_pool_index])
    selected.update(
        {
            "status": "selected",
            "method": "weighted_normalized_real_doe_minimization",
            "trigger": "surrogate_validation_failed",
            "used_infeasible_pool": not any(row["feasible"] for row in rows),
            "candidate_count": len(rows),
            "feasible_count": sum(1 for row in rows if row["feasible"]),
            "objective_names": [f"{spec.scenario}:{spec.objective}" for spec in objective_specs],
            "weights": weights.tolist(),
            "score": float(scores[best_pool_index]),
            "feature_names": list(feature_names),
        }
    )
    return selected


def _doe_objective_values(
    record,
    objective_specs: list[DamperDOEObjectiveSpec],
) -> dict[str, float] | None:
    values = {}
    for spec in objective_specs:
        result = _record_result_for_scenario(record, spec.scenario)
        if result is None or spec.objective not in result.objectives:
            return None
        value = float(result.objectives[spec.objective])
        if not np.isfinite(value):
            return None
        values[f"{spec.scenario}:{spec.objective}"] = value
    return values


def _record_result_for_scenario(record, scenario: str):
    for result in record.results:
        load_case = result.metadata.get("load_case", {})
        if load_case.get("name") == scenario:
            return result
    return None


def _objective_limit_status(
    objective_values: dict[str, float],
    objective_limits: dict[str, float],
    tolerance: float,
) -> tuple[bool, list[dict[str, float]]]:
    violations = []
    for label, value in objective_values.items():
        _, _, objective = label.partition(":")
        raw_limit = objective_limits.get(label, objective_limits.get(objective))
        if raw_limit is None:
            continue
        limit = float(raw_limit)
        allowed = limit + abs(limit) * float(tolerance)
        if float(value) > allowed:
            violations.append(
                {
                    "objective": label,
                    "value": float(value),
                    "limit": limit,
                    "allowed": allowed,
                }
            )
    return not violations, violations


def _normalize_minimization_matrix(matrix: np.ndarray) -> np.ndarray:
    if matrix.ndim != 2:
        raise ValueError("objective matrix must be 2D")
    span = np.maximum(matrix.max(axis=0) - matrix.min(axis=0), 1.0e-12)
    return (matrix - matrix.min(axis=0)) / span


def _realize_damper_plan_from_design(
    parameter_names: tuple[str, ...],
    design: np.ndarray,
    placements: tuple[DamperPlacement, ...] | None,
) -> OptimizedDamperPlan:
    values = {name: float(value) for name, value in zip(parameter_names, design)}
    if "c" not in values or "alpha" not in values:
        raise ValueError("DOE fallback damper plan requires c and alpha design parameters")
    default_count = 1 if placements is None else common_physical_count(placements)
    physical_count = physical_count_as_int(values.get("physical_count_per_tower", default_count))
    total_params = DamperParams(
        c=values["c"],
        alpha=values["alpha"],
        stiffness=values.get("stiffness"),
    )
    selected_placements = (
        tower_girder_layout(physical_count)
        if placements is None
        else with_physical_count(placements, physical_count)
    )
    return OptimizedDamperPlan(
        parameter_names=parameter_names,
        design=design.copy(),
        total_params=total_params,
        physical_count_per_tower=physical_count,
        realizable_dampers=split_total_damper_params(total_params, selected_placements),
    )


def _analysis_result_summary(result) -> dict[str, object]:
    metadata = result.metadata
    load_case = metadata.get("load_case", {})
    summary = {
        "case_id": result.case_id,
        "solver": result.solver,
        "status": result.status,
        "load_case": {
            "name": load_case.get("name"),
            "load_type": load_case.get("load_type"),
        },
        "objectives": dict(result.objectives),
    }
    if "solver_design" in metadata:
        summary["solver_design"] = dict(metadata["solver_design"])
    if load_case.get("metadata"):
        summary["load_case_metadata"] = dict(load_case["metadata"])
    component_load_cases = load_case.get("metadata", {}).get("component_load_cases", [])
    if component_load_cases:
        summary["component_load_cases"] = [
            _component_load_case_summary(component)
            for component in component_load_cases
        ]
    if "command_stream" in metadata:
        summary["command_stream"] = dict(metadata["command_stream"])
    if "solver_summary" in metadata:
        summary["solver_summary"] = dict(metadata["solver_summary"])
    return summary


def _attach_damper_cost_metrics(doe_designs: list[dict[str, object]]) -> None:
    candidates = []
    indexes = []
    for index, design in enumerate(doe_designs):
        candidate = _damper_cost_candidate(design)
        if candidate is None:
            continue
        candidates.append(candidate)
        indexes.append(index)
    for index, metrics in zip(indexes, normalize_damper_costs(candidates)):
        doe_designs[index]["damper_cost_metrics"] = metrics


def _apply_damper_cost_objectives(doe_records, result_root: Path) -> None:
    candidates = []
    targets = []
    for record in doe_records:
        summary = _damper_cost_candidate(
            {
                "design_parameters": record.design_parameters(),
                "analysis_results": [
                    _analysis_result_summary(result)
                    for result in record.results
                ],
            }
        )
        if summary is None:
            continue
        target_result = _analysis_result_with_damper_capacity_fields(record.results)
        if target_result is None:
            continue
        candidates.append(summary)
        targets.append(target_result)
    for result, metrics in zip(targets, normalize_damper_costs(candidates)):
        cost_fields = {
            key: float(metrics[key])
            for key in ("Cost_L", "Cost_R", "Cost_total")
        }
        result.objectives.update(cost_fields)
        _update_persisted_summary_objectives(result_root / result.case_id / "summary.json", cost_fields)


def _damper_cost_candidate(design: dict[str, object]) -> dict[str, float] | None:
    design_parameters = design.get("design_parameters", {})
    if not isinstance(design_parameters, dict) or "c" not in design_parameters or "alpha" not in design_parameters:
        return None
    analysis_results = design.get("analysis_results", [])
    if not isinstance(analysis_results, list):
        return None
    source = _result_with_damper_capacity_fields(analysis_results)
    if source is None:
        return None
    objectives = source["objectives"]
    c_value = float(design_parameters["c"])
    return {
        "C_L": c_value,
        "C_R": c_value,
        "alpha": float(design_parameters["alpha"]),
        "Fmax_L": float(objectives["Fmax_L"]),
        "Fmax_R": float(objectives["Fmax_R"]),
        "Smax_L": float(objectives["Smax_L"]),
        "Smax_R": float(objectives["Smax_R"]),
        "E_L": float(objectives["E_L"]),
        "E_R": float(objectives["E_R"]),
    }


def _result_with_damper_capacity_fields(analysis_results: list[object]) -> dict[str, object] | None:
    required = {"Fmax_L", "Fmax_R", "Smax_L", "Smax_R", "E_L", "E_R"}
    for result in analysis_results:
        if not isinstance(result, dict):
            continue
        objectives = result.get("objectives", {})
        if isinstance(objectives, dict) and required <= set(objectives):
            return result
    return None


def _analysis_result_with_damper_capacity_fields(analysis_results) -> object | None:
    required = {"Fmax_L", "Fmax_R", "Smax_L", "Smax_R", "E_L", "E_R"}
    for result in analysis_results:
        if required <= set(result.objectives):
            return result
    return None


def _update_persisted_summary_objectives(path: Path, objectives: dict[str, float]) -> None:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    payload.setdefault("objectives", {}).update(objectives)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


def _component_load_case_summary(component: dict[str, object]) -> dict[str, object]:
    summary = {
        "name": component.get("name"),
        "load_type": component.get("load_type"),
        "path": component.get("path"),
        "dt": component.get("dt"),
        "duration": component.get("duration"),
        "scale": component.get("scale"),
        "direction": dict(component.get("direction", {})),
        "components": list(component.get("components", [])),
        "file_hash": component.get("file_hash"),
    }
    metadata = component.get("metadata", {})
    if metadata:
        summary["metadata"] = dict(metadata)
    return summary


def _scenario_decision_summary(decision) -> dict[str, object] | None:
    if decision is None:
        return None
    return {
        "best_index": int(decision.best_index),
        "objective_names": list(decision.objective_names),
        "objective_weights": _array_to_list(decision.objective_weights),
        "scenario_weights": {
            name: float(weight)
            for name, weight in decision.scenario_weights.items()
        },
        "scenario_objective_names": {
            name: list(values)
            for name, values in decision.scenario_objective_names.items()
        },
        "scenario_objective_weights": {
            name: _array_to_list(weights)
            for name, weights in decision.scenario_objective_weights.items()
        },
    }


def _pareto_solution_summaries(optimization) -> list[dict[str, object]]:
    selected_index = optimization.scenario_decision.best_index
    return [
        {
            "design_parameters": {
                name: float(value)
                for name, value in zip(optimization.parameter_names, design)
            },
            "objective_values": {
                name: float(value)
                for name, value in zip(optimization.objective_names, objectives)
            },
            "selected": index == selected_index,
        }
        for index, (design, objectives) in enumerate(
            zip(optimization.pareto_designs, optimization.pareto_objectives)
        )
    ]


def _surrogate_selection_summaries(pipeline) -> dict[str, dict[str, object]]:
    return {
        surrogate.label: {
            "model_name": selection.name,
            "metric_source": "full_sample_fit",
            "metrics": dict(selection.metrics),
            "selection_metric_source": "cross_validation",
            "selection_metrics": dict(selection.cv_metrics.get(selection.name, {}))
            if selection.cv_metrics
            else dict(selection.metrics),
            "selection_error_metric": "max_relative_error",
            "selection_error": selection.selection_error,
            "cross_validation_metrics": dict(selection.cv_metrics.get(selection.name, {}))
            if selection.cv_metrics
            else {},
        }
        for surrogate, selection in zip(pipeline.surrogates, pipeline.selections)
    }


def _surrogate_candidate_metric_summaries(pipeline) -> dict[str, dict[str, object]]:
    rows: dict[str, dict[str, object]] = {}
    for surrogate, selection in zip(pipeline.surrogates, pipeline.selections):
        rows[surrogate.label] = {
            model_name: {
                "selected": model_name == selection.name,
                "fit": dict(metrics.get("fit", {})),
                "cross_validation": dict(metrics.get("cross_validation", {})),
            }
            for model_name, metrics in (selection.candidate_metrics or {}).items()
        }
    return rows


def _validation_report_summary(report) -> dict[str, object]:
    return {
        "objective_name": report.objective_name,
        "design_count": report.design_count,
        "accepted": report.accepted,
        "verified_execution": report.verified_execution,
        "case_ids": list(report.case_ids),
        "verification_evidence": [
            dict(item)
            for item in report.verification_evidence
        ],
        "true_values": list(report.true_values),
        "predicted_values": list(report.predicted_values),
        "metrics": dict(report.metrics),
    }


def _validation_status_summary(pipeline) -> dict[str, object]:
    reports = tuple(pipeline.validation_reports.values())
    return {
        "validation_design_count": int(pipeline.validation_designs.shape[0]),
        "validation_record_count": len(pipeline.validation_records),
        "validation_report_count": len(reports),
        "has_validation_records": bool(pipeline.validation_records),
        "has_validation_reports": bool(reports),
        "all_verified_execution": bool(reports) and all(
            report.verified_execution for report in reports
        ),
        "all_accepted": bool(reports) and all(report.accepted for report in reports),
    }


def _active_learning_status(
    records: tuple[FEMValidationRecord, ...],
    iterations: int,
    quality_gates: dict[str, object] | None,
) -> dict[str, object]:
    surrogate_gate = (quality_gates or {}).get("surrogate", {})
    case_ids = [
        result.case_id
        for record in records
        for result in record.results
    ]
    return {
        "triggered": bool(records) or int(iterations) > 0,
        "iteration_count": int(iterations),
        "record_count": len(records),
        "case_ids": case_ids,
        "selection_metrics": (
            surrogate_gate.get("ranking_metrics", [])
            if isinstance(surrogate_gate, dict)
            else []
        ),
        "r2_used_for_acceptance": False,
    }


def _empty_validation_status() -> dict[str, object]:
    return {
        "validation_design_count": 0,
        "validation_record_count": 0,
        "validation_report_count": 0,
        "has_validation_records": False,
        "has_validation_reports": False,
        "all_verified_execution": False,
        "all_accepted": False,
    }


def _validation_record_summary(record) -> dict[str, object]:
    return {
        "feature_names": list(record.feature_names),
        "design": _array_to_list(record.design),
        "design_parameters": record.design_parameters(),
        "damper_params": record.damper_params.to_dict(),
        "analysis_results": [
            _analysis_result_summary(result)
            for result in record.results
        ],
    }


def _review_status_summary(pipeline) -> dict[str, object]:
    records = tuple(pipeline.review_records)
    return {
        "review_record_count": len(records),
        "has_review_records": bool(records),
        "all_verified_execution": bool(records) and all(
            record.verified_execution for record in records
        ),
        "all_accepted": bool(records) and all(record.accepted for record in records),
    }


def _empty_review_status() -> dict[str, object]:
    return {
        "review_record_count": 0,
        "has_review_records": False,
        "all_verified_execution": False,
        "all_accepted": False,
    }


def _quality_gates(
    *,
    min_surrogate_r2: float | None,
    run_validation: bool,
    min_validation_r2: float | None,
    max_validation_peak_relative_error: float | None,
    run_review: bool,
    review_reuse_doe_results: bool,
    review_relative_error_limit: float,
) -> dict[str, object]:
    return {
        "surrogate": {
            "ranking_metrics": ["max_relative_error", "rmse", "mae"],
            "r2_used_for_acceptance": False,
            "enforced": False,
        },
        "fem_validation": {
            "run_validation": bool(run_validation),
            "max_relative_error": max_validation_peak_relative_error,
            "r2_used_for_acceptance": False,
            "enforced": bool(run_validation)
            and max_validation_peak_relative_error is not None,
        },
        "final_review": {
            "run_review": bool(run_review),
            "reuse_doe_results": bool(review_reuse_doe_results),
            "relative_error_limit": float(review_relative_error_limit),
            "enforced": bool(run_review),
        },
    }


def _review_record_summary(record) -> dict[str, object]:
    return {
        "candidate": {
            "kind": record.candidate.kind,
            "reasons": list(record.candidate.reasons),
            "pareto_index": int(record.candidate.pareto_index),
            "design_parameters": record.candidate.design_parameters(),
            "objective_values": record.candidate.objective_values(),
        },
        "accepted": record.accepted,
        "verified_execution": record.verified_execution,
        "damper_params": record.damper_params.to_dict(),
        "result_count": len(record.results),
        "verification_evidence": [
            _verification_evidence_summary(result)
            for result in record.results
        ],
        "analysis_results": [
            _analysis_result_summary(result)
            for result in record.results
        ],
        "objective_checks": [
            {
                "objective_name": check.objective_name,
                "surrogate_value": check.surrogate_value,
                "fem_value": check.fem_value,
                "absolute_error": check.absolute_error,
                "relative_error": check.relative_error,
                "accepted": check.accepted,
                "case_id": check.case_id,
            }
            for check in record.objective_checks
        ],
    }


def _verification_evidence_summary(result) -> dict[str, object]:
    metadata = result.metadata
    load_case = metadata.get("load_case", {})
    load_metadata = load_case.get("metadata", {})
    solver_design = metadata.get("solver_design", {})
    return {
        "case_id": result.case_id,
        "solver": result.solver,
        "status": result.status,
        "command_stream": _artifact_evidence_summary(metadata.get("command_stream", {})),
        "solver_summary": _artifact_evidence_summary(metadata.get("solver_summary", {})),
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


def _artifact_evidence_summary(metadata: dict) -> dict[str, object]:
    return {
        key: metadata[key]
        for key in ("path", "sha256", "dry_run", "objective_names")
        if key in metadata
    }


def _array_to_list(values) -> list[float]:
    return np.asarray(values, dtype=float).reshape(-1).tolist()


def _validation_kwargs(
    run_validation: bool,
    solver: str,
    output_dir: Path,
    solver_kwargs: dict | None,
    bridge_model: BridgeModel,
    load_cases: list[LoadCase],
    feature_names: tuple[str, ...],
) -> dict:
    if not run_validation:
        return {}
    from pyansys_bridge.batch import BatchAnalyzer

    validation_cache_dirs = [output_dir.parent] if output_dir.parent.exists() else None
    if "physical_count_per_tower" not in feature_names:
        return {
            "validation_analyzer": BatchAnalyzer(
                solver=solver,
                output_dir=output_dir,
                solver_kwargs=solver_kwargs,
                cache_dirs=validation_cache_dirs,
            ),
            "validation_bridge_model": bridge_model,
            "validation_load_cases": load_cases,
        }

    def validation_analyzer_factory(design_parameters: dict[str, float]):
        kwargs = _solver_kwargs_for_physical_count(
            solver_kwargs,
            physical_count_as_int(design_parameters["physical_count_per_tower"]),
        )
        return BatchAnalyzer(
            solver=solver,
            output_dir=output_dir,
            solver_kwargs=kwargs,
            cache_dirs=validation_cache_dirs,
        )

    return {
        "validation_analyzer_factory": validation_analyzer_factory,
        "validation_bridge_model": bridge_model,
        "validation_load_cases": load_cases,
    }


def _review_kwargs(
    run_review: bool,
    solver: str,
    output_dir: Path,
    solver_kwargs: dict | None,
    bridge_model: BridgeModel,
    load_cases: list[LoadCase],
    feature_names: tuple[str, ...],
    reuse_doe_results: bool,
    review_relative_error_limit: float,
    review_objective_scales: dict[str, float] | None,
) -> dict:
    if not run_review:
        return {}
    from pyansys_bridge.batch import BatchAnalyzer

    base = {
        "review_bridge_model": bridge_model,
        "review_load_cases": load_cases,
        "review_relative_error_limit": review_relative_error_limit,
        "review_objective_scales": review_objective_scales,
    }
    review_output_dir = output_dir.parent if reuse_doe_results else output_dir
    review_cache_dirs = (
        [
            path
            for path in (output_dir.parent / "fem_validation", output_dir)
            if path.exists()
        ]
        if reuse_doe_results
        else None
    )
    if "physical_count_per_tower" not in feature_names:
        return {
            **base,
            "review_analyzer": BatchAnalyzer(
                solver=solver,
                output_dir=review_output_dir,
                solver_kwargs=solver_kwargs,
                cache_dirs=review_cache_dirs,
            ),
        }

    def review_analyzer_factory(candidate):
        kwargs = _solver_kwargs_for_physical_count(
            solver_kwargs,
            physical_count_as_int(candidate.design_parameters()["physical_count_per_tower"]),
        )
        return BatchAnalyzer(
            solver=solver,
            output_dir=review_output_dir,
            solver_kwargs=kwargs,
            cache_dirs=review_cache_dirs,
        )

    return {
        **base,
        "review_analyzer_factory": review_analyzer_factory,
    }


def _solver_kwargs_for_physical_count(
    solver_kwargs: dict | None,
    physical_count: int,
) -> dict:
    kwargs = dict(solver_kwargs or {})
    physical_count = physical_count_as_int(physical_count)
    kwargs["physical_count_per_tower"] = physical_count
    placements = kwargs.get("damper_placements")
    if placements is not None:
        kwargs["damper_placements"] = with_physical_count(tuple(placements), physical_count)
    return kwargs
