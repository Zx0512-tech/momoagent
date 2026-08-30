"""High-fidelity FEM review workflow for surrogate-selected candidates."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from dataclasses import dataclass
from hashlib import sha256
import math
from pathlib import Path
from typing import TYPE_CHECKING, Iterable

import numpy as np

from pyansys_bridge.models import AnalysisResult, BridgeModel, DamperParams, LoadCase
from pyansys_bridge.optimization.review import FEMReviewCandidate

if TYPE_CHECKING:
    from pyansys_bridge.batch import BatchAnalyzer


@dataclass(frozen=True)
class FEMObjectiveCheck:
    """Comparison between one surrogate objective and one FEM result objective."""

    objective_name: str
    surrogate_value: float
    fem_value: float
    absolute_error: float
    relative_error: float
    accepted: bool
    case_id: str


@dataclass(frozen=True)
class FEMReviewRecord:
    """FEM re-run results for one selected optimization candidate."""

    candidate: FEMReviewCandidate
    damper_params: DamperParams
    results: tuple[AnalysisResult, ...]
    objective_checks: tuple[FEMObjectiveCheck, ...]

    @property
    def verified_execution(self) -> bool:
        results_by_scenario = {_load_case_name(result): result for result in self.results}
        if not self.objective_checks:
            return False
        for check in self.objective_checks:
            scenario, objective_name = _split_objective_label(check.objective_name)
            result = results_by_scenario.get(scenario)
            if result is None or not is_verified_solver_output(result, objective_name):
                return False
        return True

    @property
    def accepted(self) -> bool:
        return self.verified_execution and all(check.accepted for check in self.objective_checks)


@dataclass(frozen=True)
class FEMValidationRecord:
    """FEM results for one high-value surrogate validation design."""

    design: np.ndarray
    feature_names: tuple[str, ...]
    damper_params: DamperParams
    results: tuple[AnalysisResult, ...]

    def design_parameters(self) -> dict[str, float]:
        return {name: float(value) for name, value in zip(self.feature_names, self.design)}


def run_fem_review(
    analyzer: BatchAnalyzer | None,
    bridge_model: BridgeModel,
    load_cases: Iterable[LoadCase],
    candidates: Iterable[FEMReviewCandidate],
    relative_error_limit: float = 0.05,
    objective_scales: dict[str, float] | None = None,
    analyzer_factory: Callable[[FEMReviewCandidate], BatchAnalyzer] | None = None,
    parallel_workers: int = 1,
    parallel_license_limit: int | None = None,
    parallel_mode: str = "thread",
) -> tuple[FEMReviewRecord, ...]:
    """Run selected candidates through FEM batch analysis and compare objectives."""

    if relative_error_limit < 0:
        raise ValueError("relative_error_limit cannot be negative")
    if analyzer is None and analyzer_factory is None:
        raise ValueError("Either analyzer or analyzer_factory is required for FEM review")
    if analyzer is not None and analyzer_factory is not None:
        raise ValueError("Provide either analyzer or analyzer_factory, not both")

    load_case_list = tuple(load_cases)
    if not load_case_list:
        raise ValueError("At least one load case is required for FEM review")

    candidate_list = list(candidates)
    effective_workers = _effective_parallel_workers(parallel_workers, parallel_license_limit)
    mode = _parallel_mode(parallel_mode)
    _validate_analyzer_parallelism(analyzer, effective_workers, mode)
    if effective_workers == 1 or len(candidate_list) <= 1:
        return tuple(
            _run_review_candidate(
                analyzer,
                analyzer_factory,
                bridge_model,
                load_case_list,
                candidate,
                relative_error_limit,
                objective_scales,
            )
            for candidate in candidate_list
        )
    if analyzer_factory is not None:
        review_jobs = tuple(
            (candidate, analyzer_factory(candidate))
            for candidate in candidate_list
        )
        for _, candidate_analyzer in review_jobs:
            _validate_analyzer_parallelism(candidate_analyzer, effective_workers, mode)
        executor_class = _executor_class(mode)
        with executor_class(max_workers=effective_workers) as executor:
            futures = [
                executor.submit(
                    _run_review_candidate,
                    candidate_analyzer,
                    None,
                    bridge_model,
                    load_case_list,
                    candidate,
                    relative_error_limit,
                    objective_scales,
                )
                for candidate, candidate_analyzer in review_jobs
            ]
            return tuple(future.result() for future in futures)
    executor_class = _executor_class(mode)
    with executor_class(max_workers=effective_workers) as executor:
        futures = [
            executor.submit(
                _run_review_candidate,
                analyzer,
                analyzer_factory,
                bridge_model,
                load_case_list,
                candidate,
                relative_error_limit,
                objective_scales,
            )
            for candidate in candidate_list
        ]
        return tuple(future.result() for future in futures)


def run_validation_designs(
    analyzer: BatchAnalyzer | None,
    bridge_model: BridgeModel,
    load_cases: Iterable[LoadCase],
    validation_designs: np.ndarray,
    feature_names: tuple[str, ...] = ("c", "alpha"),
    analyzer_factory: Callable[[dict[str, float]], BatchAnalyzer] | None = None,
    parallel_workers: int = 1,
    parallel_license_limit: int | None = None,
    parallel_mode: str = "thread",
) -> tuple[FEMValidationRecord, ...]:
    """Run deduplicated surrogate validation designs through the configured FEM analyzer."""

    if analyzer is None and analyzer_factory is None:
        raise ValueError("Either analyzer or analyzer_factory is required for FEM validation")
    if analyzer is not None and analyzer_factory is not None:
        raise ValueError("Provide either analyzer or analyzer_factory, not both")
    load_case_list = tuple(load_cases)
    if not load_case_list:
        raise ValueError("At least one load case is required for FEM validation")
    designs = np.asarray(validation_designs, dtype=float)
    if designs.ndim != 2:
        raise ValueError("validation_designs must be a 2D array")
    if designs.shape[1] != len(feature_names):
        raise ValueError("feature_names must match validation_designs column count")

    effective_workers = _effective_parallel_workers(parallel_workers, parallel_license_limit)
    mode = _parallel_mode(parallel_mode)
    _validate_analyzer_parallelism(analyzer, effective_workers, mode)
    if effective_workers == 1 or len(designs) <= 1:
        return tuple(
            _run_validation_design(
                analyzer,
                analyzer_factory,
                bridge_model,
                load_case_list,
                design,
                feature_names,
            )
            for design in designs
        )
    if analyzer_factory is not None:
        validation_jobs = tuple(
            (design, analyzer_factory(_design_parameters(design, feature_names)))
            for design in designs
        )
        for _, validation_analyzer in validation_jobs:
            _validate_analyzer_parallelism(validation_analyzer, effective_workers, mode)
        executor_class = _executor_class(mode)
        with executor_class(max_workers=effective_workers) as executor:
            futures = [
                executor.submit(
                    _run_validation_design,
                    validation_analyzer,
                    None,
                    bridge_model,
                    load_case_list,
                    design,
                    feature_names,
                )
                for design, validation_analyzer in validation_jobs
            ]
            return tuple(future.result() for future in futures)
    executor_class = _executor_class(mode)
    with executor_class(max_workers=effective_workers) as executor:
        futures = [
            executor.submit(
                _run_validation_design,
                analyzer,
                analyzer_factory,
                bridge_model,
                load_case_list,
                design,
                feature_names,
            )
            for design in designs
        ]
        return tuple(future.result() for future in futures)


def _run_review_candidate(
    analyzer,
    analyzer_factory,
    bridge_model: BridgeModel,
    load_cases: tuple[LoadCase, ...],
    candidate: FEMReviewCandidate,
    relative_error_limit: float,
    objective_scales: dict[str, float] | None,
) -> FEMReviewRecord:
    params = damper_params_from_candidate(candidate)
    candidate_analyzer = analyzer_factory(candidate) if analyzer_factory is not None else analyzer
    assert candidate_analyzer is not None
    results = tuple(candidate_analyzer.run_combinations(bridge_model, [params], list(load_cases)))
    checks = compare_fem_objectives(candidate, results, relative_error_limit, objective_scales)
    return FEMReviewRecord(
        candidate=candidate,
        damper_params=params,
        results=results,
        objective_checks=checks,
    )


def _run_validation_design(
    analyzer,
    analyzer_factory,
    bridge_model: BridgeModel,
    load_cases: tuple[LoadCase, ...],
    design: np.ndarray,
    feature_names: tuple[str, ...],
) -> FEMValidationRecord:
    params = damper_params_from_design(design, feature_names)
    design_parameters = _design_parameters(design, feature_names)
    validation_analyzer = (
        analyzer_factory(design_parameters) if analyzer_factory is not None else analyzer
    )
    assert validation_analyzer is not None
    results = tuple(validation_analyzer.run_combinations(bridge_model, [params], list(load_cases)))
    return FEMValidationRecord(
        design=design.copy(),
        feature_names=feature_names,
        damper_params=params,
        results=results,
    )


def _design_parameters(design: np.ndarray, feature_names: tuple[str, ...]) -> dict[str, float]:
    return {name: float(value) for name, value in zip(feature_names, design)}


def _effective_parallel_workers(
    parallel_workers: int,
    parallel_license_limit: int | None,
) -> int:
    if parallel_workers < 1:
        raise ValueError("parallel_workers must be at least 1")
    if parallel_license_limit is None:
        return int(parallel_workers)
    if parallel_license_limit < 1:
        raise ValueError("parallel_license_limit must be at least 1")
    return min(int(parallel_workers), int(parallel_license_limit))


def _parallel_mode(mode: str) -> str:
    normalized = str(mode).lower()
    if normalized not in {"thread", "process"}:
        raise ValueError("parallel_mode must be 'thread' or 'process'")
    return normalized


def _executor_class(mode: str):
    return ProcessPoolExecutor if mode == "process" else ThreadPoolExecutor


def _validate_analyzer_parallelism(analyzer, effective_workers: int, mode: str) -> None:
    solver_name = "" if analyzer is None else str(getattr(analyzer, "solver_name", ""))
    if solver_name.lower() == "openseespy_inproc" and effective_workers > 1 and mode != "process":
        raise ValueError(
            "openseespy_inproc does not support thread parallel execution; "
            "use serial execution or parallel_mode='process'"
        )


def damper_params_from_candidate(candidate: FEMReviewCandidate) -> DamperParams:
    """Convert optimization design columns into equivalent total damper parameters."""

    return damper_params_from_design(candidate.design, candidate.parameter_names)


def damper_params_from_design(design: np.ndarray, feature_names: tuple[str, ...]) -> DamperParams:
    """Convert design columns into equivalent total damper parameters."""

    array = np.asarray(design, dtype=float)
    if array.ndim != 1:
        raise ValueError("design must be a 1D array")
    if array.shape[0] != len(feature_names):
        raise ValueError("feature_names must match design column count")
    values = {name: float(value) for name, value in zip(feature_names, array)}
    if "c" not in values or "alpha" not in values:
        raise ValueError("FEM validation/review designs must include c and alpha")
    return DamperParams(c=values["c"], alpha=values["alpha"], stiffness=values.get("stiffness"))


def compare_fem_objectives(
    candidate: FEMReviewCandidate,
    results: Iterable[AnalysisResult],
    relative_error_limit: float = 0.05,
    objective_scales: dict[str, float] | None = None,
) -> tuple[FEMObjectiveCheck, ...]:
    """Compare surrogate objective labels with completed FEM result objectives."""

    scales = objective_scales or {}
    result_by_load_name = {_load_case_name(result): result for result in results}
    checks = []
    for label, surrogate_value in candidate.objective_values().items():
        scenario, objective_name = _split_objective_label(label)
        result = result_by_load_name.get(scenario)
        if result is None:
            raise ValueError(f"Missing FEM result for scenario: {scenario}")
        if result.status != "completed" or objective_name not in result.objectives:
            checks.append(
                FEMObjectiveCheck(
                    objective_name=label,
                    surrogate_value=float(surrogate_value),
                    fem_value=float("nan"),
                    absolute_error=float("inf"),
                    relative_error=float("inf"),
                    accepted=False,
                    case_id=result.case_id,
                )
            )
            continue
        fem_value = float(result.objectives[objective_name]) * float(scales.get(label, 1.0))
        absolute_error = abs(fem_value - surrogate_value)
        denominator = max(abs(float(surrogate_value)), 1.0e-12)
        relative_error = absolute_error / denominator
        checks.append(
            FEMObjectiveCheck(
                objective_name=label,
                surrogate_value=float(surrogate_value),
                fem_value=fem_value,
                absolute_error=absolute_error,
                relative_error=relative_error,
                accepted=relative_error <= relative_error_limit,
                case_id=result.case_id,
            )
        )
    return tuple(checks)


def _split_objective_label(label: str) -> tuple[str, str]:
    if ":" not in label:
        raise ValueError(f"Objective label must use 'scenario:objective' format: {label}")
    scenario, objective_name = label.split(":", 1)
    if not scenario or not objective_name:
        raise ValueError(f"Objective label must use 'scenario:objective' format: {label}")
    return scenario, objective_name


def _load_case_name(result: AnalysisResult) -> str:
    load_case = result.metadata.get("load_case", {})
    name = load_case.get("name")
    if not name:
        raise ValueError(f"Analysis result {result.case_id} does not include load_case.name metadata")
    return str(name)


def is_verified_solver_output(result: AnalysisResult, objective_name: str) -> bool:
    """Return whether one objective was loaded after a real solver command."""

    solver_name = result.solver.lower()
    if solver_name not in {"ansys", "opensees", "openseespy_inproc"}:
        return False
    design = result.metadata.get("solver_design", {})
    command_stream = result.metadata.get("command_stream", {})
    execution = result.metadata.get("command_execution", {})
    solver_summary = result.metadata.get("solver_summary", {})
    common_checks = (
        result.status == "completed"
        and design.get("execution_mode") == "run"
        and command_stream.get("dry_run") is False
        and _metadata_path_exists(command_stream)
        and _metadata_hash_matches(command_stream)
        and _metadata_path_exists(solver_summary)
        and _metadata_hash_matches(solver_summary)
        and _solver_summary_lists_objective(solver_summary, objective_name)
        and _has_finite_objective_value(result, objective_name)
        and _has_verified_load_calibration(result)
        and _has_verified_damper_calibration(result)
    )
    if solver_name == "openseespy_inproc":
        return common_checks and result.metadata.get("is_verified_solver_output") is True
    return (
        common_checks
        and execution.get("returncode") == 0
        and _execution_references_command_stream(execution, command_stream)
    )


def _metadata_path_exists(metadata: dict) -> bool:
    path = metadata.get("path")
    return bool(path) and Path(path).is_file()


def _metadata_hash_matches(metadata: dict) -> bool:
    path = metadata.get("path")
    expected = metadata.get("sha256")
    if not path or not expected:
        return False
    return sha256(Path(path).read_bytes()).hexdigest() == expected


def _solver_summary_lists_objective(metadata: dict, objective_name: str) -> bool:
    objective_names = metadata.get("objective_names", ())
    if not isinstance(objective_names, (list, tuple, set)):
        return False
    return objective_name in {str(name) for name in objective_names}


def _execution_references_command_stream(execution: dict, command_stream: dict) -> bool:
    stream_path = command_stream.get("path")
    command = execution.get("command", ())
    if not stream_path or not isinstance(command, (list, tuple)):
        return False
    resolved_stream = Path(stream_path).resolve()
    return any(Path(str(part)).resolve() == resolved_stream for part in command)


def _has_finite_objective_value(result: AnalysisResult, objective_name: str) -> bool:
    try:
        return math.isfinite(float(result.objectives[objective_name]))
    except (KeyError, TypeError, ValueError):
        return False


def _has_verified_load_calibration(result: AnalysisResult) -> bool:
    load_case = result.metadata.get("load_case", {})
    load_types = _load_types(load_case)
    if load_types == {"earthquake"}:
        return True
    metadata = load_case.get("metadata", {})
    calibration = metadata.get("load_calibration", {})
    if _calibration_artifact_verified(calibration):
        return True
    if load_case.get("load_type") != "combination":
        return False
    components = metadata.get("component_load_cases", ())
    return bool(components) and all(
        _component_has_verified_load_calibration(component)
        for component in components
    )


def _has_verified_damper_calibration(result: AnalysisResult) -> bool:
    design = result.metadata.get("solver_design", {})
    calibration = design.get("damper_calibration", {})
    return _calibration_artifact_verified(calibration)


def _component_has_verified_load_calibration(component: dict) -> bool:
    if _load_types(component) == {"earthquake"}:
        return True
    calibration = component.get("metadata", {}).get("load_calibration", {})
    return _calibration_artifact_verified(calibration)


def _calibration_artifact_verified(calibration: dict) -> bool:
    if not isinstance(calibration, dict) or calibration.get("status") != "verified":
        return False
    path = calibration.get("artifact_path") or calibration.get("path")
    artifact_metadata = {
        "path": path,
        "sha256": calibration.get("sha256"),
    }
    return _metadata_path_exists(artifact_metadata) and _metadata_hash_matches(artifact_metadata)


def _load_types(load_case: dict) -> set[str]:
    load_type = load_case.get("load_type")
    if load_type != "combination":
        return {str(load_type)}
    metadata = load_case.get("metadata", {})
    component_types = metadata.get("component_types", ())
    if component_types:
        return {str(item) for item in component_types}
    return {
        _infer_load_type(str(component))
        for component in load_case.get("components", ())
    }


def _infer_load_type(name: str) -> str:
    lowered = name.lower()
    if "eq" in lowered or "earthquake" in lowered:
        return "earthquake"
    if "traffic" in lowered or "vehicle" in lowered or "car" in lowered:
        return "traffic"
    if "wind" in lowered:
        return "wind"
    return name
