"""Canonical command template registry.

The canonical template root is ``pyansys_bridge/templates``. Legacy callers may
still pass explicit template paths; new code should resolve packaged command
modules through this registry.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


TEMPLATE_ROOT = Path(__file__).resolve().parent / "templates"


@dataclass(frozen=True)
class TemplateInfo:
    solver: str
    name: str
    role: str
    relative_path: str
    syntax: str = "jinja2"

    def path(self, root: str | Path | None = None) -> Path:
        base = Path(root) if root is not None else TEMPLATE_ROOT
        return base / self.relative_path


COMMAND_TEMPLATES: tuple[TemplateInfo, ...] = (
    TemplateInfo("ansys", "model_apdl", "model", "ansys/model.apdl", "jinja2"),
    TemplateInfo("ansys", "damper_viscous", "damper", "ansys/damper_viscous.apdl", "jinja2"),
    TemplateInfo("ansys", "damper_user300_viscous", "damper", "ansys/damper_viscous.apdl", "jinja2"),
    TemplateInfo("ansys", "damper_friction", "damper", "ansys/damper_friction.apdl", "jinja2"),
    TemplateInfo("ansys", "damper_eddy_current", "damper", "ansys/damper_eddy_current.apdl", "jinja2"),
    TemplateInfo("ansys", "gravity_apdl", "gravity", "ansys/gravity.apdl", "jinja2"),
    TemplateInfo("ansys", "modal_apdl", "modal", "ansys/modal.apdl", "jinja2"),
    TemplateInfo("ansys", "earthquake_apdl", "earthquake", "ansys/earthquake.apdl", "jinja2"),
    TemplateInfo("ansys", "traffic_apdl", "traffic", "ansys/traffic.apdl", "jinja2"),
    TemplateInfo("ansys", "wind_apdl", "wind", "ansys/wind.apdl", "jinja2"),
    TemplateInfo("ansys", "postprocess_apdl", "postprocess", "ansys/postprocess.apdl", "jinja2"),
    TemplateInfo("opensees", "model_openseespy", "model", "opensees/model.pyfrag"),
    TemplateInfo("opensees", "damper_viscous", "damper", "opensees/damper_viscous.pyfrag"),
    TemplateInfo("opensees", "damper_friction", "damper", "opensees/damper_friction.pyfrag"),
    TemplateInfo("opensees", "damper_eddy_current", "damper", "opensees/damper_eddy_current.pyfrag"),
    TemplateInfo("opensees", "gravity_openseespy", "gravity", "opensees/gravity.pyfrag"),
    TemplateInfo("opensees", "modal_openseespy", "modal", "opensees/modal.pyfrag"),
    TemplateInfo("opensees", "earthquake_openseespy", "earthquake", "opensees/earthquake.pyfrag"),
    TemplateInfo("opensees", "traffic_openseespy", "traffic", "opensees/traffic.pyfrag"),
    TemplateInfo("opensees", "wind_openseespy", "wind", "opensees/wind.pyfrag"),
    TemplateInfo("opensees", "postprocess_openseespy", "postprocess", "opensees/postprocess.pyfrag"),
)


def iter_command_templates(solver: str | None = None) -> Iterable[TemplateInfo]:
    """Yield registered command templates, optionally filtered by solver."""

    if solver is None:
        yield from COMMAND_TEMPLATES
        return
    normalized = solver.lower()
    yield from (template for template in COMMAND_TEMPLATES if template.solver.lower() == normalized)


def get_command_template(solver: str, name: str) -> TemplateInfo:
    """Return one registered command template by solver and module name."""

    normalized = solver.lower()
    for template in COMMAND_TEMPLATES:
        if template.solver.lower() == normalized and template.name == name:
            return template
    raise KeyError(f"Unknown command template: {solver}/{name}")
