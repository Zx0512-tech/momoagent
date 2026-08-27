"""Objective helpers."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any


DEFAULT_OBJECTIVES = (
    "max_displacement",
    "max_girder_end_displacement",
    "max_acceleration",
    "cumulative_displacement",
    "max_tower_base_moment",
    "max_tower_base_shear",
    "max_damper_stroke",
    "max_damper_force",
)

ObjectiveFunction = Callable[[Mapping[str, Any]], float]
OBJECTIVE_REGISTRY: dict[str, ObjectiveFunction] = {}


def objective_vector(objectives: dict[str, float], names: tuple[str, ...] = DEFAULT_OBJECTIVES) -> list[float]:
    return [float(objectives[name]) for name in names]


def register_objective(name: str, func: ObjectiveFunction, *, overwrite: bool = False) -> None:
    """Register a scalar minimization objective extractor."""

    if not name:
        raise ValueError("Objective name cannot be empty")
    if name in OBJECTIVE_REGISTRY and not overwrite:
        raise ValueError(f"Objective already registered: {name}")
    OBJECTIVE_REGISTRY[name] = func


def objective_value(source: Mapping[str, Any], name: str) -> float:
    """Extract one scalar objective from a result/objective mapping."""

    objectives = _objective_mapping(source)
    if name in objectives:
        return float(objectives[name])
    if name not in OBJECTIVE_REGISTRY:
        raise KeyError(f"Unknown objective: {name}")
    return float(OBJECTIVE_REGISTRY[name](source))


def cumulative_absolute_response(time: list[float], values: list[float]) -> float:
    """Return the total travel distance of one response coordinate."""

    if len(time) != len(values):
        raise ValueError("time and values must contain the same number of samples")
    if len(time) < 2:
        return 0.0

    for index in range(1, len(time)):
        dt = float(time[index]) - float(time[index - 1])
        if dt < 0:
            raise ValueError("time samples must be nondecreasing")
    return sum(
        abs(float(values[index]) - float(values[index - 1]))
        for index in range(1, len(values))
    )


def cumulative_displacement_objective(timeseries: dict[str, list[float]]) -> float:
    """Operation-state objective based on cumulative displacement."""

    return cumulative_absolute_response(timeseries["time"], timeseries["displacement"])


def _objective_mapping(source: Mapping[str, Any]) -> Mapping[str, Any]:
    nested = source.get("objectives")
    if isinstance(nested, Mapping):
        return nested
    return source


def _extract_first(*names: str) -> ObjectiveFunction:
    def extractor(source: Mapping[str, Any]) -> float:
        objectives = _objective_mapping(source)
        for name in names:
            if name in objectives:
                return float(objectives[name])
        raise KeyError(f"Missing objective value; tried: {', '.join(names)}")

    return extractor


def _extract_max_abs(*names: str) -> ObjectiveFunction:
    def extractor(source: Mapping[str, Any]) -> float:
        objectives = _objective_mapping(source)
        values = [float(objectives[name]) for name in names if name in objectives]
        if not values:
            raise KeyError(f"Missing objective value; tried: {', '.join(names)}")
        return max(abs(value) for value in values)

    return extractor


def _register_default_objectives() -> None:
    defaults: dict[str, ObjectiveFunction] = {
        "max_displacement": _extract_first("max_displacement"),
        "main_girder_max_displacement": _extract_first(
            "main_girder_max_displacement",
            "max_girder_end_displacement",
            "max_displacement",
        ),
        "max_girder_end_displacement": _extract_first("max_girder_end_displacement", "max_displacement"),
        "max_acceleration": _extract_first("max_acceleration"),
        "cumulative_displacement": _extract_first("cumulative_displacement"),
        "tower_base_internal_force": _extract_max_abs(
            "tower_base_internal_force",
            "max_tower_base_internal_force",
            "max_tower_base_moment",
            "max_tower_base_shear",
        ),
        "max_tower_base_moment": _extract_first("max_tower_base_moment"),
        "max_tower_base_shear": _extract_first("max_tower_base_shear"),
        "damper_stroke": _extract_first("damper_stroke", "max_damper_stroke"),
        "max_damper_stroke": _extract_first("max_damper_stroke"),
        "damper_force": _extract_first("damper_force", "max_damper_force"),
        "max_damper_force": _extract_first("max_damper_force"),
        "energy_dissipation": _extract_first("energy_dissipation", "dissipated_energy"),
        "cost": _extract_first("cost", "damper_cost"),
    }
    for name, func in defaults.items():
        register_objective(name, func, overwrite=True)


_register_default_objectives()
