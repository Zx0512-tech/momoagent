"""DOE-to-batch bridge helpers."""

from __future__ import annotations

from collections.abc import Iterable
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from pyansys_bridge.core.progress_sink import write_batch_component_progress, write_batch_progress
from pyansys_bridge.models import (
    AnalysisResult,
    BridgeModel,
    DamperParams,
    LoadCase,
    physical_count_as_int,
    with_physical_count,
)
from pyansys_bridge.optimization.fem_review import damper_params_from_design

from .batch_analyzer import BatchAnalyzer


@dataclass(frozen=True)
class DamperDOERecord:
    """Batch analysis results for one damper DOE design point."""

    design: np.ndarray
    feature_names: tuple[str, ...]
    damper_params: DamperParams
    results: tuple[AnalysisResult, ...]

    def design_parameters(self) -> dict[str, float]:
        return {name: float(value) for name, value in zip(self.feature_names, self.design)}


def run_damper_doe_batch(
    bridge_model: BridgeModel,
    load_cases: Iterable[LoadCase],
    designs: np.ndarray,
    output_dir: str | Path = "output/doe_batch",
    feature_names: tuple[str, ...] = ("c", "alpha"),
    solver: str = "mock",
    solver_kwargs: dict | None = None,
    parallel_workers: int = 1,
    parallel_license_limit: int | None = None,
    parallel_mode: str = "thread",
    progress_dir: str | Path | None = None,
    progress_completed_offset: int = 0,
    progress_total_cases: int | None = None,
    progress_component: str | None = None,
) -> tuple[DamperDOERecord, ...]:
    """Run a damper DOE design matrix through all requested load cases.

    When ``progress_dir`` is given, the completed-design count is written there
    as each design finishes, so an out-of-process reader can report "8/15".
    Returned records always follow the ``designs`` row order, regardless of
    completion order.
    """

    load_case_list = list(load_cases)
    if not load_case_list:
        raise ValueError("At least one load case is required for DOE batch analysis")
    design_matrix = np.asarray(designs, dtype=float)
    if design_matrix.ndim != 2:
        raise ValueError("designs must be a 2D array")
    if design_matrix.shape[1] != len(feature_names):
        raise ValueError("feature_names must match designs column count")
    effective_workers = _effective_parallel_workers(parallel_workers, parallel_license_limit)
    mode = _parallel_mode(parallel_mode)
    _validate_solver_parallelism(solver, effective_workers, mode)

    total = len(design_matrix)
    progress_total = total if progress_total_cases is None else int(progress_total_cases)
    progress_offset = int(progress_completed_offset)
    if progress_total < total + progress_offset:
        raise ValueError("progress_total_cases must include the completed offset and DOE cases")
    if progress_offset < 0:
        raise ValueError("progress_completed_offset cannot be negative")

    def report(completed: int) -> None:
        if progress_dir is not None:
            if progress_component:
                write_batch_component_progress(
                    progress_dir,
                    component=progress_component,
                    completed=progress_offset + completed,
                    total=progress_total,
                )
            else:
                write_batch_progress(
                    progress_dir,
                    completed=progress_offset + completed,
                    total=progress_total,
                )

    report(0)

    if effective_workers == 1 or total <= 1:
        records = []
        for index, design in enumerate(design_matrix, start=1):
            records.append(
                _run_one_design(
                    bridge_model,
                    load_case_list,
                    design,
                    output_dir=output_dir,
                    feature_names=feature_names,
                    solver=solver,
                    solver_kwargs=solver_kwargs,
                )
            )
            report(index)
        return tuple(records)

    executor_class = ProcessPoolExecutor if mode == "process" else ThreadPoolExecutor
    with executor_class(max_workers=effective_workers) as executor:
        future_to_index = {
            executor.submit(
                _run_one_design,
                bridge_model,
                load_case_list,
                design,
                output_dir=output_dir,
                feature_names=feature_names,
                solver=solver,
                solver_kwargs=solver_kwargs,
            ): index
            for index, design in enumerate(design_matrix)
        }
        # Report progress as designs finish, but keep results in designs order:
        # downstream surrogate fitting pairs records with the design matrix by
        # position.
        records_by_index: dict[int, DamperDOERecord] = {}
        completed = 0
        for future in as_completed(future_to_index):
            records_by_index[future_to_index[future]] = future.result()
            completed += 1
            report(completed)
        return tuple(records_by_index[index] for index in range(total))


def _run_one_design(
    bridge_model: BridgeModel,
    load_cases: list[LoadCase],
    design: np.ndarray,
    *,
    output_dir: str | Path,
    feature_names: tuple[str, ...],
    solver: str,
    solver_kwargs: dict | None,
) -> DamperDOERecord:
    params = damper_params_from_design(design, feature_names)
    design_parameters = {name: float(value) for name, value in zip(feature_names, design)}
    analyzer = BatchAnalyzer(
        solver=solver,
        output_dir=output_dir,
        solver_kwargs=_solver_kwargs_for_design(solver_kwargs, design_parameters),
    )
    results = tuple(analyzer.run_combinations(bridge_model, [params], load_cases))
    return DamperDOERecord(
        design=design.copy(),
        feature_names=feature_names,
        damper_params=params,
        results=results,
    )


def _solver_kwargs_for_design(base_kwargs: dict | None, design_parameters: dict[str, float]) -> dict:
    kwargs = dict(base_kwargs or {})
    if "physical_count_per_tower" in design_parameters:
        physical_count = physical_count_as_int(design_parameters["physical_count_per_tower"])
        kwargs["physical_count_per_tower"] = physical_count
        placements = kwargs.get("damper_placements")
        if placements is not None:
            kwargs["damper_placements"] = with_physical_count(tuple(placements), physical_count)
    return kwargs


def _effective_parallel_workers(parallel_workers: int, parallel_license_limit: int | None) -> int:
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


def _validate_solver_parallelism(solver: str, effective_workers: int, mode: str) -> None:
    if str(solver).lower() == "openseespy_inproc" and effective_workers > 1 and mode != "process":
        raise ValueError(
            "openseespy_inproc does not support thread parallel execution; "
            "use serial execution or process parallel execution"
        )
