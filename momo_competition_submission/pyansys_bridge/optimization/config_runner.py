"""Config-file entrypoints for DOE optimization workflows."""

from __future__ import annotations

import json
import math
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from shutil import which
from typing import Any

import numpy as np

from pyansys_bridge.core.ansys_damper import ANSYS_DAMPER_C_SCALE
from pyansys_bridge.core.result_summary import STANDARD_OBJECTIVE_NAMES
from pyansys_bridge.core.progress_sink import write_batch_component_progress
from pyansys_bridge.models import AnalysisResult, BridgeModel, DamperParams, DamperPlacement, LoadCase


DEFAULT_TOTAL_DAMPER_BOUNDS = {"c": (5.0e4, 2.0e6), "alpha": (0.3, 1.0)}
DEFAULT_TOTAL_DAMPER_STEPS = {"c": 1.0e4, "alpha": 0.05}
PRODUCTION_SOLVER_TYPES = {"ansys", "openseespy_inproc"}
_UNSET = object()


def optimize_from_sampled_damper_doe(*args, **kwargs):
    """Lazy wrapper preserving the historical monkeypatch/import seam."""

    from .doe_pipeline import optimize_from_sampled_damper_doe as _optimize

    return _optimize(*args, **kwargs)


def optimize_from_damper_doe_batch(*args, **kwargs):
    """Lazy wrapper preserving the historical monkeypatch/import seam."""

    from .doe_pipeline import optimize_from_damper_doe_batch as _optimize

    return _optimize(*args, **kwargs)


@dataclass(frozen=True)
class BaselineOptimizationWorkflowResult:
    """Result from a baseline-first optimization workflow config."""

    baseline: AnalysisResult
    optimization: DamperDOEOptimizationResult
    baseline_config_path: Path
    optimization_config_path: Path
    optimization_summary_path: Path
    summary_path: Path

    def to_dict(self) -> dict[str, object]:
        optimization_summary = self.optimization.to_dict()
        optimization_section = optimization_summary.get("optimization", {})
        payload = {
            "baseline_config_path": str(self.baseline_config_path),
            "optimization_config_path": str(self.optimization_config_path),
            "workflow_summary_path": str(self.summary_path.resolve()),
            "baseline_summary_path": self.baseline.metadata.get("baseline", {}).get("summary_path"),
            "optimization_summary_path": str(self.optimization_summary_path.resolve()),
            "baseline_status": self.baseline.status,
            "optimization_status": str(optimization_summary.get("status", "completed")),
            "optimization_objective_names": list(optimization_section.get("objective_names", ())),
            "objective_limits": dict(self.optimization.objective_limits or {}),
        }
        if optimization_summary.get("failure_reason"):
            payload["optimization_failure_reason"] = str(optimization_summary["failure_reason"])
            payload["optimization_failure_stage"] = str(optimization_summary.get("failure_stage", ""))
        return payload


@dataclass(frozen=True)
class SolverParityWorkflowResult:
    """Result from a two-solver baseline parity workflow config."""

    reference: AnalysisResult
    candidate: AnalysisResult
    reference_config_path: Path
    candidate_config_path: Path
    summary_path: Path
    parity_report: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "reference_config_path": str(self.reference_config_path),
            "candidate_config_path": str(self.candidate_config_path),
            "summary_path": str(self.summary_path.resolve()),
            "reference_summary_path": self.reference.metadata.get("baseline", {}).get("summary_path"),
            "candidate_summary_path": self.candidate.metadata.get("baseline", {}).get("summary_path"),
            "reference_status": self.reference.status,
            "candidate_status": self.candidate.status,
            "parity": self.parity_report,
        }


def run_sampled_doe_optimization_config(
    config_path: str | Path,
    *,
    execution_timeout_s: float | None = None,
) -> DamperDOEOptimizationResult:
    """Run sampled damper DOE optimization from a JSON config file."""

    config_file = Path(config_path).resolve()
    config = _config_with_execution_timeout(_load_config(config_file), execution_timeout_s)
    return _run_sampled_doe_optimization_config_data(config, config_file.parent)


def run_solver_acceptance_case_config(
    config_path: str | Path,
    *,
    load_case_name: str | None = None,
    output_dir: str | Path | None = None,
    damper_params: dict[str, Any] | None = None,
    solver_kwargs_override: dict[str, Any] | None = None,
    omit_dampers: bool = False,
    execution_timeout_s: float | None = None,
) -> AnalysisResult:
    """Run one configured solver case without launching a full DOE workflow."""

    from pyansys_bridge.batch import BatchAnalyzer

    config_file = Path(config_path).resolve()
    config = _load_config(config_file)
    config_dir = config_file.parent
    solver, solver_kwargs, _ = _configured_solver(config, config_dir)
    solver_kwargs = solver_kwargs or {}
    solver_kwargs.update(dict(solver_kwargs_override or {}))
    if execution_timeout_s is not None:
        solver_kwargs["execution_timeout_s"] = float(execution_timeout_s)
    if omit_dampers:
        solver_kwargs["omit_dampers"] = True
    # 单个验收算例也上报时程进度；不支持的 solver 由能力声明挡掉。
    solver_kwargs = _with_case_step_progress(
        solver, solver_kwargs, _progress_dir(config, config_dir)
    ) or {}
    _validate_run_mode_output_contract(solver, solver_kwargs)

    load_case = _acceptance_load_case(config, config_dir, load_case_name)
    result_root = (
        _config_relative_path(output_dir, Path.cwd())
        if output_dir is not None
        else _acceptance_output_dir(config, config_file)
    )
    configured_damper_params = damper_params if damper_params is not None else config.get("damper_params")
    if (
        configured_damper_params is None
        and not bool(solver_kwargs.get("omit_dampers"))
        and solver_kwargs.get("damper_module")
    ):
        raise ValueError(
            "damper_params with explicit c/alpha are required when damper_module is configured; "
            "set omit_dampers=true for an undamped acceptance case"
        )
    params = _damper_params(configured_damper_params)
    analyzer = BatchAnalyzer(
        solver=solver,
        output_dir=result_root,
        solver_kwargs=solver_kwargs,
    )
    result = analyzer.run_combinations(
        _bridge_model(config["bridge_model"], config_dir),
        [params],
        [load_case],
    )[0]
    result.metadata.setdefault("acceptance_case", {}).update(
        {
            "source_config": str(config_file),
            "load_case": load_case.name,
            "summary_path": str((Path(result_root) / result.case_id / "summary.json").resolve()),
        }
    )
    analyzer.store.save(result)
    return result


def preflight_config(config_path: str | Path) -> dict[str, object]:
    """Validate a run config without launching solvers or writing result artifacts."""

    config_file = Path(config_path).resolve()
    config = _load_config(config_file)
    if "baseline_config" in config and "optimization_config" in config:
        return _preflight_baseline_optimization_workflow_config(config, config_file)
    if "reference_config" in config and "candidate_config" in config:
        return _preflight_solver_parity_workflow_config(config, config_file)
    if "objective_specs" in config:
        return _preflight_sampled_doe_optimization_config(config, config_file)
    return _preflight_undamped_baseline_config(config, config_file)


def _run_sampled_doe_optimization_config_data(
    config: dict[str, Any],
    config_dir: Path,
) -> Any:
    bridge_model, load_cases, explicit_designs, common_kwargs = _prepare_sampled_doe_execution(
        config, config_dir
    )
    output_dir = Path(common_kwargs["output_dir"])
    if explicit_designs is None:
        result = optimize_from_sampled_damper_doe(
            bridge_model,
            load_cases,
            n_doe_samples=int(config["n_doe_samples"]),
            **common_kwargs,
        )
    else:
        result = optimize_from_damper_doe_batch(
            bridge_model,
            load_cases,
            explicit_designs,
            **common_kwargs,
        )
    summary_path = config.get("summary_path")
    result.write_summary(
        Path(output_dir) / "optimization_summary.json"
        if summary_path is None
        else _config_relative_path(summary_path, config_dir)
    )
    return result


def _preflight_sampled_doe_optimization_config(
    config: dict[str, Any],
    config_file: Path,
    *,
    validate_objective_limits: bool = True,
) -> dict[str, object]:
    config_dir = config_file.parent
    design_space = _design_space(config)
    load_cases = _configured_load_cases(config, config_dir)
    objective_specs = [_objective_spec(item) for item in config["objective_specs"]]
    solver, solver_kwargs, raw_solver_kwargs = _configured_solver(config, config_dir)
    _validate_run_mode_output_contract(solver, solver_kwargs)
    _validate_validation_gate_config(config)
    parallel = _parallel_settings(config)
    _validate_parallel_solver_config(solver, parallel)
    explicit_designs = _configured_doe_designs(config, design_space)
    if validate_objective_limits:
        _optional_float_map(config.get("objective_limits"))
    return {
        "kind": "optimization",
        "config_path": str(config_file),
        "solver": solver,
        "solver_capability": _solver_capability(solver),
        **_solver_preflight_summary(raw_solver_kwargs, solver_kwargs),
        "path_checks": _preflight_path_checks(config, raw_solver_kwargs, config_dir),
        "active_load_cases": [load_case.name for load_case in load_cases],
        "load_case_types": {
            load_case.name: load_case.load_type
            for load_case in load_cases
        },
        "objective_names": [
            f"{spec.scenario}:{spec.objective}"
            for spec in objective_specs
        ],
        "feature_names": list(
            design_space["feature_names"]
            if design_space["feature_names"] is not None
            else design_space["bounds"].keys()
        ),
        "doe_design_source": "explicit" if explicit_designs is not None else "lhs",
        "doe_design_count": (
            int(explicit_designs.shape[0])
            if explicit_designs is not None
            else int(config["n_doe_samples"])
        ),
        "surrogate_cv": config.get("surrogate_cv", "auto"),
        "run_validation": bool(config.get("run_validation", False)),
        "max_validation_peak_relative_error": config.get("max_validation_peak_relative_error"),
        "max_validation_designs": config.get("max_validation_designs"),
        "run_review": bool(config.get("run_review", False)),
        "review_reuse_doe_results": bool(config.get("review_reuse_doe_results", False)),
        "max_active_learning_iterations": int(config.get("max_active_learning_iterations", 1)),
        "max_review_iterations": int(config.get("max_review_iterations", 1)),
        "parallel": parallel,
        "objective_limit_relative_tolerance": float(
            config.get("objective_limit_relative_tolerance", 0.0)
        ),
        "max_normalized_doe_distance": config.get("max_normalized_doe_distance"),
        "summary_path": str(_optimization_summary_path_from_config(config, config_dir).resolve()),
    }


def run_undamped_baseline_config(
    config_path: str | Path,
    *,
    execution_timeout_s: float | None = None,
    use_cache: bool = True,
    progress_component: str | None = None,
) -> AnalysisResult:
    """Run one load case with solver-side dampers omitted and write a baseline summary."""

    from pyansys_bridge.core.solver_factory import SolverFactory

    config_file = Path(config_path).resolve()
    config = _config_with_execution_timeout(_load_config(config_file), execution_timeout_s)
    load_case = _baseline_load_case(config, config_file.parent)
    solver, solver_kwargs, _ = _configured_solver(config, config_file.parent)
    solver_kwargs = solver_kwargs or {}
    # 单次基线分析同样上报时程进度：只有一个算例，但用户等待的时间与 DOE 单点相同。
    progress_dir = _progress_dir(config, config_file.parent)
    progress_component = progress_component or config.get("progress_component")
    if progress_dir is not None and progress_component:
        write_batch_component_progress(
            progress_dir,
            component=str(progress_component),
            completed=0,
            total=1,
        )
    solver_kwargs = _with_case_step_progress(solver, solver_kwargs, progress_dir) or {}
    _validate_run_mode_output_contract(solver, solver_kwargs)
    summary_path = _config_relative_path(
        config.get("summary_path", "output/baselines/undamped_summary.json"),
        config_file.parent,
    )
    if use_cache:
        cached = _cached_baseline_result(summary_path, config_file, load_case)
        if cached is not None:
            if progress_dir is not None and progress_component:
                write_batch_component_progress(
                    progress_dir,
                    component=str(progress_component),
                    completed=1,
                    total=1,
                )
            return cached
    solver_kwargs["omit_dampers"] = True
    result = SolverFactory.create(solver, **solver_kwargs).run_analysis(
        _bridge_model(config["bridge_model"], config_file.parent),
        load_case,
        _damper_params(config.get("damper_params")),
    )
    result.metadata.setdefault("baseline", {}).update(
        {
            "type": "undamped",
            "source_config": str(config_file),
            "load_case": load_case.name,
        }
    )
    result.metadata.setdefault("baseline", {})["summary_path"] = str(summary_path.resolve())
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    with summary_path.open("w", encoding="utf-8") as handle:
        json.dump(result.to_dict(), handle, ensure_ascii=False, indent=2)
    if progress_dir is not None and progress_component:
        write_batch_component_progress(
            progress_dir,
            component=str(progress_component),
            completed=1,
            total=1,
        )
    return result


def _preflight_undamped_baseline_config(config: dict[str, Any], config_file: Path) -> dict[str, object]:
    config_dir = config_file.parent
    load_case = _baseline_load_case(config, config_dir)
    solver, solver_kwargs, raw_solver_kwargs = _configured_solver(config, config_dir)
    _validate_run_mode_output_contract(solver, solver_kwargs)
    return {
        "kind": "undamped_baseline",
        "config_path": str(config_file),
        "solver": solver,
        "solver_capability": _solver_capability(solver),
        **_solver_preflight_summary(raw_solver_kwargs, solver_kwargs),
        "path_checks": _preflight_path_checks(config, raw_solver_kwargs, config_dir),
        "load_case": load_case.name,
        "load_type": load_case.load_type,
        "summary_path": str(
            _config_relative_path(
                config.get("summary_path", "output/baselines/undamped_summary.json"),
                config_dir,
            ).resolve()
        ),
    }


def run_undamped_baseline_optimization_workflow_config(
    config_path: str | Path,
    *,
    execution_timeout_s: float | None = None,
) -> BaselineOptimizationWorkflowResult:
    """Run an undamped baseline, then optimize with limits from that baseline."""

    workflow_file = Path(config_path).resolve()
    workflow = _load_config(workflow_file)
    baseline_config_path = _config_relative_path(workflow["baseline_config"], workflow_file.parent).resolve()
    optimization_config_path = _config_relative_path(workflow["optimization_config"], workflow_file.parent).resolve()

    optimization_config = _load_config(optimization_config_path)
    optimization_config.update(dict(workflow.get("optimization_overrides", {})))
    optimization_config = _config_with_execution_timeout(
        optimization_config,
        execution_timeout_s,
    )
    doe_count = len(optimization_config.get("doe_designs") or [])
    if doe_count == 0:
        doe_count = int(optimization_config.get("n_doe_samples") or 0)
    baseline_progress_dir = _progress_dir(
        _load_config(baseline_config_path),
        baseline_config_path.parent,
    )
    if baseline_progress_dir is not None:
        optimization_config["progress_dir"] = str(baseline_progress_dir)
    optimization_config["progress_component"] = "doe"
    optimization_config["progress_completed_offset"] = 0
    optimization_config["progress_total_cases"] = doe_count
    # DOE 阶段只受设计变量上下限约束；基线指标约束仅在两者完成后的优化后处理使用。
    optimization_config.pop("baseline_objective_limits", None)

    doe_config = dict(optimization_config)
    baseline_future = None
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="baseline-doe") as executor:
        baseline_future = executor.submit(
            run_undamped_baseline_config,
            baseline_config_path,
            execution_timeout_s=execution_timeout_s,
            progress_component="baseline",
        )
        bridge_model, load_cases, explicit_designs, common_kwargs = _prepare_sampled_doe_execution(
            doe_config,
            optimization_config_path.parent,
            objective_limits_override=None,
        )
        if explicit_designs is None:
            from pyansys_bridge.optimization.doe_pipeline import _sample_unique_doe_designs

            designs = _sample_unique_doe_designs(
                common_kwargs["bounds"],
                int(doe_config["n_doe_samples"]),
                seed=doe_config.get("seed"),
                steps=common_kwargs["steps"],
            )
        else:
            designs = explicit_designs
        from pyansys_bridge.batch import run_damper_doe_batch

        doe_future = executor.submit(
            run_damper_doe_batch,
            bridge_model,
            load_cases,
            designs,
            output_dir=common_kwargs["output_dir"],
            feature_names=common_kwargs["feature_names"],
            solver=common_kwargs["solver"],
            solver_kwargs=common_kwargs["solver_kwargs"],
            parallel_workers=common_kwargs["parallel_workers"],
            parallel_license_limit=common_kwargs["parallel_license_limit"],
            parallel_mode=common_kwargs["parallel_mode"],
            progress_dir=common_kwargs["progress_dir"],
            progress_completed_offset=0,
            progress_total_cases=doe_count,
            progress_component="doe",
        )
        baseline = baseline_future.result()
        doe_records = doe_future.result()

    baseline_summary_path = Path(baseline.metadata["baseline"]["summary_path"]).resolve()
    optimization_config["baseline_objective_limits"] = _baseline_limit_config_with_source(
        workflow.get("baseline_objective_limits"),
        baseline_summary_path,
    )
    common_kwargs["objective_limits"] = _effective_objective_limits(
        optimization_config,
        optimization_config_path.parent,
    )
    optimization_summary_path = _optimization_summary_path_from_config(
        optimization_config,
        optimization_config_path.parent,
    )
    from pyansys_bridge.optimization.doe_pipeline import optimize_from_damper_doe_batch

    optimization = optimize_from_damper_doe_batch(
        bridge_model,
        load_cases,
        designs,
        precomputed_doe_records=doe_records,
        **common_kwargs,
    )
    optimization.write_summary(optimization_summary_path)

    summary_path = _config_relative_path(
        workflow.get("summary_path", "output/workflows/baseline_optimization_summary.json"),
        workflow_file.parent,
    )
    result = BaselineOptimizationWorkflowResult(
        baseline=baseline,
        optimization=optimization,
        baseline_config_path=baseline_config_path,
        optimization_config_path=optimization_config_path,
        optimization_summary_path=optimization_summary_path,
        summary_path=summary_path,
    )
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    with summary_path.open("w", encoding="utf-8") as handle:
        json.dump(result.to_dict(), handle, ensure_ascii=False, indent=2)
    return result


def _prepare_sampled_doe_execution(
    config: dict[str, Any],
    config_dir: Path,
    *,
    objective_limits_override: dict[str, float] | None | object = _UNSET,
) -> tuple[BridgeModel, list[LoadCase], np.ndarray | None, dict[str, Any]]:
    """Prepare solver-independent DOE inputs shared by standalone and joint workflows."""

    output_dir = _config_relative_path(config.get("output_dir", "output/doe_optimization"), config_dir)
    design_space = _design_space(config)
    # 单独 DOE 只接受显式工程约束；基线指标约束由联合工作流在求解完成后注入。
    objective_limits = (
        _optional_float_map(config.get("objective_limits"))
        if objective_limits_override is _UNSET
        else objective_limits_override
    )
    solver, solver_kwargs, _ = _configured_solver(config, config_dir)
    _validate_run_mode_output_contract(solver, solver_kwargs)
    parallel = _parallel_settings(config)
    _validate_parallel_solver_config(solver, parallel)
    progress_dir = _progress_dir(config, config_dir)
    solver_kwargs = _with_case_step_progress(solver, solver_kwargs, progress_dir)
    common_kwargs = dict(
        objective_specs=[_objective_spec(item) for item in config["objective_specs"]],
        bounds=design_space["bounds"],
        output_dir=output_dir,
        feature_names=(
            design_space["feature_names"]
            if design_space["feature_names"] is not None
            else tuple(design_space["bounds"].keys())
        ),
        solver=solver,
        solver_kwargs=solver_kwargs,
        n_candidates=int(config.get("n_candidates", 200)),
        seed=config.get("seed"),
        steps=design_space["steps"],
        objective_limits=objective_limits,
        objective_limit_relative_tolerance=float(config.get("objective_limit_relative_tolerance", 0.0)),
        max_normalized_doe_distance=(
            None
            if config.get("max_normalized_doe_distance") is None
            else float(config["max_normalized_doe_distance"])
        ),
        n_validation_points=int(config.get("n_validation_points", 3)),
        max_validation_designs=(
            None
            if config.get("max_validation_designs") is None
            else int(config["max_validation_designs"])
        ),
        min_surrogate_r2=config.get("min_surrogate_r2"),
        surrogate_cv=config.get("surrogate_cv", "auto"),
        run_validation=bool(config.get("run_validation", False)),
        min_validation_r2=config.get("min_validation_r2"),
        max_validation_peak_relative_error=config.get("max_validation_peak_relative_error"),
        run_review=bool(config.get("run_review", False)),
        review_reuse_doe_results=bool(config.get("review_reuse_doe_results", False)),
        review_relative_error_limit=float(config.get("review_relative_error_limit", 0.05)),
        review_objective_scales=_optional_float_map(config.get("review_objective_scales")),
        max_active_learning_iterations=int(config.get("max_active_learning_iterations", 1)),
        max_review_iterations=int(config.get("max_review_iterations", 1)),
        parallel_workers=parallel["max_workers"] if parallel["enabled"] else 1,
        parallel_license_limit=parallel["license_limit"] if parallel["enabled"] else None,
        parallel_mode=str(parallel["mode"]),
        progress_dir=progress_dir,
        progress_completed_offset=max(0, int(config.get("progress_completed_offset", 0))),
        progress_total_cases=(
            None
            if config.get("progress_total_cases") is None
            else int(config["progress_total_cases"])
        ),
        progress_component=config.get("progress_component"),
    )
    return (
        _bridge_model(config["bridge_model"], config_dir),
        _configured_load_cases(config, config_dir),
        _configured_doe_designs(config, design_space),
        common_kwargs,
    )


def run_solver_parity_workflow_config(
    config_path: str | Path,
    *,
    execution_timeout_s: float | None = None,
) -> SolverParityWorkflowResult:
    """Run two undamped baseline configs and compare their solver summaries."""

    from pyansys_bridge.core.solver_parity import (
        ParityTolerance,
        compare_solver_summary_files,
        write_parity_report,
    )

    workflow_file = Path(config_path).resolve()
    workflow = _load_config(workflow_file)
    reference_config_path = _config_relative_path(workflow["reference_config"], workflow_file.parent).resolve()
    candidate_config_path = _config_relative_path(workflow["candidate_config"], workflow_file.parent).resolve()
    reference = run_undamped_baseline_config(
        reference_config_path,
        execution_timeout_s=execution_timeout_s,
        use_cache=False,
    )
    candidate = run_undamped_baseline_config(
        candidate_config_path,
        execution_timeout_s=execution_timeout_s,
        use_cache=False,
    )
    reference_summary_path = Path(reference.metadata["baseline"]["summary_path"]).resolve()
    candidate_summary_path = Path(candidate.metadata["baseline"]["summary_path"]).resolve()
    parity_config = dict(workflow.get("parity", {}))
    report = compare_solver_summary_files(
        reference_summary_path,
        candidate_summary_path,
        default_tolerance=ParityTolerance(
            relative=float(parity_config.get("relative_tolerance", 0.10)),
            absolute=float(parity_config.get("absolute_tolerance", 0.0)),
        ),
    )
    summary_path = _config_relative_path(
        workflow.get("summary_path", "output/workflows/solver_parity_report.json"),
        workflow_file.parent,
    )
    write_parity_report(report, summary_path)
    result = SolverParityWorkflowResult(
        reference=reference,
        candidate=candidate,
        reference_config_path=reference_config_path,
        candidate_config_path=candidate_config_path,
        summary_path=summary_path,
        parity_report=report,
    )
    with summary_path.open("w", encoding="utf-8") as handle:
        json.dump(result.to_dict(), handle, ensure_ascii=False, indent=2)
    return result


def _preflight_baseline_optimization_workflow_config(
    workflow: dict[str, Any],
    workflow_file: Path,
) -> dict[str, object]:
    workflow_dir = workflow_file.parent
    baseline_config_path = _config_relative_path(workflow["baseline_config"], workflow_dir).resolve()
    optimization_config_path = _config_relative_path(workflow["optimization_config"], workflow_dir).resolve()
    baseline_config = _load_config(baseline_config_path)
    optimization_config = _load_config(optimization_config_path)
    optimization_config.update(dict(workflow.get("optimization_overrides", {})))
    if workflow.get("baseline_objective_limits", optimization_config.get("baseline_objective_limits")) is None:
        raise ValueError("baseline-first workflow requires baseline_objective_limits")
    summary_path = _config_relative_path(
        workflow.get("summary_path", "output/workflows/baseline_optimization_summary.json"),
        workflow_dir,
    )
    return {
        "kind": "baseline_optimization_workflow",
        "config_path": str(workflow_file),
        "baseline": _preflight_undamped_baseline_config(baseline_config, baseline_config_path),
        "optimization": _preflight_sampled_doe_optimization_config(
            optimization_config,
            optimization_config_path,
            validate_objective_limits=False,
        ),
        "workflow_summary_path": str(summary_path.resolve()),
    }


def _preflight_solver_parity_workflow_config(
    workflow: dict[str, Any],
    workflow_file: Path,
) -> dict[str, object]:
    workflow_dir = workflow_file.parent
    reference_config_path = _config_relative_path(workflow["reference_config"], workflow_dir).resolve()
    candidate_config_path = _config_relative_path(workflow["candidate_config"], workflow_dir).resolve()
    reference_config = _load_config(reference_config_path)
    candidate_config = _load_config(candidate_config_path)
    parity_config = dict(workflow.get("parity", {}))
    summary_path = _config_relative_path(
        workflow.get("summary_path", "output/workflows/solver_parity_report.json"),
        workflow_dir,
    )
    return {
        "kind": "solver_parity_workflow",
        "config_path": str(workflow_file),
        "reference": _preflight_undamped_baseline_config(reference_config, reference_config_path),
        "candidate": _preflight_undamped_baseline_config(candidate_config, candidate_config_path),
        "relative_tolerance": float(parity_config.get("relative_tolerance", 0.10)),
        "absolute_tolerance": float(parity_config.get("absolute_tolerance", 0.0)),
        "summary_path": str(summary_path.resolve()),
    }


def _load_config(path: Path) -> dict[str, Any]:
    if path.suffix.lower() != ".json":
        raise ValueError("Only JSON optimization configs are supported")
    with path.open("r", encoding="utf-8") as handle:
        config = json.load(handle)
    if not isinstance(config, dict):
        raise ValueError("Optimization config must be a JSON object")
    return config


def _config_with_execution_timeout(
    config: dict[str, Any],
    execution_timeout_s: float | None,
) -> dict[str, Any]:
    if execution_timeout_s is None:
        return config
    updated = dict(config)
    if isinstance(updated.get("solver"), dict):
        solver_config = dict(updated["solver"])
        solver_kwargs = dict(solver_config.get("kwargs") or {})
        solver_kwargs["execution_timeout_s"] = float(execution_timeout_s)
        solver_config["kwargs"] = solver_kwargs
        updated["solver"] = solver_config
        return updated
    solver_kwargs = dict(updated.get("solver_kwargs") or {})
    solver_kwargs["execution_timeout_s"] = float(execution_timeout_s)
    updated["solver_kwargs"] = solver_kwargs
    return updated


def _configured_solver(
    config: dict[str, Any],
    config_dir: Path | None,
) -> tuple[str, dict[str, Any] | None, dict[str, Any]]:
    from pyansys_bridge.core.solver_factory import SolverConfig

    if "solver" not in config:
        raise ValueError("solver.type is required in optimization config")
    solver_config = SolverConfig.from_mapping(
        config.get("solver"),
        legacy_kwargs=config.get("solver_kwargs"),
    )
    raw_kwargs = dict(solver_config.kwargs)
    return solver_config.type, _solver_kwargs(raw_kwargs, config_dir), raw_kwargs


def _solver_capability(solver: str) -> dict[str, object]:
    from pyansys_bridge.core.solver_factory import SolverFactory

    return SolverFactory.capability(solver)


def _validate_run_mode_output_contract(solver: str, solver_kwargs: dict[str, Any] | None) -> None:
    kwargs = dict(solver_kwargs or {})
    if str(kwargs.get("execution_mode", "dry_run")) != "run":
        return
    if solver.lower() not in {"ansys", "opensees"}:
        return
    if kwargs.get("postprocessor") is None:
        raise ValueError(
            f"{solver} run-mode config requires a postprocessor so solver output "
            "can be converted into summary.json or timeseries.csv"
        )


def _validate_validation_gate_config(config: dict[str, Any]) -> None:
    if not bool(config.get("run_validation", False)) and config.get("max_validation_peak_relative_error") is not None:
        raise ValueError("FEM validation accuracy gate requires run_validation=True")
    if float(config.get("objective_limit_relative_tolerance", 0.0)) < 0:
        raise ValueError("objective_limit_relative_tolerance cannot be negative")
    if (
        config.get("max_normalized_doe_distance") is not None
        and float(config["max_normalized_doe_distance"]) <= 0
    ):
        raise ValueError("max_normalized_doe_distance must be positive")
    if config.get("max_validation_designs") is not None and int(config["max_validation_designs"]) <= 0:
        raise ValueError("max_validation_designs must be positive")
    if int(config.get("max_active_learning_iterations", 1)) < 0:
        raise ValueError("max_active_learning_iterations cannot be negative")
    if int(config.get("max_review_iterations", 1)) < 0:
        raise ValueError("max_review_iterations cannot be negative")


def _parallel_settings(config: dict[str, Any]) -> dict[str, object]:
    raw = config.get("parallel") or {}
    if not isinstance(raw, dict):
        raise ValueError("parallel config must be a JSON object")
    enabled = bool(raw.get("enabled", False))
    max_workers = int(raw.get("max_workers", 1))
    license_limit = raw.get("license_limit")
    mode = str(raw.get("mode", "thread")).lower()
    if max_workers < 1:
        raise ValueError("parallel.max_workers must be at least 1")
    if mode not in {"thread", "process"}:
        raise ValueError("parallel.mode must be 'thread' or 'process'")
    if license_limit is not None:
        license_limit = int(license_limit)
        if license_limit < 1:
            raise ValueError("parallel.license_limit must be at least 1")
    return {
        "enabled": enabled,
        "max_workers": max_workers,
        "license_limit": license_limit,
        "mode": mode,
    }


def _progress_dir(config: dict[str, Any], config_dir: Path | None) -> Path | None:
    """解析可选的 ``progress_dir``；未配置时进度上报整体关闭。"""

    raw = config.get("progress_dir")
    if raw is None:
        return None
    return Path(_config_relative_path(raw, config_dir))


def _with_case_step_progress(
    solver: str,
    solver_kwargs: dict[str, Any] | None,
    progress_dir: Path | None,
) -> dict[str, Any] | None:
    """只给声明了 ``case_step_progress`` 的 solver 注入 ``progress_dir``。

    solver kwargs 直接展开进构造函数；只有明确声明能力的 solver 才接收该
    参数，避免把观测配置传给不兼容的适配器。
    """

    if progress_dir is None:
        return solver_kwargs
    capability = _solver_capability(solver)
    if "case_step_progress" not in (capability.get("supported_features") or ()):
        return solver_kwargs
    kwargs = dict(solver_kwargs or {})
    kwargs["progress_dir"] = progress_dir
    return kwargs


def _validate_parallel_solver_config(solver: str, parallel: dict[str, object]) -> None:
    if str(solver).lower() != "openseespy_inproc":
        return
    if (
        bool(parallel.get("enabled"))
        and int(parallel.get("max_workers", 1)) > 1
        and str(parallel.get("mode", "thread")) != "process"
    ):
        raise ValueError(
            "openseespy_inproc does not support thread parallel execution; "
            "use serial execution or a future multiprocessing worker pool"
        )


def _solver_preflight_summary(
    raw_solver_kwargs: dict[str, Any] | None,
    solver_kwargs: dict[str, Any] | None,
) -> dict[str, object]:
    raw_kwargs = dict(raw_solver_kwargs or {})
    kwargs = dict(solver_kwargs or {})
    raw_postprocessor = raw_kwargs.get("postprocessor")
    return {
        "execution_mode": str(kwargs.get("execution_mode", "dry_run")),
        "postprocessor_configured": kwargs.get("postprocessor") is not None,
        "postprocessor_mode": (
            raw_postprocessor.get("mode")
            if isinstance(raw_postprocessor, dict)
            else None
        ),
    }


def _preflight_path_checks(
    config: dict[str, Any],
    raw_solver_kwargs: dict[str, Any] | None,
    config_dir: Path,
) -> dict[str, dict[str, object]]:
    checks = {
        "bridge_model.source_path": _path_check(config["bridge_model"]["source_path"], config_dir),
    }
    kwargs = dict(raw_solver_kwargs or {})
    for name in ("model_path", "sim_workdir"):
        if name in kwargs:
            checks[f"solver_kwargs.{name}"] = _path_check(kwargs[name], config_dir)
    executable_name = _solver_executable_key(kwargs)
    if executable_name is not None:
        checks[f"solver_kwargs.{executable_name}"] = _path_check(
            kwargs[executable_name],
            config_dir,
            as_executable=True,
        )
    return checks


def _solver_executable_key(raw_solver_kwargs: dict[str, Any]) -> str | None:
    if "mapdl_executable" in raw_solver_kwargs:
        return "mapdl_executable"
    if "python_executable" in raw_solver_kwargs:
        return "python_executable"
    return None


def _path_check(path: str | Path, config_dir: Path, *, as_executable: bool = False) -> dict[str, object]:
    if as_executable and _looks_like_shell_command(path):
        resolved = which(str(path))
        return {
            "path": str(path) if resolved is None else resolved,
            "exists": resolved is not None,
        }
    resolved = _config_relative_path(path, config_dir).resolve()
    return {
        "path": str(resolved),
        "exists": resolved.exists(),
    }


def _looks_like_shell_command(path: str | Path) -> bool:
    if not isinstance(path, str):
        return False
    source = Path(path)
    return not source.is_absolute() and source.parent == Path(".")


def _optimization_summary_path_from_config(config: dict[str, Any], config_dir: Path) -> Path:
    output_dir = _config_relative_path(config.get("output_dir", "output/doe_optimization"), config_dir)
    summary_path = config.get("summary_path")
    return Path(output_dir) / "optimization_summary.json" if summary_path is None else _config_relative_path(
        summary_path,
        config_dir,
    )


def _baseline_limit_config_with_source(config: Any, source: Path) -> Any:
    if config is None:
        raise ValueError("baseline-first workflow requires baseline_objective_limits")
    if isinstance(config, list):
        return [_baseline_limit_config_with_source(entry, source) for entry in config]
    if not isinstance(config, dict):
        raise ValueError("baseline_objective_limits entries must be JSON objects")
    updated = dict(config)
    updated["source"] = str(source)
    return updated


def _baseline_load_case(config: dict[str, Any], config_dir: Path) -> LoadCase:
    if "load_case" in config:
        return _load_case(config["load_case"], config_dir)
    cases = _configured_load_cases(config, config_dir)
    if len(cases) != 1:
        raise ValueError("Undamped baseline config must select exactly one load case")
    return cases[0]


def _cached_baseline_result(
    summary_path: Path,
    config_file: Path,
    load_case: LoadCase,
) -> AnalysisResult | None:
    if not summary_path.exists():
        return None
    payload = _load_config(summary_path)
    metadata = payload.get("metadata", {})
    baseline = metadata.get("baseline", {})
    if payload.get("status") != "completed":
        return None
    if baseline.get("type") != "undamped":
        return None
    if baseline.get("source_config") != str(config_file):
        return None
    if baseline.get("load_case") != load_case.name:
        return None
    if not _cached_load_case_matches(metadata.get("load_case"), load_case):
        return None
    objectives = payload.get("objectives", {})
    if not isinstance(objectives, dict) or not objectives or not _finite_values(objectives):
        return None
    if _requires_standard_objective_cache_contract(payload) and not set(STANDARD_OBJECTIVE_NAMES).issubset(objectives):
        return None
    if not _run_mode_artifacts_are_valid(payload):
        return None
    return _analysis_result_from_payload(payload)


def _cached_load_case_matches(cached: Any, load_case: LoadCase) -> bool:
    if not isinstance(cached, dict):
        return False
    expected = load_case.to_dict()
    for key in ("name", "load_type", "dt", "duration", "scale", "file_hash"):
        if cached.get(key) != expected.get(key):
            return False
    if not _same_cache_path(cached.get("path"), expected.get("path")):
        return False
    for key in ("direction", "components"):
        if cached.get(key) != expected.get(key):
            return False
    return _load_case_cache_payload(cached.get("metadata", {})) == _load_case_cache_payload(
        expected.get("metadata", {})
    )


def _load_case_cache_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _load_case_cache_payload(item)
            for key, item in value.items()
            if key != "load_calibration"
        }
    if isinstance(value, list):
        return [_load_case_cache_payload(item) for item in value]
    return value


def _same_cache_path(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return left is None and right is None
    return str(Path(str(left)).resolve()) == str(Path(str(right)).resolve())


def _requires_standard_objective_cache_contract(payload: dict[str, Any]) -> bool:
    return str(payload.get("solver")) in PRODUCTION_SOLVER_TYPES


def _analysis_result_from_payload(payload: dict[str, Any]) -> AnalysisResult:
    return AnalysisResult(
        case_id=payload["case_id"],
        solver=payload["solver"],
        status=payload["status"],
        objectives={key: float(value) for key, value in payload.get("objectives", {}).items()},
        timeseries={
            key: [float(item) for item in values]
            for key, values in payload.get("timeseries", {}).items()
        },
        metadata=payload.get("metadata", {}),
        started_at=payload.get("started_at"),
        finished_at=payload.get("finished_at"),
    )


def _run_mode_artifacts_are_valid(payload: dict[str, Any]) -> bool:
    metadata = payload.get("metadata", {})
    solver_design = metadata.get("solver_design", {})
    if solver_design.get("execution_mode") != "run":
        return True
    solver_name = str(payload.get("solver", ""))
    command_stream = metadata.get("command_stream", {})
    command_execution = metadata.get("command_execution", {})
    solver_summary = metadata.get("solver_summary", {})
    common_valid = (
        command_stream.get("dry_run") is False
        and _artifact_hash_matches(command_stream)
        and _artifact_hash_matches(solver_summary)
        and _postprocessor_contract_is_current(command_stream)
        and _solver_summary_lists_objectives(solver_summary, payload.get("objectives", {}))
    )
    if solver_name.lower() == "openseespy_inproc":
        return common_valid
    return (
        common_valid
        and command_execution.get("returncode") == 0
        and _execution_references_command_stream(command_execution, command_stream)
    )


def _postprocessor_contract_is_current(command_stream: dict[str, Any]) -> bool:
    contract = command_stream.get("postprocessor_contract", {})
    if contract.get("type") != "ansys_dpf_rst":
        return True
    return contract.get("damper_force_policy") == "physical_elements_only"


def _artifact_hash_matches(metadata: dict[str, Any]) -> bool:
    path = metadata.get("path")
    expected = metadata.get("sha256")
    if not path or not expected:
        return False
    artifact = Path(path)
    return artifact.is_file() and sha256(artifact.read_bytes()).hexdigest() == expected


def _execution_references_command_stream(execution: dict[str, Any], command_stream: dict[str, Any]) -> bool:
    stream_path = command_stream.get("path")
    command = execution.get("command", ())
    if not stream_path or not isinstance(command, (list, tuple)):
        return False
    resolved_stream = Path(stream_path).resolve()
    return any(Path(str(part)).resolve() == resolved_stream for part in command)


def _solver_summary_lists_objectives(
    solver_summary: dict[str, Any],
    objectives: dict[str, Any],
) -> bool:
    objective_names = solver_summary.get("objective_names", ())
    if not isinstance(objective_names, (list, tuple)):
        return False
    return set(objectives).issubset(set(str(name) for name in objective_names))


def _finite_values(values: dict[str, Any]) -> bool:
    try:
        return all(math.isfinite(float(value)) for value in values.values())
    except (TypeError, ValueError):
        return False


def _acceptance_load_case(
    config: dict[str, Any],
    config_dir: Path,
    load_case_name: str | None,
) -> LoadCase:
    if "load_case" in config:
        load_case = _load_case(config["load_case"], config_dir)
        if load_case_name is not None and load_case.name != load_case_name:
            raise ValueError(f"Acceptance config has no load case named: {load_case_name}")
        return load_case
    cases = _configured_load_cases(config, config_dir)
    if load_case_name is not None:
        return _named_load_case({case.name: case for case in cases}, load_case_name)
    if len(cases) != 1:
        raise ValueError("Acceptance case requires --load-case when multiple active load cases are configured")
    return cases[0]


def _acceptance_output_dir(config: dict[str, Any], config_file: Path) -> Path:
    config_dir = config_file.parent
    if "acceptance_output_dir" in config:
        return _config_relative_path(config["acceptance_output_dir"], config_dir)
    if "output_dir" in config:
        return _config_relative_path(config["output_dir"], config_dir) / "acceptance_cases"
    if "summary_path" in config:
        return _config_relative_path(config["summary_path"], config_dir).parent / f"{config_file.stem}_acceptance"
    return Path("output/run_acceptance") / config_file.stem


def _bridge_model(config: dict[str, Any], config_dir: Path | None = None) -> BridgeModel:
    metadata = _model_metadata(config.get("metadata"), config_dir)
    return BridgeModel(
        name=config["name"],
        source_path=str(_config_relative_path(config["source_path"], config_dir)),
        unit_system=config.get("unit_system", "SI"),
        critical_nodes=dict(config.get("critical_nodes", {})),
        metadata=metadata,
    )


def _load_case(config: dict[str, Any], config_dir: Path | None = None) -> LoadCase:
    path = config.get("path")
    return LoadCase(
        name=config["name"],
        load_type=config["load_type"],
        path=None if path is None else str(_config_relative_path(path, config_dir)),
        dt=config.get("dt"),
        duration=config.get("duration"),
        scale=float(config.get("scale", 1.0)),
        direction=dict(config.get("direction", {})),
        components=tuple(config.get("components", ())),
        metadata=_metadata(config.get("metadata"), config_dir),
    )


def _configured_load_cases(config: dict[str, Any], config_dir: Path | None = None) -> list[LoadCase]:
    from pyansys_bridge.batch.load_combiner import combine_load_cases

    cases_by_name = {}
    for item in config["load_cases"]:
        load_case = _load_case(item, config_dir)
        cases_by_name[load_case.name] = load_case

    for item in config.get("load_combinations", ()):
        component_names = item["components"]
        components = [_named_load_case(cases_by_name, name) for name in component_names]
        combined = combine_load_cases(
            name=item["name"],
            load_cases=components,
            scale=float(item.get("scale", 1.0)),
            metadata=_metadata(item.get("metadata"), config_dir),
        )
        cases_by_name[combined.name] = combined

    active_names = config.get("active_load_cases")
    if active_names is None:
        return list(cases_by_name.values())
    return [_named_load_case(cases_by_name, name) for name in active_names]


def _named_load_case(cases_by_name: dict[str, LoadCase], name: str) -> LoadCase:
    try:
        return cases_by_name[name]
    except KeyError as exc:
        raise ValueError(f"Unknown load case referenced in optimization config: {name}") from exc


def _objective_spec(config: dict[str, Any]):
    from .doe_pipeline import DamperDOEObjectiveSpec

    return DamperDOEObjectiveSpec(
        scenario=config["scenario"],
        objective=config["objective"],
        weight=float(config.get("weight", 1.0)),
        decision_scenario=config.get("decision_scenario"),
    )


def _damper_params(config: dict[str, Any] | None) -> DamperParams:
    values = dict(config or {})
    return DamperParams(
        c=float(values.get("c", 1.0)),
        alpha=float(values.get("alpha", 1.0)),
        stiffness=None if values.get("stiffness") is None else float(values["stiffness"]),
        regularization_velocity=(
            None
            if values.get("regularization_velocity") is None
            else float(values["regularization_velocity"])
        ),
    )


def _metadata(config: dict[str, Any] | None, config_dir: Path | None = None) -> dict[str, Any]:
    metadata = dict(config or {})
    calibration = metadata.get("load_calibration")
    if isinstance(calibration, dict):
        metadata["load_calibration"] = _calibration(calibration, config_dir)
    return metadata


def _model_metadata(config: dict[str, Any] | None, config_dir: Path | None = None) -> dict[str, Any]:
    metadata = dict(config or {})
    for load_kind in ("wind", "traffic"):
        path_key = f"{load_kind}_load_mappings_path"
        if path_key in metadata:
            source = _config_relative_path(metadata[path_key], config_dir)
            metadata[f"{load_kind}_load_mappings"] = json.loads(source.read_text(encoding="utf-8"))
            metadata[path_key] = str(source)
    return metadata


def _calibration(config: dict[str, Any], config_dir: Path | None = None) -> dict[str, Any]:
    calibration = dict(config)
    for key in ("artifact_path", "path"):
        if key in calibration:
            calibration[key] = str(_config_relative_path(calibration[key], config_dir))
    return calibration


def _bounds(config: dict[str, Any]) -> dict[str, tuple[float, float]]:
    return {
        name: (float(values[0]), float(values[1]))
        for name, values in config.items()
    }


def _design_space(config: dict[str, Any]) -> dict[str, Any]:
    bounds = _bounds(config.get("bounds", DEFAULT_TOTAL_DAMPER_BOUNDS))
    steps = _optional_float_map(config.get("steps", DEFAULT_TOTAL_DAMPER_STEPS))
    feature_names = _optional_tuple(config.get("feature_names"))
    count_bounds = config.get("physical_count_per_tower_bounds")
    if count_bounds is not None:
        count_low, count_high = _physical_count_bounds(count_bounds)
        bounds["physical_count_per_tower"] = (float(count_low), float(count_high))
        steps = dict(steps or {})
        steps.setdefault("physical_count_per_tower", 1.0)
        if feature_names is not None and "physical_count_per_tower" not in feature_names:
            raise ValueError(
                "physical_count_per_tower_bounds requires feature_names to include physical_count_per_tower"
            )
        if feature_names is None:
            feature_names = tuple(bounds.keys())
    return {
        "bounds": bounds,
        "steps": steps,
        "feature_names": feature_names,
    }


def _configured_doe_designs(
    config: dict[str, Any],
    design_space: dict[str, Any],
) -> np.ndarray | None:
    raw = config.get("doe_designs")
    if raw is None:
        return None
    if not isinstance(raw, list) or not raw:
        raise ValueError("doe_designs must be a non-empty list")
    bounds = design_space["bounds"]
    feature_names = tuple(design_space["feature_names"] or bounds.keys())
    rows = []
    for item in raw:
        if isinstance(item, dict):
            try:
                rows.append([float(item[name]) for name in feature_names])
            except KeyError as exc:
                raise ValueError(f"doe_designs entry is missing feature: {exc.args[0]}") from exc
        else:
            rows.append([float(value) for value in item])
    designs = np.asarray(rows, dtype=float)
    if designs.ndim != 2:
        raise ValueError("doe_designs must be a 2D design matrix")
    if designs.shape[1] != len(feature_names):
        raise ValueError("doe_designs column count must match feature_names")
    for col, name in enumerate(feature_names):
        low, high = bounds[name]
        if np.any(designs[:, col] < low) or np.any(designs[:, col] > high):
            raise ValueError(f"doe_designs values for {name} must stay within configured bounds")
    return designs


def _physical_count_bounds(values) -> tuple[int, int]:
    if len(values) != 2:
        raise ValueError("physical_count_per_tower_bounds must contain exactly two values")
    low = float(values[0])
    high = float(values[1])
    if not low.is_integer() or not high.is_integer():
        raise ValueError("physical_count_per_tower_bounds must be positive integers")
    low_int = int(low)
    high_int = int(high)
    if low_int <= 0 or high_int <= 0 or high_int <= low_int:
        raise ValueError("physical_count_per_tower_bounds must be positive integers with high > low")
    return low_int, high_int


def _optional_float_map(config: dict[str, Any] | None) -> dict[str, float] | None:
    if config is None:
        return None
    return {name: float(value) for name, value in config.items()}


def _effective_objective_limits(config: dict[str, Any], config_dir: Path) -> dict[str, float] | None:
    explicit_limits = _optional_float_map(config.get("objective_limits")) or {}
    baseline_limits = _baseline_objective_limits(config.get("baseline_objective_limits"), config_dir)
    merged = dict(baseline_limits)
    for name, limit in explicit_limits.items():
        merged[name] = min(float(limit), float(merged[name])) if name in merged else float(limit)
    return merged or None


def _baseline_objective_limits(config: Any, config_dir: Path) -> dict[str, float]:
    if config is None:
        return {}
    entries = config if isinstance(config, list) else [config]
    limits: dict[str, float] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("baseline_objective_limits entries must be JSON objects")
        scenario = str(entry["scenario"])
        baseline_values: dict[str, float] = {}
        source = entry.get("source")
        if source is not None:
            baseline_values.update(_load_baseline_objectives(source, config_dir))
        values = entry.get("values")
        if values is not None:
            baseline_values.update(_optional_float_map(values) or {})

        objectives = entry.get("objectives")
        if objectives is None:
            objectives = sorted(baseline_values)
        if isinstance(objectives, dict):
            for target, source_name_or_value in objectives.items():
                limits[f"{scenario}:{target}"] = _baseline_limit_value(
                    baseline_values,
                    source_name_or_value,
                )
        else:
            for objective in objectives:
                objective_name = str(objective)
                limits[f"{scenario}:{objective_name}"] = _baseline_limit_value(
                    baseline_values,
                    objective_name,
                )
    return limits


def _load_baseline_objectives(source: str | Path, config_dir: Path) -> dict[str, float]:
    source_path = _config_relative_path(source, config_dir)
    payload = _load_config(source_path)
    objectives = payload.get("objectives")
    if objectives is None:
        objectives = payload
    if not isinstance(objectives, dict):
        raise ValueError("Baseline objective source must contain an objectives object")
    return {name: float(value) for name, value in objectives.items()}


def _baseline_limit_value(baseline_values: dict[str, float], source_name_or_value: Any) -> float:
    if isinstance(source_name_or_value, int | float):
        return float(source_name_or_value)
    source_name = str(source_name_or_value)
    try:
        return float(baseline_values[source_name])
    except KeyError as exc:
        raise ValueError(f"Baseline objective is missing value for: {source_name}") from exc


def _optional_tuple(values) -> tuple[str, ...] | None:
    if values is None:
        return None
    return tuple(values)


def _solver_kwargs(config: dict[str, Any] | None, config_dir: Path | None = None) -> dict[str, Any] | None:
    if config is None:
        return None
    kwargs = dict(config)
    if "model_path" in kwargs:
        kwargs["model_path"] = str(_config_relative_path(kwargs["model_path"], config_dir))
    damper_calibration = kwargs.get("damper_calibration")
    if isinstance(damper_calibration, dict):
        kwargs["damper_calibration"] = _calibration(damper_calibration, config_dir)
    modules = kwargs.get("modules")
    if isinstance(modules, list):
        kwargs["modules"] = [_command_module(module, config_dir) for module in modules]
    placements = kwargs.get("damper_placements")
    if isinstance(placements, list):
        kwargs["damper_placements"] = tuple(_damper_placement(placement) for placement in placements)
    postprocessor = kwargs.get("postprocessor")
    if isinstance(postprocessor, dict):
        kwargs["postprocessor"] = _postprocessor(postprocessor, config_dir)
    return kwargs


def _damper_placement(config: dict[str, Any]) -> DamperPlacement:
    return DamperPlacement(
        name=config["name"],
        node_i=int(config["node_i"]),
        node_j=int(config["node_j"]),
        direction=config.get("direction", "X"),
        physical_count=int(config.get("physical_count", 1)),
    )


def _command_module(config: dict[str, Any], config_dir: Path | None = None):
    from pyansys_bridge.core.command_stream import CommandModule

    if "path" in config:
        path = _config_relative_path(config["path"], config_dir)
        return CommandModule.from_file(
            name=config["name"],
            solver=config["solver"],
            role=config["role"],
            path=path,
        )
    return CommandModule(
        name=config["name"],
        solver=config["solver"],
        role=config["role"],
        content=config["content"],
        metadata=dict(config.get("metadata", {})),
    )


def _config_relative_path(path: str | Path, config_dir: Path | None) -> Path:
    source = Path(path)
    if source.is_absolute() or config_dir is None:
        return source
    return config_dir / source


def _postprocessor(config: dict[str, Any], config_dir: Path | None = None):
    from pyansys_bridge.core.postprocessor import (
        CommandPostprocessor,
        CompositePostprocessor,
        CsvTimeseriesPostprocessor,
        FilePostprocessor,
        ansys_dpf_multi_csv_postprocessor,
        ansys_dpf_rst_postprocessor,
        ansys_dpf_timeseries_postprocessor,
        opensees_csv_postprocessor,
    )

    if "steps" in config:
        return CompositePostprocessor(
            steps=tuple(_postprocessor(step, config_dir) for step in config["steps"])
        )
    mode = config["mode"]
    if mode == "command":
        return CommandPostprocessor(
            command=tuple(config["command"]),
            timeout_s=config.get("timeout_s"),
            log_name=config.get("log_name", "postprocess.log"),
        )
    if mode == "csv":
        return CsvTimeseriesPostprocessor(
            source=_postprocessor_source_path(config["source"], config_dir),
            columns={
                name: tuple(sources) if isinstance(sources, list) else sources
                for name, sources in config["columns"].items()
            },
        )
    if mode == "file":
        return FilePostprocessor(
            summary_source=_optional_postprocessor_source_path(config.get("summary_source"), config_dir),
            timeseries_source=_optional_postprocessor_source_path(config.get("timeseries_source"), config_dir),
        )
    if mode == "ansys-dpf-csv":
        source = config.get("source")
        return ansys_dpf_timeseries_postprocessor(
            "ansys_dpf_export.csv" if source is None else _postprocessor_source_path(source, config_dir)
        )
    if mode == "ansys-dpf-multi-csv":
        output_dir = config.get("output_dir")
        return ansys_dpf_multi_csv_postprocessor(
            config["job_name"],
            output_dir=None if output_dir is None else _postprocessor_source_path(output_dir, config_dir),
        )
    if mode == "ansys-dpf-nodes":
        from pyansys_bridge.core.postprocessor import ansys_dpf_node_response_postprocessor

        return ansys_dpf_node_response_postprocessor(
            response_nodes=config["response_nodes"],
            response_component=config.get("response_component", 0),
            response_elements=config.get("response_elements", ()),
            ansys_path=config.get("ansys_path"),
        )
    if mode == "ansys-dpf-rst":
        return ansys_dpf_rst_postprocessor(
            response_nodes=config.get("response_nodes", (36, 107)),
            response_component=config.get("response_component", 0),
            cumulative_displacement_node=config.get("cumulative_displacement_node", 107),
            damper_pairs=tuple(
                tuple(pair) for pair in config.get("damper_pairs", ())
            ) or None,
            damper_component=config.get("damper_component", 0),
            damper_c_scale=config.get("damper_c_scale", ANSYS_DAMPER_C_SCALE),
            ansys_path=config.get("ansys_path"),
        )
    if mode == "opensees-csv":
        source = config.get("source")
        return opensees_csv_postprocessor(
            "timeseries.csv" if source is None else _postprocessor_source_path(source, config_dir)
        )
    raise ValueError(f"Unsupported solver postprocessor mode: {mode}")


def _optional_postprocessor_source_path(path: str | Path | None, config_dir: Path | None) -> str | None:
    if path is None:
        return None
    return _postprocessor_source_path(path, config_dir)


def _postprocessor_source_path(path: str | Path, config_dir: Path | None) -> str:
    source = Path(path)
    if source.is_absolute() or config_dir is None:
        return str(source)
    config_relative = config_dir / source
    if config_relative.exists():
        return str(config_relative)
    return str(source)
