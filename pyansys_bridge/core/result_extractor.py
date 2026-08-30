"""Run-mode solver result extraction helpers."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path

from pyansys_bridge.core.result_summary import apply_solver_summary, ensure_solver_summary
from pyansys_bridge.models import AnalysisResult


def apply_run_mode_solver_outputs(result: AnalysisResult, case_dir: Path, solver_name: str) -> None:
    summary = ensure_solver_summary(case_dir)
    if summary is None:
        result.objectives = {}
        result.timeseries = {}
        result.metadata["error"] = (
            f"{solver_name} run completed without summary.json or timeseries.csv solver output"
        )
        result.finish("failed")
        return

    result.objectives = {}
    result.timeseries = {}
    apply_solver_summary(result, summary)
    summary_objectives = summary.get("objectives")
    result.metadata.setdefault("solver_summary", {}).update(
        {
            "path": str(case_dir / "summary.json"),
            "sha256": _file_sha256(case_dir / "summary.json"),
            "objective_names": (
                sorted(summary_objectives)
                if isinstance(summary_objectives, dict)
                else []
            ),
        }
    )
    if result.status != "completed":
        result.objectives = {}
        result.timeseries = {}
        result.metadata["error"] = f"{solver_name} solver summary status {result.status}"
        result.finish("failed")
    elif not result.objectives:
        result.metadata["error"] = f"{solver_name} solver summary did not contain solver objectives"
        result.finish("failed")
    elif not _has_finite_objective_values(result.objectives):
        result.objectives = {}
        result.timeseries = {}
        result.metadata["error"] = f"{solver_name} solver summary contained non-finite solver objectives"
        result.finish("failed")
    elif not _has_finite_timeseries_values(result.timeseries):
        result.objectives = {}
        result.timeseries = {}
        result.metadata["error"] = f"{solver_name} solver summary contained non-finite solver timeseries"
        result.finish("failed")


def _has_finite_objective_values(objectives: dict[str, float]) -> bool:
    import math

    return all(math.isfinite(float(value)) for value in objectives.values())


def _has_finite_timeseries_values(timeseries: dict[str, list[float]]) -> bool:
    import math

    return all(
        math.isfinite(float(value))
        for values in timeseries.values()
        for value in values
    )


def _file_sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()
