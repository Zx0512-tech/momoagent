"""ANSYS transient load-table and solve-command rendering helpers."""

from __future__ import annotations

import re
from pathlib import Path

from pyansys_bridge.core.ansys_load_targets import (
    STBRIDGE_TRAFFIC_MACRO_APPLICATIONS,
    is_zero,
    traffic_application,
    traffic_macro_solution_mode,
)
from pyansys_bridge.core.load_application_renderer import render_load_application_commands
from pyansys_bridge.core.time_table_renderer import (
    render_load_tables,
    write_transformed_vread_column_file,
)
from pyansys_bridge.models import TimeHistoryLoad

_APDL_TABLE_FORCE_RE = re.compile(
    r"^\s*F\s*,\s*([^,]+?)\s*,\s*([^,]+?)\s*,\s*%([A-Za-z_][A-Za-z0-9_]*)%\s*(?:!.*)?$",
    re.IGNORECASE,
)
_APDL_FORCE_RE = re.compile(r"^\s*F\s*,", re.IGNORECASE)


def read_scalar_record_values(path: Path) -> list[float]:
    """读取标量地震记录中的有效数值。"""

    values: list[float] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        text = line.strip()
        if not text or text.startswith(("#", "!")):
            continue
        tokens = text.replace(",", " ").split()
        try:
            values.append(float(tokens[-1]))
        except (IndexError, ValueError) as exc:
            raise ValueError(f"Cannot parse scalar earthquake record {path} at line {line_number}") from exc
    if not values:
        raise ValueError(f"Scalar earthquake record has no numeric samples: {path}")
    return values


def legacy_vread_rows(context: dict[str, object], sample_count: int) -> int:
    """按旧 VREAD 合同计算需要写入的记录行数。"""

    dt = context.get("earthquake_dt", context.get("load_dt"))
    duration = context.get("earthquake_duration", context.get("load_duration"))
    try:
        if dt is not None and duration is not None and float(dt) > 0.0:
            return max(1, int(round(float(duration) / float(dt))))
    except (TypeError, ValueError):
        pass
    return max(1, sample_count - 1)


def ansys_load_table_commands(context: dict[str, object]) -> dict[str, str]:
    loads = _ansys_time_history_loads(context)
    legacy_earthquake = _uses_legacy_earthquake_vread(context)
    legacy_earthquake_tables = (
        _ansys_legacy_earthquake_vread_commands(context)
        if legacy_earthquake
        else ""
    )
    rendered_loads = tuple(
        load
        for load in loads
        if not (legacy_earthquake and load.kind == "earthquake")
    )
    wind_tables, wind_loads = (
        _ansys_distributed_vector_table_commands(
            base_name="WIND_LOAD",
            source_name="WIND_LOAD_SOURCE",
            path=context.get("wind_path"),
            path_var="WIND_LOAD_PATH",
            load_name="wind",
            scale=context.get("wind_scale"),
            dt=context.get("wind_dt"),
            duration=context.get("wind_duration"),
            direction=(
                context.get("wind_direction_x"),
                context.get("wind_direction_y"),
                context.get("wind_direction_z"),
            ),
            load_points=context.get("ansys_wind_load_points", ()),
            load_component=context.get("ansys_wind_load_component"),
        )
        if "wind_name" in context
        else ("", "")
    )
    traffic_tables, traffic_loads = (
        _ansys_traffic_load_commands(context)
        if "traffic_name" in context
        else ("", "")
    )
    return {
        "time_history_loads": loads,
        "ansys_load_render_role": _ansys_load_render_role(context),
        "load_tables": "\n".join(
            part for part in (legacy_earthquake_tables, render_load_tables(rendered_loads)) if part
        ),
        "load_application_commands": render_load_application_commands(rendered_loads),
        "ansys_earthquake_table_commands": (
            legacy_earthquake_tables
            if legacy_earthquake
            else _ansys_scalar_table_commands(
                table_name="EQ_ACC_TABLE",
                path=context.get("earthquake_path"),
                path_var="EQ_ACC_PATH",
                scale=context.get("earthquake_scale"),
                dt=context.get("earthquake_dt"),
                duration=context.get("earthquake_duration"),
                inline_from_source=context.get("earthquake_inline_table_from_source"),
            )
            if "earthquake_name" in context
            else ""
        ),
        "ansys_wind_table_commands": wind_tables,
        "ansys_wind_load_application_commands": wind_loads,
        "ansys_wind_mean_preload_commands": _ansys_wind_mean_preload_commands(context),
        "ansys_traffic_table_commands": traffic_tables,
        "ansys_traffic_load_application_commands": traffic_loads,
    }


def _ansys_time_history_loads(context: dict[str, object]) -> tuple[TimeHistoryLoad, ...]:
    loads = []
    if "earthquake_name" in context:
        loads.append(
            _ansys_scalar_time_history_load(
                kind="earthquake",
                table_name="EQ_ACC_TABLE",
                path_var="EQ_ACC_PATH",
                name=context.get("earthquake_name"),
                path=context.get("earthquake_path"),
                scale=context.get("earthquake_scale"),
                dt=context.get("earthquake_dt"),
                duration=context.get("earthquake_duration"),
                inline_from_source=context.get("earthquake_inline_table_from_source"),
                application={"type": "uniform_excitation", "table_name": "EQ_ACC_TABLE"},
            )
        )
    if "wind_name" in context:
        loads.append(
            _ansys_distributed_time_history_load(
                kind="wind",
                base_name="WIND_LOAD",
                source_name="WIND_LOAD_SOURCE",
                path_var="WIND_LOAD_PATH",
                name=context.get("wind_name"),
                path=context.get("wind_path"),
                scale=context.get("wind_scale"),
                dt=context.get("wind_dt"),
                duration=context.get("wind_duration"),
                direction=(
                    context.get("wind_direction_x"),
                    context.get("wind_direction_y"),
                    context.get("wind_direction_z"),
                ),
                load_points=context.get("ansys_wind_load_points", ()),
                load_component=context.get("ansys_wind_load_component"),
            )
        )
    if "traffic_name" in context:
        if traffic_application(context) in STBRIDGE_TRAFFIC_MACRO_APPLICATIONS:
            loads.append(_ansys_traffic_macro_load(context))
        else:
            loads.append(
                _ansys_distributed_time_history_load(
                    kind="traffic",
                    base_name="TRAFFIC_LOAD",
                    source_name="TRAFFIC_LOAD_SOURCE",
                    path_var="TRAFFIC_LOAD_PATH",
                    name=context.get("traffic_name"),
                    path=context.get("traffic_path"),
                    scale=context.get("traffic_scale"),
                    dt=context.get("traffic_dt"),
                    duration=context.get("traffic_duration"),
                    direction=(
                        context.get("traffic_direction_x"),
                        context.get("traffic_direction_y"),
                        context.get("traffic_direction_z"),
                    ),
                    load_points=context.get("ansys_traffic_load_points", ()),
                    load_component=context.get("ansys_traffic_load_component"),
                )
            )
    return tuple(loads)


def _ansys_load_render_role(context: dict[str, object]) -> str:
    for role in ("earthquake", "wind", "traffic"):
        if f"{role}_name" in context:
            return role
    return ""


def _ansys_scalar_time_history_load(
    *,
    kind: str,
    table_name: str,
    path_var: str,
    name,
    path,
    scale,
    dt,
    duration,
    inline_from_source=False,
    application: dict[str, object],
) -> TimeHistoryLoad:
    effective_dt, effective_duration = _effective_load_timing(dt, duration)
    effective_scale = 1.0 if scale is None else float(scale)
    metadata = {
        "name": name,
        "scale": effective_scale,
        "path_var": path_var,
    }
    samples = (effective_scale,)
    if path is not None:
        metadata["source_path"] = str(path)
    if path is not None and inline_from_source:
        metadata["inline_table_from_source"] = True
        samples = tuple(value * effective_scale for value in _read_scalar_source_samples(path))
    return TimeHistoryLoad(
        kind=kind,
        dt=effective_dt,
        duration=effective_duration,
        samples=samples,
        application={**application, "table_name": table_name},
        metadata=metadata,
    )


def _ansys_distributed_time_history_load(
    *,
    kind: str,
    base_name: str,
    source_name: str,
    path_var: str,
    name,
    path,
    scale,
    dt,
    duration,
    direction: tuple[object, object, object],
    load_points,
    load_component=None,
) -> TimeHistoryLoad:
    effective_dt, effective_duration = _effective_load_timing(dt, duration)
    rows = _table_row_count(path, effective_dt, effective_duration)
    tables, application_points = _ansys_distributed_table_specs(
        base_name=base_name,
        source_name=source_name,
        rows=rows,
        load_name=kind,
        scale=scale,
        direction=direction,
        load_points=load_points,
        load_component=load_component,
    )
    metadata = {
        "name": name,
        "scale": 1.0 if scale is None else float(scale),
    }
    if path is not None:
        metadata["source_path"] = str(path)
    return TimeHistoryLoad(
        kind=kind,
        dt=effective_dt,
        duration=effective_duration,
        samples=(1.0,),
        application={
            "type": "distributed_nodal_force",
            "distributed_tables": {
                "source_name": source_name,
                "path_var": path_var,
                "tables": tables,
            },
            "load_points": application_points,
        },
        metadata=metadata,
    )


def _ansys_distributed_table_specs(
    *,
    base_name: str,
    source_name: str,
    rows: int,
    load_name: str,
    scale,
    direction: tuple[object, object, object],
    load_points,
    load_component=None,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    points = tuple(load_points or ())
    component = None if load_component is None else str(load_component).strip()
    if not points and not component:
        raise ValueError(f"ANSYS {load_name} load module requires target load nodes")
    active_axes = [
        (axis, dof, axis_component)
        for axis, dof, axis_component in (("X", "FX", direction[0]), ("Y", "FY", direction[1]), ("Z", "FZ", direction[2]))
        if not is_zero(axis_component)
    ]
    explicit_dof_points = _has_explicit_dof_points(points)
    if not explicit_dof_points and not active_axes:
        raise ValueError(f"ANSYS {load_name} load module requires at least one nonzero direction component")

    table_specs: list[dict[str, object]] = []
    application_points: list[dict[str, object]] = []
    if component:
        safe_component = "".join(char if char.isalnum() else "_" for char in component.upper())
        for axis, dof, axis_component in active_axes:
            table_name = f"{base_name}_{safe_component}_{axis}"
            table_specs.append({"name": table_name, "factor": _ansys_product(scale, axis_component)})
            application_points.append({"node_id": component, "dof": dof, "table_name": table_name})
        return table_specs, application_points

    if explicit_dof_points:
        for point_index, point in enumerate(points, start=1):
            node_id = int(point["node_id"])
            weight = point["weight"]
            dof = str(point["dof"]).upper()
            label = str(point["label"]).upper()
            safe_label = "".join(char if char.isalnum() else "_" for char in label)
            table_name = f"{base_name}_{point_index:02d}_{safe_label}_{dof}"
            table_specs.append(
                {
                    "name": table_name,
                    "factor": _ansys_product(scale, weight),
                    "rows": rows,
                    "source_name": source_name,
                    "source_column": _source_column(point),
                    "offset": _ansys_mean_force_offset(point, scale),
                }
            )
            application_points.append({"node_id": node_id, "dof": dof, "table_name": table_name})
        return table_specs, application_points

    for point_index, point in enumerate(points, start=1):
        node_id = int(point["node_id"])
        weight = point["weight"]
        label = str(point["label"]).upper()
        safe_label = "".join(char if char.isalnum() else "_" for char in label)
        for axis, dof, axis_component in active_axes:
            table_name = f"{base_name}_{point_index:02d}_{safe_label}_{axis}"
            factor = _ansys_product(_ansys_product(scale, axis_component), weight)
            table_specs.append(
                {
                    "name": table_name,
                    "factor": factor,
                    "rows": rows,
                    "source_name": source_name,
                    "source_column": _source_column(point),
                    "offset": _ansys_mean_force_offset(
                        point,
                        _ansys_product(scale, axis_component),
                    ),
                }
            )
            application_points.append({"node_id": node_id, "dof": dof, "table_name": table_name})
    return table_specs, application_points


def _ansys_traffic_macro_load(context: dict[str, object]) -> TimeHistoryLoad:
    effective_dt, effective_duration = _effective_load_timing(context.get("traffic_dt"), context.get("traffic_duration"))
    path = context.get("traffic_path")
    metadata = {
        "name": context.get("traffic_name"),
        "scale": 1.0 if context.get("traffic_scale") is None else float(context.get("traffic_scale")),
    }
    if path is not None:
        metadata["source_path"] = str(path)
    return TimeHistoryLoad(
        kind="traffic",
        dt=effective_dt,
        duration=effective_duration,
        samples=(1.0,),
        application={
            "type": "stbridge_traffic_macro",
            "solution_mode": traffic_macro_solution_mode(context),
        },
        metadata=metadata,
    )


def _effective_load_timing(dt, duration) -> tuple[float, float]:
    effective_dt = 1.0 if dt is None else float(dt)
    effective_duration = 0.0 if duration is None else float(duration)
    return effective_dt, effective_duration


def _ansys_scalar_table_commands(
    table_name: str,
    path,
    path_var: str,
    scale,
    dt,
    duration,
    inline_from_source=False,
) -> str:
    rows = _table_row_count(path, dt, duration)
    effective_scale = 1.0 if scale is None else scale
    effective_dt = 1.0 if dt is None else float(dt)
    if path is not None and inline_from_source:
        samples = _read_scalar_source_samples(path)
        rows = max(rows, len(samples))
        lines = [f"{path_var}='{path}'", f"*DIM,{table_name},TABLE,{rows},1,1,TIME"]
        for row in range(1, rows + 1):
            sample = samples[min(row - 1, len(samples) - 1)] if samples else 0.0
            lines.append(f"{table_name}({row},0)={(row - 1) * effective_dt:.12g}")
            lines.append(f"{table_name}({row},1)={sample * float(effective_scale):.12g}")
        lines.append(f"{table_name}_ROWS={rows}")
        return "\n".join(lines)
    if path is not None:
        fname, ext = _tread_file_args(path)
        return "\n".join(
            [
                f"{path_var}='{path}'",
                f"*DIM,{table_name},TABLE,{rows},1,1,TIME",
                f"*TREAD,{table_name},{fname},{ext}",
                f"*VOPER,{table_name}(1,1),{table_name}(1,1),MULT,{effective_scale}",
                f"{table_name}_ROWS={rows}",
            ]
        )
    lines = [f"*DIM,{table_name},TABLE,{rows},1,1,TIME"]
    for row in range(1, rows + 1):
        lines.append(f"{table_name}({row},0)={(row - 1) * effective_dt:.12g}")
        lines.append(f"{table_name}({row},1)={effective_scale}")
    lines.append(f"{table_name}_ROWS={rows}")
    return "\n".join(lines)


def _ansys_legacy_earthquake_vread_commands(context: dict[str, object]) -> str:
    if not context.get("earthquake_path"):
        raise ValueError("ANSYS legacy_vread earthquake mode requires earthquake_path")
    dt = _ansys_float(context.get("earthquake_dt"), _ansys_float(context.get("load_dt"), 1.0))
    rows = _legacy_earthquake_vread_rows(context)
    return "\n".join(
        [
            f"tidel={dt}",
            f"*DIM,ACEX, , {rows},2",
            "*CREAT, TDATE",
            "*VFILL,ACEX(1,1),ramp,0,tidel",
            f"*VREAD,ACEX(1,2), ACCE, TXT, ,  , {rows}, 1",
            "(1F12.9)",
            "*END",
            "/INPUT,TDATE",
            f"ACEX_ROWS={rows}",
        ]
    )


def _uses_legacy_earthquake_vread(context: dict[str, object]) -> bool:
    return "earthquake_name" in context and context.get("earthquake_path") is not None


def _legacy_earthquake_vread_rows(context: dict[str, object]) -> int:
    dt = context.get("earthquake_dt", context.get("load_dt"))
    duration = context.get("earthquake_duration", context.get("load_duration"))
    try:
        if dt is not None and duration is not None and float(dt) > 0.0:
            return max(1, int(round(float(duration) / float(dt))))
    except (TypeError, ValueError):
        pass
    return max(1, _table_row_count(context.get("earthquake_path"), dt, duration) - 1)


def _read_scalar_source_samples(path) -> list[float]:
    samples: list[float] = []
    source = Path(str(path))
    for line_number, line in enumerate(source.read_text(encoding="utf-8-sig").splitlines(), start=1):
        text = line.strip()
        if not text or text.startswith(("#", "!")):
            continue
        tokens = text.replace(",", " ").split()
        try:
            samples.append(float(tokens[-1]))
        except (IndexError, ValueError) as exc:
            raise ValueError(f"Cannot parse scalar time-history source {source} at line {line_number}") from exc
    if not samples:
        raise ValueError(f"Scalar time-history source has no numeric samples: {source}")
    return samples


def _ansys_vector_table_commands(
    base_name: str,
    source_name: str,
    path,
    path_var: str,
    load_name: str,
    scale,
    dt,
    duration,
    direction: tuple[object, object, object],
) -> str:
    axes = ("", "_Y", "_Z")
    if path is None:
        raise ValueError(f"ANSYS {load_name} load module requires a time-history path")
    rows = _table_row_count(path, dt, duration)
    fname, ext = _tread_file_args(path)
    lines = [
        f"{path_var}='{path}'",
        f"*DIM,{source_name},TABLE,{rows},1,1,TIME",
        f"*TREAD,{source_name},{fname},{ext}",
    ]
    for suffix, component in zip(axes, direction):
        table_name = f"{base_name}{suffix}"
        factor = _ansys_product(scale, component)
        lines.extend(
            [
                f"*DIM,{table_name},TABLE,{rows},1,1,TIME",
            ]
        )
    lines.append(f"*DO,IROW,1,{rows}")
    for suffix, component in zip(axes, direction):
        table_name = f"{base_name}{suffix}"
        factor = _ansys_product(scale, component)
        lines.extend(
            [
                f"{table_name}(IROW,0)={source_name}(IROW,0)",
                f"{table_name}(IROW,1)={source_name}(IROW,1)*{_apdl_factor(factor)}",
            ]
        )
    lines.append("*ENDDO")
    return "\n".join(lines)


def _ansys_traffic_load_commands(context: dict[str, object]) -> tuple[str, str]:
    path = context.get("traffic_path")
    if traffic_application(context) in STBRIDGE_TRAFFIC_MACRO_APPLICATIONS:
        return "", _ansys_traffic_macro_commands(path, context.get("traffic_scale"))
    return _ansys_distributed_vector_table_commands(
        base_name="TRAFFIC_LOAD",
        source_name="TRAFFIC_LOAD_SOURCE",
        path=path,
        path_var="TRAFFIC_LOAD_PATH",
        load_name="traffic",
        scale=context.get("traffic_scale"),
        dt=context.get("traffic_dt"),
        duration=context.get("traffic_duration"),
        direction=(
            context.get("traffic_direction_x"),
            context.get("traffic_direction_y"),
            context.get("traffic_direction_z"),
        ),
        load_points=context.get("ansys_traffic_load_points", ()),
        load_component=context.get("ansys_traffic_load_component"),
    )


def _ansys_traffic_macro_commands(path, scale) -> str:
    if path is None:
        raise ValueError("ANSYS traffic macro loading requires stbridge_random_traffic_load.mac")
    source = Path(str(path))
    solution_mode = traffic_macro_solution_mode({"traffic_path": path, "traffic_name": "traffic"})
    scale_note = (
        "! traffic_scale is ignored in macro mode; vehicle loads are already baked into the generated macro."
        if str(scale) not in {"1", "1.0", "None"}
        else "! traffic macro contains absolute FY loads generated from WIM axle weights."
    )
    return "\n".join(
        [
            "! STbridge random traffic loading scheme: single-road centerline moving axle loads",
            "! Source generator: RandomTrafficLoadGeneration/generate_stbridge_traffic_loads.py",
            f"! Expected generator options: --single-road --direction-mode bidirectional --solution-mode {solution_mode}",
            "! The macro owns FDELE, TIME, F,node,FY, SOLVE, and POST26 UX export.",
            scale_note,
            f"/CWD,{_apdl_quoted(source.parent)}",
            f"/INPUT,{source.stem},{source.suffix.lstrip('.') or 'mac'}",
        ]
    )


def _ansys_distributed_vector_table_commands(
    base_name: str,
    source_name: str,
    path,
    path_var: str,
    load_name: str,
    scale,
    dt,
    duration,
    direction: tuple[object, object, object],
    load_points,
    load_component=None,
) -> tuple[str, str]:
    points = tuple(load_points or ())
    component = None if load_component is None else str(load_component).strip()
    if path is not None and Path(str(path)).is_absolute() and not Path(str(path)).is_file():
        raise ValueError(f"ANSYS distributed TABLE requires an existing source file: {path}")
    if not points and not component:
        raise ValueError(f"ANSYS {load_name} load module requires target load nodes")
    active_axes = [
        (axis, dof, component)
        for axis, dof, component in (("X", "FX", direction[0]), ("Y", "FY", direction[1]), ("Z", "FZ", direction[2]))
        if not is_zero(component)
    ]
    explicit_dof_points = _has_explicit_dof_points(points)
    if not explicit_dof_points and not active_axes:
        raise ValueError(f"ANSYS {load_name} load module requires at least one nonzero direction component")

    rows = _table_row_count(path, dt, duration)
    source_column_count = _source_column_count(points)
    if path is None:
        table_lines = [
            f"*DIM,{source_name},TABLE,{rows},{source_column_count},1,TIME",
            "*DO,IROW,1,{rows}".format(rows=rows),
            f"{source_name}(IROW,0)=(IROW-1)*{dt if dt is not None else 1.0}",
        ]
        if source_column_count == 1:
            table_lines.append(f"{source_name}(IROW,1)=1.0")
        else:
            table_lines.extend(
                [
                    f"*DO,ICOL,1,{source_column_count}",
                    f"{source_name}(IROW,ICOL)=1.0",
                    "*ENDDO",
                ]
            )
        table_lines.append("*ENDDO")
    else:
        fname, ext = _tread_file_args(path)
        table_lines = [
            f"{path_var}='{path}'",
            f"*DIM,{source_name},TABLE,{rows},{source_column_count},1,TIME",
            f"*TREAD,{source_name},{fname},{ext}",
        ]
    if path is not None and Path(str(path)).exists():
        source_path = Path(str(path)).resolve()
        table_lines = [f"{path_var}='{source_path}'"]
        def append_vread_table(
            table_name: str,
            source_column: int,
            factor: object,
            offset: float = 0.0,
        ) -> None:
            numeric_factor = float(factor)
            value_path = write_transformed_vread_column_file(
                source_path,
                source_name,
                source_column,
                rows,
                factor=numeric_factor,
                offset=offset,
            )
            fname, ext = _tread_file_args(value_path)
            table_lines.extend(
                [
                    f"*DIM,{table_name},TABLE,{rows},1,1,TIME",
                    f"*VFILL,{table_name}(1,0),RAMP,0,{float(dt if dt is not None else 1.0):.12g}",
                    f"*VREAD,{table_name}(1,1),{fname},{ext.upper()},,,{rows},1",
                    "(1E22.14)",
                ]
            )
    else:
        append_vread_table = None
    load_lines = [f"! {load_name} nodal time-history loads"]
    if component:
        safe_component = "".join(char if char.isalnum() else "_" for char in component.upper())
        for axis, dof, axis_component in active_axes:
            table_name = f"{base_name}_{safe_component}_{axis}"
            factor = _ansys_product(scale, axis_component)
            if append_vread_table is not None:
                append_vread_table(table_name, 1, factor)
            else:
                table_lines.extend(
                    [
                        f"*DIM,{table_name},TABLE,{rows},1,1,TIME",
                        f"*DO,IROW,1,{rows}",
                        f"{table_name}(IROW,0)={source_name}(IROW,0)",
                        f"{table_name}(IROW,1)={source_name}(IROW,1)*{_apdl_factor(factor)}",
                        "*ENDDO",
                    ]
                )
            load_lines.append(f"F,{component},{dof},%{table_name}%")
        return "\n".join(table_lines), "\n".join(load_lines)

    if explicit_dof_points:
        for point_index, point in enumerate(points, start=1):
            node_id = int(point["node_id"])
            weight = point["weight"]
            dof = str(point["dof"]).upper()
            label = str(point["label"]).upper()
            safe_label = "".join(char if char.isalnum() else "_" for char in label)
            table_name = f"{base_name}_{point_index:02d}_{safe_label}_{dof}"
            source_column = _source_column(point)
            factor = _ansys_product(scale, weight)
            offset = _ansys_mean_force_offset(point, scale)
            if append_vread_table is not None:
                append_vread_table(table_name, source_column, factor, offset)
            else:
                table_lines.extend(
                    [
                        f"*DIM,{table_name},TABLE,{rows},1,1,TIME",
                        f"*DO,IROW,1,{rows}",
                        f"{table_name}(IROW,0)={source_name}(IROW,0)",
                        f"{table_name}(IROW,1)={source_name}(IROW,{source_column})*{_apdl_factor(factor)}+{offset:g}",
                        "*ENDDO",
                    ]
                )
            load_lines.append(f"F,{node_id},{dof},%{table_name}%")
        return "\n".join(table_lines), "\n".join(load_lines)

    for point_index, point in enumerate(points, start=1):
        node_id = int(point["node_id"])
        weight = point["weight"]
        label = str(point["label"]).upper()
        safe_label = "".join(char if char.isalnum() else "_" for char in label)
        for axis, dof, component in active_axes:
            table_name = f"{base_name}_{point_index:02d}_{safe_label}_{axis}"
            factor = _ansys_product(_ansys_product(scale, component), weight)
            source_column = _source_column(point)
            offset = _ansys_mean_force_offset(point, _ansys_product(scale, component))
            if append_vread_table is not None:
                append_vread_table(table_name, source_column, factor, offset)
            else:
                table_lines.extend(
                    [
                        f"*DIM,{table_name},TABLE,{rows},1,1,TIME",
                        f"*DO,IROW,1,{rows}",
                        f"{table_name}(IROW,0)={source_name}(IROW,0)",
                        f"{table_name}(IROW,1)={source_name}(IROW,{source_column})*{_apdl_factor(factor)}+{offset:g}",
                        "*ENDDO",
                    ]
                )
            load_lines.append(f"F,{node_id},{dof},%{table_name}%")
    return "\n".join(table_lines), "\n".join(load_lines)


def _has_explicit_dof_points(points) -> bool:
    if not any("dof" in point for point in points):
        return False
    if not all("dof" in point for point in points):
        raise ValueError("ANSYS explicit load-point mappings require dof on every point")
    return True


def _source_column(point: dict[str, object]) -> int:
    value = point.get("source_column", 1)
    column = int(value)
    if column <= 0:
        raise ValueError("source_column must be a positive 1-based load-data column")
    return column


def _source_column_count(points) -> int:
    columns = [_source_column(dict(point)) for point in points or ()]
    return max(columns, default=1)


def _ansys_mean_force_offset(point: dict[str, object], scale) -> float:
    mean_force = float(point.get("mean_force_N", 0.0))
    return mean_force * float(scale if scale is not None else 1.0)


def _ansys_wind_mean_preload_commands(context: dict[str, object]) -> str:
    if "wind_name" not in context:
        return ""
    scale = context.get("wind_scale")
    lines = []
    for raw_point in context.get("ansys_wind_load_points", ()) or ():
        point = dict(raw_point)
        mean_force = _ansys_mean_force_offset(point, scale)
        if mean_force == 0.0:
            continue
        node_id = int(point["node_id"])
        dof = str(point.get("dof") or "FY").upper()
        lines.append(f"F,{node_id},{dof},{mean_force:g}")
    return "\n".join(lines)


def ansys_solver_execution_commands(context: dict[str, object]) -> str:
    load_application_commands = str(context.get("load_application_commands") or "").strip()
    if traffic_application(context) in STBRIDGE_TRAFFIC_MACRO_APPLICATIONS:
        solution_mode = traffic_macro_solution_mode(context)
        damping_ratio = _ansys_float(context.get("damping_ratio"), 0.05)
        gravity_x = _ansys_float(context.get("gravity_accel_x"), 0.0)
        gravity_y = _ansys_float(context.get("gravity_accel_y"), 9.8)
        gravity_z = _ansys_float(context.get("gravity_accel_z"), 0.0)
        commands = [
            "/SOLU",
            "ANTYPE,TRANS",
            "TRNOPT,FULL",
            "SSTIF,ON",
            "NLGEOM,ON",
            "SOLCONTROL,ON",
            f"KES={damping_ratio}",
            "*IF,M,LE,0,THEN",
            "M=0.055317",
            "*ENDIF",
            "*IF,N,LE,0,THEN",
            "N=0.100449",
            "*ENDIF",
            "ALPHAD,2*KES*M*N*2*3.1416/(M+N)",
            "BETAD,KES/(M+N)/3.1416",
            "TIMINT,OFF",
            f"ACEL,{gravity_x},{gravity_y},{gravity_z}",
            "TIME,0.001",
            "NSUBST,2,2,2",
            "KBC,1",
            "SOLVE",
            "",
            "TIMINT,ON",
            "OUTRES,ALL,ALL",
            "AUTOTS,ON",
            "KBC,1",
        ]
        if load_application_commands:
            commands.extend(
                [
                    "! 瞬态荷载施加命令",
                    load_application_commands,
                ]
            )
        commands.extend(
            [
                f"! Random traffic macro already executed the {solution_mode} time-step SOLVE loop.",
                "! No additional unified transient SOLVE is issued for this command stream.",
                "/POST1",
                "SET,LAST",
                "PRRSOL",
                "FINISH",
            ]
        )
        return "\n".join(commands)

    dt = float(context.get("load_dt") or 1.0)
    duration = float(context.get("load_duration") or dt)
    if dt <= 0.0:
        dt = 1.0
    if duration <= 0.0:
        duration = dt
    steps = max(1, int(round(duration / dt)))
    if "earthquake_name" in context:
        return _ansys_earthquake_solver_execution_commands(context, load_application_commands, dt, steps)

    interpolate_tables = bool(context.get("interpolate_time_history_tables", False))
    requires_stepwise_sum = "FCUM,ADD" in load_application_commands.upper()
    if interpolate_tables and not requires_stepwise_sum:
        load_prelude_commands = "\n".join(
            part for part in ("FDELE,ALL,ALL", load_application_commands) if part
        )
        load_row_setup_commands = ""
        step_load_commands = ""
    else:
        load_prelude_commands, load_row_setup_commands, step_load_commands = _split_stepwise_nodal_load_commands(
            load_application_commands
        )
    damping_ratio = _ansys_float(context.get("damping_ratio"), 0.05)
    gravity_x = _ansys_float(context.get("gravity_accel_x"), 0.0)
    gravity_y = _ansys_float(context.get("gravity_accel_y"), 9.8)
    gravity_z = _ansys_float(context.get("gravity_accel_z"), 0.0)
    gravity_substeps = _ansys_nsubst(context.get("gravity_substeps"), "10,20,5")
    wind_mean_preload = str(context.get("ansys_wind_mean_preload_commands") or "").strip()
    commands = [
        "/SOLU",
        "ANTYPE,TRANS",
        "TRNOPT,FULL",
        "SSTIF,ON",
        "SOLCONTROL,ON",
        "OUTRES,ALL,ALL",
        "OUTPR,ALL,NONE",
        "NLGEOM,ON",
        "TIMINT,OFF",
        "KBC,1",
        f"ACEL,{gravity_x},{gravity_y},{gravity_z}",
        "TIME,0.001",
        f"NSUBST,{gravity_substeps}",
        *(["! 平均风静力预载", wind_mean_preload] if wind_mean_preload else []),
        "SOLVE",
        "",
        "TIMINT,ON",
        "OUTRES,ALL,ALL",
        "KBC,0",
        f"KES={damping_ratio}",
        "*IF,M,LE,0,THEN",
        "M=0.055317",
        "*ENDIF",
        "*IF,N,LE,0,THEN",
        "N=0.100449",
        "*ENDIF",
        "ALPHAD,2*KES*M*N*2*3.1416/(M+N)",
        "BETAD,KES/(M+N)/3.1416",
        f"ACEL,{gravity_x},{gravity_y},{gravity_z}",
        "AUTOTS,ON",
    ]
    if load_prelude_commands:
        commands.extend(
            [
                "! 瞬态荷载施加命令",
                load_prelude_commands,
            ]
        )
    commands.extend(
        [
            f"DTSTEP={dt}",
            f"NSTEPS={steps}",
            "DELTIM,DTSTEP",
        ]
    )
    if interpolate_tables and not requires_stepwise_sum and load_application_commands:
        commands.extend(
            [
                "AUTOTS,OFF",
                "DELTIM,DTSTEP,DTSTEP,DTSTEP",
                "TIME,NSTEPS*DTSTEP",
                "SOLVE",
            ]
        )
    elif step_load_commands:
        if load_row_setup_commands:
            commands.append(load_row_setup_commands)
        commands.extend(
            [
                "*DO,ISTEP,1,NSTEPS,1",
                "TIME,ISTEP*DTSTEP",
                "ALLSEL",
                "FDELE,ALL,ALL",
                step_load_commands,
                "ALLSEL",
                "SOLVE",
                "*ENDDO",
            ]
        )
    else:
        commands.extend(
            [
                "TIME,NSTEPS*DTSTEP",
                "SOLVE",
            ]
        )
    commands.extend(
        [
            "FINISH",
            "/POST1",
            "SET,LAST",
            "PRRSOL",
            "FINISH",
        ]
    )
    return "\n".join(commands)


def _split_stepwise_nodal_load_commands(commands: str) -> tuple[str, str, str]:
    prelude_lines: list[str] = []
    setup_lines: list[str] = []
    step_lines: list[str] = []
    table_row_vars: dict[str, str] = {}
    table_force_groups: dict[tuple[str, str], list[str]] = {}

    def row_var_for(table_name: str) -> str:
        if table_name not in table_row_vars:
            table_row_vars[table_name] = f"LOAD_ROWS_{len(table_row_vars) + 1:03d}"
        return table_row_vars[table_name]

    for line in commands.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("FCUM,"):
            continue
        table_force = _APDL_TABLE_FORCE_RE.match(stripped)
        if table_force:
            node_id, dof, table_name = (
                table_force.group(1).strip(),
                table_force.group(2).strip().upper(),
                table_force.group(3).strip(),
            )
            row_var_for(table_name)
            table_force_groups.setdefault((node_id, dof), []).append(table_name)
            continue
        if _APDL_FORCE_RE.match(stripped):
            step_lines.append(stripped)
            continue
        prelude_lines.append(line)

    for index, ((node_id, dof), table_names) in enumerate(table_force_groups.items(), start=1):
        step_lines.append("LOAD_ROW=ISTEP+1")
        for table_name in table_names:
            row_var = row_var_for(table_name)
            step_lines.extend(
                [
                    f"*IF,LOAD_ROW,GT,{row_var},THEN",
                    f"LOAD_ROW={row_var}",
                    "*ENDIF",
                ]
            )
        if len(table_names) == 1:
            step_lines.append(f"F,{node_id},{dof},{table_names[0]}(LOAD_ROW,1)")
            continue
        value_name = f"LOAD_VALUE_{index:03d}"
        value_expression = "+".join(f"{table_name}(LOAD_ROW,1)" for table_name in table_names)
        step_lines.extend(
            [
                f"{value_name}={value_expression}",
                f"F,{node_id},{dof},{value_name}",
            ]
        )

    for table_name, row_var in table_row_vars.items():
        setup_lines.append(f"*GET,{row_var},PARM,{table_name},DIM,1")

    prelude = "\n".join(line for line in prelude_lines if line.strip())
    setup = "\n".join(setup_lines)
    step = "\n".join(step_lines)
    return prelude, setup, step


def _ansys_earthquake_solver_execution_commands(
    context: dict[str, object],
    load_application_commands: str,
    dt: float,
    steps: int,
) -> str:
    other_load_application_commands = _remove_earthquake_table_acel(load_application_commands)
    damping_ratio = _ansys_float(context.get("damping_ratio"), 0.05)
    gravity_x = _ansys_float(context.get("gravity_accel_x"), 0.0)
    gravity_y = _ansys_float(context.get("gravity_accel_y"), -9.81)
    gravity_z = _ansys_float(context.get("gravity_accel_z"), 0.0)
    legacy_earthquake = _uses_legacy_earthquake_vread(context)

    commands = [
        "/SOLU",
        "ANTYPE,TRANS",
        "TRNOPT,FULL",
        "OUTRES,ALL,ALL",
        "OUTPR,ALL,NONE",
        "NLGEOM,ON",
    ]
    commands.extend(
        [
            "",
            "TIMINT,OFF",
            "KBC,1",
            f"ACEL,{gravity_x},{gravity_y},{gravity_z}",
            "TIME,0.001",
            "NSUBST,2,2,2",
            "SOLVE",
            "",
        ]
    )
    commands.extend(
        [
            "TIMINT,ON",
            "KBC,0",
            f"KES={damping_ratio}",
            "*IF,M,LE,0,THEN",
            "M=0.055317",
            "*ENDIF",
            "*IF,N,LE,0,THEN",
            "N=0.100449",
            "*ENDIF",
            "ALPHAD,2*KES*M*N*2*3.1416/(M+N)",
            "BETAD,KES/(M+N)/3.1416",
            "AUTOTS,ON",
        ]
    )
    if other_load_application_commands:
        commands.extend(
            [
                "! 瞬态荷载施加命令",
                other_load_application_commands,
            ]
        )
    if legacy_earthquake:
        scale = _ansys_float(context.get("earthquake_scale"), 1.0)
        commands.extend(
            [
                f"DT={dt}",
                "*GET, WAVE_LEN, PARM, ACEX, DIM, 1",
                "*DO ,i, 1, WAVE_LEN",
                "TIME,ACEX(i,1)+0.002",
                f"ACEL,ACEX(i,2)*{scale},{gravity_y},,",
                "NSUBST, 1",
                "SOLVE",
                "*ENDDO",
                "FINISH",
                "/POST1",
                "SET,LAST",
                "PRRSOL",
                "FINISH",
            ]
        )
        return "\n".join(commands)
    commands.extend(
        [
            f"DTSTEP={dt}",
            f"NSTEPS={steps}",
            "EQ_DYN_STEPS=EQ_ACC_TABLE_ROWS-1",
            "*IF,EQ_DYN_STEPS,GT,0,THEN",
            "NSTEPS=EQ_DYN_STEPS",
            "*ENDIF",
            "DELTIM,DTSTEP",
            "*DO,IACC,1,NSTEPS",
            "EQ_ROW=IACC",
            "*IF,EQ_ROW,GT,EQ_ACC_TABLE_ROWS,THEN",
            "EQ_ROW=EQ_ACC_TABLE_ROWS",
            "*ENDIF",
            "TIME,IACC*DTSTEP",
            f"ACEL,EQ_ACC_TABLE(EQ_ROW,1),{gravity_y},{gravity_z}",
            "NSUBST,1,1,1",
            "SOLVE",
            "*ENDDO",
            "FINISH",
            "/POST1",
            "SET,LAST",
            "PRRSOL",
            "FINISH",
        ]
    )
    return "\n".join(commands)


def _remove_earthquake_table_acel(commands: str) -> str:
    lines = []
    skip_comment = False
    for line in commands.splitlines():
        stripped = line.strip()
        if stripped.startswith("! earthquake uniform excitation table:"):
            skip_comment = True
            continue
        if stripped == "ACEL,%EQ_ACC_TABLE%,0,0":
            skip_comment = False
            continue
        if skip_comment and not stripped:
            skip_comment = False
            continue
        lines.append(line)
        skip_comment = False
    return "\n".join(line for line in lines if line.strip())


def _ansys_float(value: object, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _ansys_nsubst(value: object, default: str) -> str:
    text = str(default if value is None else value).strip()
    parts = [part.strip() for part in text.split(",")]
    if not 1 <= len(parts) <= 3 or any(not part.isdigit() or int(part) <= 0 for part in parts):
        raise ValueError(f"Invalid ANSYS gravity_substeps: {value}")
    return ",".join(parts)


def _ansys_product(left, right) -> str:
    left = 1.0 if left is None else left
    right = 1.0 if right is None else right
    try:
        return str(float(left) * float(right))
    except (TypeError, ValueError):
        return f"({left})*({right})"


def _apdl_factor(value: object) -> str:
    text = str(value).strip()
    if text.startswith("-"):
        return f"({text})"
    return text


def _table_row_count(path, dt, duration) -> int:
    try:
        source = Path(path)
    except TypeError:
        source = None
    if source is not None and source.exists() and source.is_file():
        rows = sum(
            1
            for line in source.read_text(encoding="utf-8", errors="ignore").splitlines()
            if line.strip() and not line.lstrip().startswith(("#", "!"))
        )
        if rows > 0:
            return rows
    try:
        if dt is not None and duration is not None and float(dt) > 0.0:
            return max(1, int(round(float(duration) / float(dt))) + 1)
    except (TypeError, ValueError):
        pass
    return 1


def _tread_file_args(path) -> tuple[str, str]:
    source = Path(str(path))
    ext = source.suffix.lstrip(".") or "txt"
    fname = source.with_suffix("").as_posix()
    return fname, ext


def _apdl_quoted(value: Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"
