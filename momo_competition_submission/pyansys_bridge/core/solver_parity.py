"""Compare ANSYS and OpenSeesPy solver summary outputs."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from pyansys_bridge.core.result_summary import SUMMARY_FILENAME, STANDARD_OBJECTIVE_NAMES, load_solver_summary


@dataclass(frozen=True)
class ParityTolerance:
    relative: float = 0.10
    absolute: float = 0.0

    def __post_init__(self) -> None:
        if self.relative < 0:
            raise ValueError("relative tolerance cannot be negative")
        if self.absolute < 0:
            raise ValueError("absolute tolerance cannot be negative")


@dataclass(frozen=True)
class MetricParity:
    metric: str
    reference: float | None
    candidate: float | None
    absolute_error: float | None
    relative_error: float | None
    tolerance: ParityTolerance
    status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "reference": self.reference,
            "candidate": self.candidate,
            "absolute_error": self.absolute_error,
            "relative_error": self.relative_error,
            "relative_tolerance": self.tolerance.relative,
            "absolute_tolerance": self.tolerance.absolute,
            "status": self.status,
        }


def compare_solver_summary_files(
    reference_path: str | Path,
    candidate_path: str | Path,
    *,
    metrics: Sequence[str] = STANDARD_OBJECTIVE_NAMES,
    default_tolerance: ParityTolerance | None = None,
    metric_tolerances: Mapping[str, ParityTolerance] | None = None,
) -> dict[str, Any]:
    reference = _required_summary(reference_path)
    candidate = _required_summary(candidate_path)
    return compare_solver_summaries(
        reference,
        candidate,
        metrics=metrics,
        default_tolerance=default_tolerance,
        metric_tolerances=metric_tolerances,
        reference_path=Path(reference_path),
        candidate_path=Path(candidate_path),
    )


def compare_solver_summaries(
    reference_summary: Mapping[str, Any],
    candidate_summary: Mapping[str, Any],
    *,
    metrics: Sequence[str] = STANDARD_OBJECTIVE_NAMES,
    default_tolerance: ParityTolerance | None = None,
    metric_tolerances: Mapping[str, ParityTolerance] | None = None,
    reference_path: Path | None = None,
    candidate_path: Path | None = None,
) -> dict[str, Any]:
    tolerance = default_tolerance or ParityTolerance()
    per_metric_tolerances = dict(metric_tolerances or {})
    reference_objectives = _objective_mapping(reference_summary, "reference")
    candidate_objectives = _objective_mapping(candidate_summary, "candidate")
    comparisons = [
        _compare_metric(
            metric,
            reference_objectives,
            candidate_objectives,
            per_metric_tolerances.get(metric, tolerance),
        )
        for metric in metrics
    ]
    status = "pass" if all(item.status == "pass" for item in comparisons) else "fail"
    return {
        "status": status,
        "metrics": [item.to_dict() for item in comparisons],
        "reference": _summary_metadata(reference_summary, reference_path),
        "candidate": _summary_metadata(candidate_summary, candidate_path),
    }


def write_parity_report(report: Mapping[str, Any], path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    return target


def _compare_metric(
    metric: str,
    reference_objectives: Mapping[str, Any],
    candidate_objectives: Mapping[str, Any],
    tolerance: ParityTolerance,
) -> MetricParity:
    if metric not in reference_objectives or metric not in candidate_objectives:
        return MetricParity(
            metric=metric,
            reference=_optional_float(reference_objectives.get(metric)),
            candidate=_optional_float(candidate_objectives.get(metric)),
            absolute_error=None,
            relative_error=None,
            tolerance=tolerance,
            status="missing",
        )
    reference = float(reference_objectives[metric])
    candidate = float(candidate_objectives[metric])
    absolute_error = abs(candidate - reference)
    denominator = abs(reference)
    relative_error = None if denominator == 0.0 else absolute_error / denominator
    if denominator == 0.0 and absolute_error == 0.0:
        relative_error = 0.0
    passed = absolute_error <= tolerance.absolute or (
        relative_error is not None and relative_error <= tolerance.relative
    )
    return MetricParity(
        metric=metric,
        reference=reference,
        candidate=candidate,
        absolute_error=absolute_error,
        relative_error=relative_error,
        tolerance=tolerance,
        status="pass" if passed else "fail",
    )


def _required_summary(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    if source.is_file():
        with source.open("r", encoding="utf-8") as handle:
            summary = json.load(handle)
    else:
        summary = load_solver_summary(source)
    if summary is None:
        raise FileNotFoundError(f"{SUMMARY_FILENAME} not found: {source / SUMMARY_FILENAME}")
    if not isinstance(summary, dict):
        raise ValueError(f"solver summary must be a JSON object: {source}")
    return summary


def _objective_mapping(summary: Mapping[str, Any], label: str) -> Mapping[str, Any]:
    objectives = summary.get("objectives")
    if not isinstance(objectives, Mapping):
        raise ValueError(f"{label} summary must contain an objectives object")
    return objectives


def _summary_metadata(summary: Mapping[str, Any], path: Path | None) -> dict[str, Any]:
    metadata: dict[str, Any] = {"status": summary.get("status")}
    if path is not None:
        metadata["path"] = str(path)
    solver = summary.get("solver")
    if solver is not None:
        metadata["solver"] = solver
    return metadata


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)
