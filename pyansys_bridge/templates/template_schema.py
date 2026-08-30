"""Template variable schemas for command stream modules."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any


_LEGACY_PLACEHOLDER = re.compile(r"(?<!{){[A-Za-z_][A-Za-z0-9_]*}(?!})")
_JINJA_PLACEHOLDER = re.compile(r"{{\s*[^}]+\s*}}")


class TemplateContextError(ValueError):
    """Raised when a command template context does not satisfy its schema."""


@dataclass(frozen=True)
class TemplateSchema:
    """Required and optional variables for one command template."""

    solver: str
    name: str
    required_variables: frozenset[str]
    optional_variables: frozenset[str] = frozenset()

    def validate(self, context: dict[str, Any]) -> None:
        missing = sorted(variable for variable in self.required_variables if variable not in context)
        if missing:
            raise TemplateContextError(
                f"Missing required template variable(s) for {self.solver}/{self.name}: {', '.join(missing)}"
            )


def get_template_schema(solver: str, name: str) -> TemplateSchema:
    key = (_normalize(solver), name)
    try:
        return TEMPLATE_SCHEMAS[key]
    except KeyError as exc:
        raise KeyError(f"No template schema registered for {solver}/{name}") from exc


def schema_for_module(module: Any) -> TemplateSchema | None:
    if not getattr(module, "metadata", {}).get("source_path"):
        return None
    return TEMPLATE_SCHEMAS.get((_normalize(module.solver), module.name))


def validate_template_context(module: Any, context: dict[str, Any] | None) -> None:
    schema = schema_for_module(module)
    if schema is None:
        return
    schema.validate(dict(context or {}))


def validate_rendered_template(
    rendered: str,
    *,
    template_name: str,
    check_legacy_placeholders: bool = True,
) -> None:
    unresolved = _JINJA_PLACEHOLDER.findall(rendered)
    if check_legacy_placeholders:
        unresolved += _LEGACY_PLACEHOLDER.findall(rendered)
    if unresolved:
        preview = ", ".join(sorted(set(unresolved))[:5])
        raise TemplateContextError(f"{template_name} rendered with unresolved template variables: {preview}")


def _schema(
    solver: str,
    name: str,
    required: tuple[str, ...],
    optional: tuple[str, ...] = (),
) -> TemplateSchema:
    return TemplateSchema(
        solver=solver,
        name=name,
        required_variables=frozenset(required),
        optional_variables=frozenset(optional),
    )


def _normalize(value: str) -> str:
    return str(value).lower()


TEMPLATE_SCHEMAS: dict[tuple[str, str], TemplateSchema] = {
    ("ansys", "model_apdl"): _schema("ansys", "model_apdl", ("model_path",)),
    ("ansys", "damper_viscous"): _schema("ansys", "damper_viscous", ("damper_type", "damper_commands")),
    ("ansys", "damper_user300_viscous"): _schema(
        "ansys", "damper_user300_viscous", ("damper_type", "damper_commands")
    ),
    ("ansys", "damper_friction"): _schema("ansys", "damper_friction", ("damper_type", "damper_commands")),
    ("ansys", "damper_eddy_current"): _schema("ansys", "damper_eddy_current", ("damper_type", "damper_commands")),
    (
        "ansys",
        "gravity_apdl",
    ): _schema(
        "ansys",
        "gravity_apdl",
        ("gravity_accel_x", "gravity_accel_y", "gravity_accel_z", "gravity_time", "gravity_substeps"),
    ),
    ("ansys", "modal_apdl"): _schema("ansys", "modal_apdl", ("modal_modes",)),
    (
        "ansys",
        "earthquake_apdl",
    ): _schema(
        "ansys",
        "earthquake_apdl",
        ("command_role", "ansys_load_render_role", "load_tables", "load_application_commands"),
    ),
    (
        "ansys",
        "wind_apdl",
    ): _schema(
        "ansys",
        "wind_apdl",
        ("command_role", "ansys_load_render_role", "load_tables", "load_application_commands"),
    ),
    (
        "ansys",
        "traffic_apdl",
    ): _schema(
        "ansys",
        "traffic_apdl",
        ("command_role", "ansys_load_render_role", "load_tables", "load_application_commands"),
    ),
    (
        "ansys",
        "postprocess_apdl",
    ): _schema("ansys", "postprocess_apdl", ("ansys_solver_execution_commands",)),
    ("opensees", "model_openseespy"): _schema("opensees", "model_openseespy", ("model_path", "opensees_load_nodes")),
    ("opensees", "damper_viscous"): _schema("opensees", "damper_viscous", ("damper_type", "damper_commands")),
    ("opensees", "damper_friction"): _schema("opensees", "damper_friction", ("damper_type", "damper_commands")),
    (
        "opensees",
        "damper_eddy_current",
    ): _schema("opensees", "damper_eddy_current", ("damper_type", "damper_commands")),
    (
        "opensees",
        "gravity_openseespy",
    ): _schema(
        "opensees",
        "gravity_openseespy",
        (
            "gravity_accel_x",
            "gravity_accel_y",
            "gravity_accel_z",
            "gravity_substeps",
            "opensees_system",
            "opensees_system_args",
        ),
    ),
    ("opensees", "modal_openseespy"): _schema("opensees", "modal_openseespy", ("modal_modes",)),
    (
        "opensees",
        "earthquake_openseespy",
    ): _schema(
        "opensees",
        "earthquake_openseespy",
        (
            "earthquake_name",
            "earthquake_path",
            "earthquake_scale",
            "earthquake_dt",
            "earthquake_duration",
            "earthquake_uniform_dof",
            "earthquake_uniform_factor",
        ),
    ),
    (
        "opensees",
        "wind_openseespy",
    ): _schema(
        "opensees",
        "wind_openseespy",
        (
            "wind_name",
            "wind_path",
            "wind_scale",
            "wind_dt",
            "wind_duration",
            "wind_direction_x",
            "wind_direction_y",
            "wind_direction_z",
        ),
    ),
    (
        "opensees",
        "traffic_openseespy",
    ): _schema(
        "opensees",
        "traffic_openseespy",
        (
            "traffic_name",
            "traffic_path",
            "traffic_scale",
            "traffic_dt",
            "traffic_duration",
            "traffic_direction_x",
            "traffic_direction_y",
            "traffic_direction_z",
        ),
    ),
    (
        "opensees",
        "postprocess_openseespy",
    ): _schema(
        "opensees",
        "postprocess_openseespy",
        (
            "load_dt",
            "load_duration",
            "response_nodes",
            "response_dof",
            "tower_base_nodes",
            "tower_base_element_node_map",
            "tower_base_shear_inertia_element_mass_densities",
            "tower_base_shear_dofs",
            "tower_base_moment_dofs",
            "damper_placement_pairs",
            "diagnostic_nodes",
            "diagnostic_pairs",
            "damping_ratio",
            "opensees_system",
            "opensees_system_args",
            "rayleigh_frequency_a_hz",
            "rayleigh_frequency_b_hz",
        ),
    ),
}
