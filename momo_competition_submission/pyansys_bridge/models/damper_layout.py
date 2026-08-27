"""Damper placement and realizable unit split contracts."""

from __future__ import annotations

from dataclasses import dataclass

from .damper_params import DamperParams


@dataclass(frozen=True)
class DamperPlacement:
    """Fixed tower-girder installation position for one optimized damper group."""

    name: str
    node_i: int
    node_j: int
    direction: str = "X"
    physical_count: int = 1

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("DamperPlacement.name is required")
        if self.node_i <= 0 or self.node_j <= 0:
            raise ValueError("DamperPlacement nodes must be positive")
        object.__setattr__(
            self,
            "physical_count",
            physical_count_as_int(self.physical_count, "DamperPlacement.physical_count"),
        )


@dataclass(frozen=True)
class RealizableDamper:
    """One physical damper obtained by splitting an optimized group parameter."""

    placement_name: str
    unit_index: int
    node_i: int
    node_j: int
    direction: str
    params: DamperParams

    def to_dict(self) -> dict[str, object]:
        return {
            "placement_name": self.placement_name,
            "unit_index": self.unit_index,
            "node_i": self.node_i,
            "node_j": self.node_j,
            "direction": self.direction,
            "params": self.params.to_dict(),
        }


def tower_girder_layout(physical_count_per_tower: int = 1) -> tuple[DamperPlacement, ...]:
    """Default fixed STbridge tower-girder damper positions."""

    physical_count = physical_count_as_int(physical_count_per_tower)
    if physical_count == 1:
        return (
            DamperPlacement("north_tower_girder_1", 36, 518, physical_count=physical_count),
            DamperPlacement("south_tower_girder_1", 107, 521, physical_count=physical_count),
        )
    if physical_count == 2:
        return (
            DamperPlacement("north_tower_girder_1", 36, 517, physical_count=physical_count),
            DamperPlacement("north_tower_girder_2", 36, 518, physical_count=physical_count),
            DamperPlacement("south_tower_girder_1", 107, 520, physical_count=physical_count),
            DamperPlacement("south_tower_girder_2", 107, 521, physical_count=physical_count),
        )
    raise ValueError("tower_girder_layout currently supports physical_count_per_tower 1 or 2")


def common_physical_count(placements: tuple[DamperPlacement, ...]) -> int:
    """Return the common parallel-unit count required by one layout design."""

    counts = {placement.physical_count for placement in placements}
    if len(counts) != 1:
        raise ValueError("damper_placements must use one common physical_count")
    return counts.pop()


def with_physical_count(
    placements: tuple[DamperPlacement, ...],
    physical_count: int,
) -> tuple[DamperPlacement, ...]:
    """Preserve fixed connection locations while applying a design count."""

    count = physical_count_as_int(physical_count)
    return tuple(
        DamperPlacement(
            placement.name,
            placement.node_i,
            placement.node_j,
            direction=placement.direction,
            physical_count=count,
        )
        for placement in placements
    )


def physical_count_as_int(value: object, field_name: str = "physical_count_per_tower") -> int:
    """Return a positive integer physical damper count without silent rounding."""

    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a positive integer") from exc
    if not numeric.is_integer():
        raise ValueError(f"{field_name} must be a positive integer")
    count = int(numeric)
    if count <= 0:
        raise ValueError(f"{field_name} must be a positive integer")
    return count


def split_total_damper_params(
    total_params: DamperParams,
    placements: tuple[DamperPlacement, ...],
) -> tuple[RealizableDamper, ...]:
    """Split each optimized placement total into parallel physical dampers.

    For parallel dampers at the same tower-girder position, the equivalent
    coefficient and stiffness are additive. The velocity exponent stays equal
    to the optimized value.
    """

    realized: list[RealizableDamper] = []
    for placement in placements:
        unit_count = 1 if _is_explicit_stbridge_tower_girder_placement(placement.name) else placement.physical_count
        unit_stiffness = None if total_params.stiffness is None else total_params.stiffness / unit_count
        unit_params = DamperParams(
            c=total_params.c / unit_count,
            alpha=total_params.alpha,
            stiffness=unit_stiffness,
            regularization_velocity=total_params.regularization_velocity,
        )
        for index in range(unit_count):
            realized.append(
                RealizableDamper(
                    placement_name=placement.name,
                    unit_index=index + 1,
                    node_i=placement.node_i,
                    node_j=placement.node_j,
                    direction=placement.direction,
                    params=unit_params,
                )
            )
    return tuple(realized)


def _is_explicit_stbridge_tower_girder_placement(name: str) -> bool:
    return name in {
        "north_tower_girder_1",
        "north_tower_girder_2",
        "south_tower_girder_1",
        "south_tower_girder_2",
    }
