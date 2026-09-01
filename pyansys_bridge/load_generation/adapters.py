"""Adapters from existing load sources to unified TimeHistoryLoad objects."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from pyansys_bridge.core.command_stream import template_command_modules
from pyansys_bridge.core.load_application_renderer import render_load_application_commands
from pyansys_bridge.core.time_table_renderer import render_load_tables
from pyansys_bridge.load_generation.base import ValidationResult
from pyansys_bridge.models import LoadPointMapping, TimeHistoryLoad
from pyansys_bridge.models.load_case import (
    load_point_mappings_from_csv,
    load_earthquake_acce_txt,
    load_traffic_csv,
    load_wind_csv,
)


class _BaseTimeHistoryLoadGenerator:
    kind: str
    default_application_type: str
    path_loader: Callable[..., TimeHistoryLoad]

    def validate_params(self, params: dict[str, Any]) -> ValidationResult:
        if not isinstance(params, Mapping):
            return ValidationResult(errors=("params must be a mapping",))
        if "macro_path" in params:
            return _validate_existing_path(params["macro_path"], "macro_path")
        if "path" in params:
            return _validate_existing_path(params["path"], "path")
        if "samples" not in params:
            return ValidationResult(errors=("path or samples is required",))
        if not params["samples"]:
            return ValidationResult(errors=("samples cannot be empty",))
        dt = params.get("dt")
        if dt is None:
            return ValidationResult(errors=("dt is required when samples are provided",))
        if float(dt) <= 0.0:
            return ValidationResult(errors=("dt must be positive",))
        return ValidationResult.ok()

    def generate(self, params: dict[str, Any]) -> list[TimeHistoryLoad]:
        validation = self.validate_params(params)
        if not validation.valid:
            raise ValueError(validation.error_message)
        if "macro_path" in params:
            return [self._generate_macro_compat(params)]
        if "path" in params:
            return [self._generate_from_path(params)]
        return [self._generate_from_samples(params)]

    def write_time_history_csv(self, params: dict[str, Any], path: str | Path) -> Path:
        """Write the generated load using the unified TimeHistoryLoad CSV contract."""

        loads = self.generate(params)
        if len(loads) != 1:
            raise ValueError("write_time_history_csv expects exactly one generated load")
        return loads[0].write_csv(path)

    def render_ansys_apdl(self, params: dict[str, Any]) -> str:
        """Render this load as a standalone ANSYS APDL load module."""

        load = _ansys_renderable_load(self.generate(params)[0], params)
        context = _ansys_context_from_load(load, params)
        context.update(
            {
                "ansys_load_render_role": load.kind,
                "load_tables": render_load_tables((load,)),
                "load_application_commands": render_load_application_commands((load,)),
            }
        )
        module = _ansys_load_module(self.kind)
        return module.render(context)

    def write_ansys_apdl(self, params: dict[str, Any], path: str | Path) -> Path:
        """Write this load as a standalone ANSYS APDL load module."""

        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self.render_ansys_apdl(params), encoding="utf-8")
        return target

    def _generate_from_path(self, params: dict[str, Any]) -> TimeHistoryLoad:
        return self.path_loader(
            params["path"],
            dt=params.get("dt"),
            duration=params.get("duration"),
            scale=float(params.get("scale", 1.0)),
            application=self._application(params),
            metadata=params.get("metadata"),
        )

    def _generate_from_samples(self, params: dict[str, Any]) -> TimeHistoryLoad:
        return TimeHistoryLoad.from_series(
            kind=self.kind,
            samples=params["samples"],
            dt=float(params["dt"]),
            duration=params.get("duration"),
            application=self._application(params),
            metadata=params.get("metadata"),
        )

    def _generate_macro_compat(self, params: dict[str, Any]) -> TimeHistoryLoad:
        if self.kind != "traffic":
            raise ValueError("macro_path compatibility is only supported for traffic loads")
        macro_path = Path(params["macro_path"])
        return TimeHistoryLoad.from_series(
            kind="traffic",
            samples=(1.0,),
            dt=float(params.get("dt", 1.0)),
            duration=float(params.get("duration", 0.0)),
            application={"type": "stbridge_traffic_macro", **dict(params.get("application") or {})},
            metadata={
                "source_path": str(macro_path),
                "deprecated_macro_mode": True,
                **dict(params.get("metadata") or {}),
            },
        )

    def _application(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"type": self.default_application_type, **dict(params.get("application") or {})}


class EarthquakeLoadGenerator(_BaseTimeHistoryLoadGenerator):
    kind = "earthquake"
    default_application_type = "uniform_excitation"
    path_loader = staticmethod(load_earthquake_acce_txt)


class WindLoadGenerator(_BaseTimeHistoryLoadGenerator):
    kind = "wind"
    default_application_type = "nodal_force"
    path_loader = staticmethod(load_wind_csv)


class TrafficLoadGenerator(_BaseTimeHistoryLoadGenerator):
    kind = "traffic"
    default_application_type = "moving_or_nodal_force"
    path_loader = staticmethod(load_traffic_csv)


def _validate_existing_path(value: Any, name: str) -> ValidationResult:
    path = Path(value)
    if not path.exists() or not path.is_file():
        return ValidationResult(errors=(f"{name} does not exist: {path}",))
    return ValidationResult.ok()


def _ansys_context_from_load(load: TimeHistoryLoad, params: dict[str, Any]) -> dict[str, Any]:
    prefix = load.kind
    context = {
        f"{prefix}_name": load.metadata.get("name", prefix),
        f"{prefix}_path": load.metadata.get("source_path"),
        f"{prefix}_scale": load.metadata.get("scale", params.get("scale", 1.0)),
        f"{prefix}_dt": load.dt,
        f"{prefix}_duration": load.duration,
        f"{prefix}_direction_x": 0.0,
        f"{prefix}_direction_y": 1.0 if load.kind == "wind" else -1.0 if load.kind == "traffic" else 0.0,
        f"{prefix}_direction_z": 0.0,
    }
    mappings = _ansys_load_points(_load_point_mapping_records(params), load.kind)
    if mappings:
        context[f"ansys_{prefix}_load_points"] = mappings
    return context


def _ansys_renderable_load(load: TimeHistoryLoad, params: dict[str, Any]) -> TimeHistoryLoad:
    mappings = _ansys_load_points(_load_point_mapping_records(params), load.kind)
    if load.kind == "earthquake" or not mappings:
        return load
    base_name = {"wind": "WIND_LOAD", "traffic": "TRAFFIC_LOAD"}[load.kind]
    source_name = f"{base_name}_SOURCE"
    tables = []
    load_points = []
    for index, point in enumerate(mappings, start=1):
        dof = str(point["dof"]).upper()
        label = str(point["label"]).upper()
        safe_label = "".join(char if char.isalnum() else "_" for char in label)
        table_name = f"{base_name}_{index:02d}_{safe_label}_{dof}"
        tables.append(
            {
                "name": table_name,
                "factor": str(float(load.metadata.get("scale", params.get("scale", 1.0))) * float(point["weight"])),
            }
        )
        load_points.append({"node_id": point["node_id"], "dof": dof, "table_name": table_name})
    application = {
        **dict(load.application),
        "type": "distributed_nodal_force",
        "distributed_tables": {
            "source_name": source_name,
            "tables": tables,
        },
        "load_points": load_points,
    }
    return TimeHistoryLoad(
        kind=load.kind,
        dt=load.dt,
        duration=load.duration,
        samples=load.samples,
        application=application,
        metadata=load.metadata,
    )


def _ansys_load_points(records: Any, kind: str) -> tuple[dict[str, object], ...]:
    if not records:
        return ()
    points = []
    for record in records:
        payload = dict(record)
        payload.setdefault("group", kind)
        point = LoadPointMapping.from_dict(payload).to_ansys_load_point()
        if payload.get("source_column") is not None:
            point["source_column"] = int(payload["source_column"])
        for key in (
            "target_x_m",
            "actual_x_m",
            "tributary_length_m",
            "mean_force_N",
            "dynamic_data_column",
        ):
            if payload.get(key) is not None:
                point[key] = float(payload[key]) if key != "dynamic_data_column" else int(payload[key])
        points.append(point)
    return tuple(points)


def _load_point_mapping_records(params: dict[str, Any]) -> Any:
    records = params.get("load_point_mappings") or params.get("load_mappings")
    if records:
        return records
    path = params.get("load_point_mappings_path") or params.get("load_mappings_path")
    if path is None:
        return ()
    return [mapping.to_dict() for mapping in load_point_mappings_from_csv(path)]


def _ansys_load_module(kind: str):
    module_name = {
        "earthquake": "earthquake_apdl",
        "wind": "wind_apdl",
        "traffic": "traffic_apdl",
    }[kind]
    for module in template_command_modules():
        if module.solver == "ansys" and module.name == module_name:
            return module
    raise ValueError(f"Missing ANSYS load module template: {module_name}")
