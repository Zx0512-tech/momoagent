"""Realizable damper plan derived from optimization results."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from pyansys_bridge.models import (
    DamperParams,
    DamperPlacement,
    RealizableDamper,
    common_physical_count,
    physical_count_as_int,
    split_total_damper_params,
    tower_girder_layout,
    with_physical_count,
)

from .workflow import SurrogateOptimizationResult


@dataclass(frozen=True)
class OptimizedDamperPlan:
    """Engineering damper layout produced from one optimized total design."""

    parameter_names: tuple[str, ...]
    design: np.ndarray
    total_params: DamperParams
    physical_count_per_tower: int
    realizable_dampers: tuple[RealizableDamper, ...]

    def design_parameters(self) -> dict[str, float]:
        return {name: float(value) for name, value in zip(self.parameter_names, self.design)}

    def to_dict(self) -> dict[str, object]:
        return {
            "design_parameters": self.design_parameters(),
            "total_params": self.total_params.to_dict(),
            "physical_count_per_tower": self.physical_count_per_tower,
            "realizable_dampers": [damper.to_dict() for damper in self.realizable_dampers],
        }


def realize_optimized_damper_plan(
    result: SurrogateOptimizationResult,
    placements: tuple[DamperPlacement, ...] | None = None,
) -> OptimizedDamperPlan:
    """Split the selected total damper design into fixed tower-girder units."""

    design = np.asarray(result.best_design, dtype=float)
    if design.ndim != 1:
        raise ValueError("best_design must be a 1D array")
    if design.shape[0] != len(result.parameter_names):
        raise ValueError("parameter_names must match best_design length")
    values = {name: float(value) for name, value in zip(result.parameter_names, design)}
    if "c" not in values or "alpha" not in values:
        raise ValueError("Optimized damper plan requires c and alpha design parameters")

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
        parameter_names=result.parameter_names,
        design=design.copy(),
        total_params=total_params,
        physical_count_per_tower=physical_count,
        realizable_dampers=split_total_damper_params(total_params, selected_placements),
    )
