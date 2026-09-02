"""Read compact solver summary files into unified analysis results."""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from typing import Any

from pyansys_bridge.models import AnalysisResult


SUMMARY_FILENAME = "summary.json"
TIMESERIES_FILENAME = "timeseries.csv"
STANDARD_OBJECTIVE_NAMES = (
    "max_displacement",
    "max_acceleration",
    "max_tower_base_moment",
    "max_tower_base_shear",
    "max_damper_force",
    "max_damper_stroke",
    "dissipated_energy",
)


def load_solver_summary(case_dir: str | Path) -> dict[str, Any] | None:
    """Load a compact solver summary from a case directory if one exists."""

    summary_path = Path(case_dir) / SUMMARY_FILENAME
    if not summary_path.exists():
        return None
    with summary_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("Solver summary must be a JSON object")
    return payload


def ensure_solver_summary(case_dir: str | Path) -> dict[str, Any] | None:
    """Load summary.json or derive it from a standard timeseries.csv."""

    loaded = load_solver_summary(case_dir)
    if loaded is not None:
        return loaded

    case_path = Path(case_dir)
    timeseries_path = case_path / TIMESERIES_FILENAME
    if not timeseries_path.exists():
        return None

    timeseries = load_timeseries_csv(timeseries_path)
    summary = {
        "status": "completed",
        "objectives": objectives_from_timeseries(timeseries),
        "timeseries": timeseries,
        "metadata": {"source_timeseries": str(timeseries_path)},
    }
    with (case_path / SUMMARY_FILENAME).open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2)
    return summary


def load_timeseries_csv(path: str | Path) -> dict[str, list[float]]:
    """Read a standard solver time-history CSV file."""

    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError("timeseries.csv must contain a header row")
        columns = {name: [] for name in reader.fieldnames}
        for row in reader:
            for name in columns:
                columns[name].append(float(row[name]))
    if "time" not in columns:
        raise ValueError("timeseries.csv must contain a 'time' column")
    return columns


def objectives_from_timeseries(timeseries: dict[str, list[float]]) -> dict[str, float]:
    """Compute standard objectives from time-history columns."""

    objectives = {}
    if "displacement" in timeseries or "displacement_increment" in timeseries:
        displacement_values = _objective_displacement_values(timeseries)
        max_displacement = max(abs(value) for value in displacement_values)
        objectives["max_displacement"] = max_displacement
        objectives["max_girder_end_displacement"] = max_displacement
        objectives["cumulative_displacement"] = _cumulative_displacement_objective(timeseries)
    if "acceleration" in timeseries:
        objectives["max_acceleration"] = max(abs(value) for value in timeseries["acceleration"])
    if "tower_base_moment" in timeseries:
        objectives["max_tower_base_moment"] = max(abs(value) for value in timeseries["tower_base_moment"])
    if "tower_base_shear" in timeseries:
        objectives["max_tower_base_shear"] = max(abs(value) for value in timeseries["tower_base_shear"])
    if "damper_force" in timeseries:
        objectives["max_damper_force"] = max(abs(value) for value in timeseries["damper_force"])
    if "damper_stroke" in timeseries:
        objectives["max_damper_stroke"] = max(abs(value) for value in timeseries["damper_stroke"])
    return objectives


def dissipated_energy_from_relative_response(path: str | Path) -> dict[str, Any]:
    """由求解器导出的逐阻尼器力-速度时程计算耗散能。"""
    source = Path(path)
    with source.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows or not rows[0]:
        raise ValueError("Damper relative response must contain data rows")
    force_columns = [
        name for name in rows[0]
        if name.endswith("_damper_force")
    ]
    if not force_columns:
        raise ValueError("Damper relative response must contain force columns")

    per_damper = {
        name.removesuffix("_damper_force"): 0.0
        for name in force_columns
    }
    previous_time: float | None = None
    for row in rows:
        time = float(row["time"])
        if not math.isfinite(time):
            raise ValueError("Damper response time must be finite")
        if previous_time is not None and time < previous_time:
            raise ValueError("Damper response time must be nondecreasing")
        interval = 0.0 if previous_time is None else time - previous_time
        for force_column in force_columns:
            damper = force_column.removesuffix("_damper_force")
            velocity_column = f"{damper}_rel_vel"
            if velocity_column not in row:
                raise ValueError(f"Missing relative velocity for damper {damper}")
            force = float(row[force_column])
            velocity = float(row[velocity_column])
            if not math.isfinite(force) or not math.isfinite(velocity):
                raise ValueError("Damper force and velocity must be finite")
            per_damper[damper] += abs(force * velocity) * interval
        previous_time = time
    return {
        "dissipated_energy": sum(per_damper.values()),
        "perDamper": per_damper,
        "source": source.name,
        "definition": "sum(abs(force * relative_velocity) * delta_time)",
    }


def _objective_displacement_values(timeseries: dict[str, list[float]]) -> list[float]:
    values = timeseries.get("displacement_increment")
    if values is not None:
        if "displacement" in timeseries and len(values) != len(timeseries["displacement"]):
            raise ValueError("displacement and displacement_increment samples must have the same length")
        return values
    return timeseries["displacement"]


def _path_length(values: list[float]) -> float:
    return sum(
        abs(float(values[index]) - float(values[index - 1]))
        for index in range(1, len(values))
    )


def _cumulative_displacement_objective(timeseries: dict[str, list[float]]) -> float:
    """累计位移按 displacement 列的路径长度累加。

    累计位移是路径依赖量，只对单个节点有定义，所以这一列必须已经是某个真实节点
    自身的时程。控制节点的挑选在求解器后处理里做（各候选节点先在自己的时程上求
    峰值，再比峰值取最大者），这里不重做挑选：若在此处另按跨节点最大行程选一次，
    会得到与 max_displacement 不同的节点，同一份 summary 里两个目标就不同源了。
    """
    time = timeseries["time"]
    if len(time) < 2:
        return 0.0
    for index in range(1, len(time)):
        if float(time[index]) - float(time[index - 1]) < 0:
            raise ValueError("time samples must be nondecreasing")
    values = _objective_displacement_values(timeseries)
    if len(time) != len(values):
        raise ValueError("time and displacement samples must have the same length")
    return _path_length(values)


def apply_solver_summary(result: AnalysisResult, summary: dict[str, Any]) -> AnalysisResult:
    """Merge solver-produced objectives, timeseries, and metadata into a result."""

    objectives = summary.get("objectives")
    if objectives is not None:
        if not isinstance(objectives, dict):
            raise ValueError("summary.objectives must be a JSON object")
        result.objectives = {key: float(value) for key, value in objectives.items()}

    timeseries = summary.get("timeseries")
    if timeseries is not None:
        if not isinstance(timeseries, dict):
            raise ValueError("summary.timeseries must be a JSON object")
        result.timeseries = {
            key: [float(item) for item in values]
            for key, values in timeseries.items()
        }

    metadata = summary.get("metadata")
    if metadata is not None:
        if not isinstance(metadata, dict):
            raise ValueError("summary.metadata must be a JSON object")
        result.metadata.update(metadata)

    status = summary.get("status")
    if status is not None:
        result.status = str(status)
    return result
