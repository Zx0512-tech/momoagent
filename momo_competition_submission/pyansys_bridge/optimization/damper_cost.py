"""Viscous damper capacity and approximate cost metrics."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


LEFT_DAMPER_PREFIX = "north_tower_girder"
RIGHT_DAMPER_PREFIX = "south_tower_girder"


def compute_damper_capacity_metrics(
    *,
    relative_rows: Iterable[Mapping[str, Any]],
    damper_names: Iterable[str],
    c_by_damper: Mapping[str, float],
    alpha: float,
) -> dict[str, dict[str, float]]:
    """Compute Fmax, Smax, and dissipated energy for each damper."""

    rows = list(relative_rows)
    metrics = {
        name: {"Fmax": 0.0, "Smax": 0.0, "E": 0.0}
        for name in damper_names
    }
    previous_time: float | None = None
    for row in rows:
        time = float(row["time"])
        dt = 0.0 if previous_time is None else max(time - previous_time, 0.0)
        for name in metrics:
            c_value = float(c_by_damper[name])
            rel_disp = float(row[f"{name}_rel_disp_x_m"])
            rel_vel = float(row[f"{name}_rel_vel_x_m_per_s"])
            abs_velocity = abs(rel_vel)
            metrics[name]["Fmax"] = max(metrics[name]["Fmax"], c_value * (abs_velocity ** alpha))
            metrics[name]["Smax"] = max(metrics[name]["Smax"], abs(rel_disp))
            metrics[name]["E"] += c_value * (abs_velocity ** (alpha + 1.0)) * dt
        previous_time = time
    return metrics


def damper_cost_objective_fields(metrics: Mapping[str, Mapping[str, float]]) -> dict[str, float]:
    """Flatten left/right damper raw capacity metrics for objectives/output."""

    left = _aggregate_side_metrics(metrics, LEFT_DAMPER_PREFIX)
    right = _aggregate_side_metrics(metrics, RIGHT_DAMPER_PREFIX)
    return {
        "Fmax_L": float(left["Fmax"]),
        "Fmax_R": float(right["Fmax"]),
        "Smax_L": float(left["Smax"]),
        "Smax_R": float(right["Smax"]),
        "E_L": float(left["E"]),
        "E_R": float(right["E"]),
    }


def _aggregate_side_metrics(
    metrics: Mapping[str, Mapping[str, float]],
    prefix: str,
) -> dict[str, float]:
    if prefix in metrics:
        return {
            "Fmax": float(metrics[prefix]["Fmax"]),
            "Smax": float(metrics[prefix]["Smax"]),
            "E": float(metrics[prefix]["E"]),
        }
    side_metrics = [
        values
        for name, values in metrics.items()
        if name.startswith(f"{prefix}_")
    ]
    if not side_metrics:
        raise KeyError(prefix)
    return {
        "Fmax": max(float(values["Fmax"]) for values in side_metrics),
        "Smax": max(float(values["Smax"]) for values in side_metrics),
        "E": sum(float(values["E"]) for values in side_metrics),
    }


def normalize_damper_costs(candidates: Iterable[Mapping[str, Any]]) -> list[dict[str, float]]:
    """Add Cost_L, Cost_R, and Cost_total using candidate-wide F/S/E maxima."""

    rows = [dict(candidate) for candidate in candidates]
    if not rows:
        return []
    f0 = _max_positive(rows, ("Fmax_L", "Fmax_R"))
    s0 = _max_positive(rows, ("Smax_L", "Smax_R"))
    e0 = _max_positive(rows, ("E_L", "E_R"))
    normalized = []
    for row in rows:
        cost_l = _single_damper_cost(row["Fmax_L"], row["Smax_L"], row["E_L"], f0, s0, e0)
        cost_r = _single_damper_cost(row["Fmax_R"], row["Smax_R"], row["E_R"], f0, s0, e0)
        normalized.append(
            {
                **row,
                "F0": f0,
                "S0": s0,
                "E0": e0,
                "Cost_L": cost_l,
                "Cost_R": cost_r,
                "Cost_total": cost_l + cost_r,
            }
        )
    return normalized


def _single_damper_cost(fmax: float, smax: float, energy: float, f0: float, s0: float, e0: float) -> float:
    return (
        0.60 * float(fmax) / f0
        + 0.30 * float(smax) / s0
        + 0.10 * float(energy) / e0
    )


def _max_positive(rows: list[dict[str, Any]], fields: tuple[str, ...]) -> float:
    value = max(float(row[field]) for row in rows for field in fields)
    return value if value > 0.0 else 1.0
