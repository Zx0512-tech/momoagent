"""Shared lightweight helpers for OpenSees command-stream backends."""

from __future__ import annotations

from pyansys_bridge.core.ansys_damper import DEFAULT_DAMPER_REGULARIZATION_VELOCITY
from pyansys_bridge.models import BridgeModel, DamperPlacement, RealizableDamper


OPENSEES_DAMPER_C_SCALE = 1000.0
OPENSEES_DAMPER_IMPLEMENTATION_CONTRACT = "global_axis_user300_v3"

STBRIDGE_SUPPORT_NODES = (
    494,
    495,
    511,
    512,
    525,
    528,
    530,
    532,
    534,
    536,
    538,
    540,
    542,
    544,
    547,
    550,
)

STBRIDGE_TOWER_BASE_ELEMENT_NODE_MAP = (
    (835, 494),
    (836, 495),
    (837, 511),
    (838, 512),
)

STBRIDGE_TOWER_BASE_SHEAR_INERTIA_ELEMENT_MASS_DENSITIES = (
    (831, 196250.0),
    (832, 196250.0),
    (833, 196250.0),
    (834, 196250.0),
    (835, 273750.0),
    (836, 273750.0),
    (837, 273750.0),
    (838, 273750.0),
)


def opensees_direction(direction: str) -> int:
    mapping = {"X": 1, "Y": 2, "Z": 3, "ROTX": 4, "ROTY": 5, "ROTZ": 6}
    return mapping.get(direction.upper(), 1)


def optional_node_tuple(nodes) -> tuple[int, ...] | None:
    if nodes is None:
        return None
    return tuple(int(node) for node in nodes)


def dof_tuple(dofs, name: str) -> tuple[int, ...]:
    result = tuple(int(dof) for dof in dofs)
    if not result:
        raise ValueError(f"{name} must contain at least one OpenSees DOF")
    if any(dof < 1 or dof > 6 for dof in result):
        raise ValueError(f"{name} values must be in the OpenSees range 1..6")
    return result


def node_tuple(nodes, name: str) -> tuple[int, ...]:
    result = tuple(int(node) for node in nodes)
    if any(node <= 0 for node in result):
        raise ValueError(f"{name} values must be positive OpenSees node tags")
    return result


def element_node_map_tuple(pairs, name: str) -> tuple[tuple[int, int], ...]:
    result = []
    for pair in pairs:
        values = tuple(int(value) for value in pair)
        if len(values) != 2:
            raise ValueError(f"{name} entries must be (element_id, node_id)")
        element_id, node_id = values
        if element_id <= 0 or node_id <= 0:
            raise ValueError(f"{name} element and node tags must be positive")
        result.append((element_id, node_id))
    return tuple(result)


def element_mass_density_tuple(pairs, name: str) -> tuple[tuple[int, float], ...]:
    result = []
    for pair in pairs:
        values = tuple(pair)
        if len(values) != 2:
            raise ValueError(f"{name} entries must be (element_id, mass_density)")
        element_id, mass_density = int(values[0]), float(values[1])
        if element_id <= 0:
            raise ValueError(f"{name} element tags must be positive")
        if mass_density < 0.0:
            raise ValueError(f"{name} mass densities cannot be negative")
        result.append((element_id, mass_density))
    return tuple(result)


def opensees_system_config(system: str, args=()) -> tuple[str, tuple[str, ...]]:
    name = str(system).strip()
    if not name:
        raise ValueError("opensees_system must be a non-empty OpenSees system name")
    if isinstance(args, str):
        return name, (args,)
    return name, tuple(str(value) for value in args)


def placement_pair_tuple(pairs, name: str) -> tuple[tuple[int, int, int], ...]:
    result = []
    for pair in pairs:
        values = tuple(int(value) for value in pair)
        if len(values) == 2:
            node_i, node_j = values
            dof = 1
        elif len(values) == 3:
            node_i, node_j, dof = values
        else:
            raise ValueError(f"{name} entries must be (node_i, node_j) or (node_i, node_j, dof)")
        if node_i <= 0 or node_j <= 0:
            raise ValueError(f"{name} node tags must be positive")
        if dof < 1 or dof > 6:
            raise ValueError(f"{name} dof values must be in the OpenSees range 1..6")
        result.append((node_i, node_j, dof))
    return tuple(result)


def uniform_excitation_axis(direction: dict[str, float] | None) -> tuple[int, float]:
    values = {str(key).lower(): float(value) for key, value in dict(direction or {}).items()}
    if not values:
        return 1, 1.0
    components = [
        (1, values.get("x", 0.0)),
        (2, values.get("y", 0.0)),
        (3, values.get("z", 0.0)),
    ]
    active = [(dof, value) for dof, value in components if value != 0.0]
    if len(active) != 1:
        raise ValueError("OpenSees UniformExcitation earthquake direction must contain exactly one nonzero axis")
    return active[0]


def operation_load_nodes_from_model(model: BridgeModel | None) -> dict[str, tuple[int, ...] | None]:
    keys = ("deck_nodes", "wind_load_nodes", "traffic_load_nodes")
    metadata = {} if model is None else model.metadata
    values: dict[str, object] = {key: optional_node_tuple(metadata.get(key)) for key in keys}
    for load_kind in ("wind", "traffic"):
        mappings = metadata.get(f"{load_kind}_load_mappings") or metadata.get(f"{load_kind}_load_point_mappings")
        if mappings:
            values[f"{load_kind}_load_mappings"] = tuple(dict(item) for item in mappings)
    return values


def placement_dof_pairs(placements: tuple[DamperPlacement, ...]) -> tuple[tuple[int, int, int], ...]:
    return tuple(
        (placement.node_i, placement.node_j, opensees_direction(placement.direction))
        for placement in placements
    )


def opensees_damper_block(
    damper_module: str,
    damper: RealizableDamper,
    mat_tag: int,
    ele_tag: int,
    direction: int,
    c_scale: float = OPENSEES_DAMPER_C_SCALE,
) -> list[str]:
    exported_c = float(damper.params.c) * float(c_scale)
    header = (
        f"# {damper.placement_name} unit {damper.unit_index}: "
        f"selected={damper_module}; input_C={damper.params.c}; "
        f"opensees_C={exported_c}; alpha={damper.params.alpha}; dir={direction}"
    )
    if damper_module == "damper_viscous":
        vfloor = damper.params.regularization_velocity or DEFAULT_DAMPER_REGULARIZATION_VELOCITY
        material_command = (
            "# USER300 等价黏滞本构：F = C*max(abs(v),VFLOOR)^(alpha-1)*v\n"
            f"ops.uniaxialMaterial('User300Viscous', {mat_tag}, "
            f"{exported_c}, {damper.params.alpha}, {vfloor})"
        )
    elif damper_module == "damper_friction":
        fc = damper.params.c
        vs = damper.params.alpha
        material_command = (
            "# USER300 等价摩擦本构：F = FC*tanh(v/VS)\n"
            f"ops.uniaxialMaterial('User300Friction', {mat_tag}, {fc}, {vs})"
        )
    elif damper_module == "damper_eddy_current":
        fmax = damper.params.c
        vcr = damper.params.alpha
        material_command = (
            "# USER300 等价电涡流本构：F = 2*FMAX*VCR*v/(v*v+VCR*VCR)\n"
            f"ops.uniaxialMaterial('User300EddyCurrent', {mat_tag}, {fmax}, {vcr})"
        )
    else:
        raise NotImplementedError(f"Unsupported OpenSees damper module: {damper_module}")
    return [
        header,
        material_command,
        (
            f"ops.element('twoNodeLink', {ele_tag}, {damper.node_i}, {damper.node_j}, "
            f"'-mat', {mat_tag}, '-dir', {direction}, "
            "'-orient', *_global_axis_orient())"
        ),
        f"damper_elements.append({ele_tag})",
    ]
