"""APDL load application rendering for unified time-history loads."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from pyansys_bridge.models import TimeHistoryLoad


def render_load_application(load: TimeHistoryLoad, table_name: str | None = None) -> str:
    """Render solver commands that apply one time-history load."""

    if load.kind == "earthquake":
        effective_table_name = str(table_name or load.application.get("table_name") or "EQ_ACC_TABLE")
        return (
            f"! earthquake uniform excitation table: {effective_table_name}\n"
            f"ACEL,%{effective_table_name}%,0,0"
        )
    if load.application.get("type") == "stbridge_traffic_macro":
        return _render_traffic_macro(load)
    return _render_nodal_loads(load, table_name)


def render_load_application_commands(loads: Iterable[TimeHistoryLoad]) -> str:
    """Render APDL load application commands for all loads."""

    load_list = list(loads)
    if _nodal_load_block_count(load_list) > 1:
        nodal_block = _render_combined_nodal_loads(load_list)
        earthquake_blocks = [
            render_load_application(load)
            for load in load_list
            if load.kind == "earthquake" or load.application.get("type") == "stbridge_traffic_macro"
        ]
        return "\n".join(block for block in (*earthquake_blocks, nodal_block) if block)
    rendered = [render_load_application(load) for load in load_list]
    blocks = [block for block in rendered if block]
    return "\n".join(blocks)


def _render_combined_nodal_loads(loads: list[TimeHistoryLoad]) -> str:
    lines = ["! combined nodal time-history loads", "FCUM,ADD"]
    for load in loads:
        if load.kind == "earthquake" or load.application.get("type") == "stbridge_traffic_macro":
            continue
        for point in load.application.get("load_points", ()):
            table_name = point.get("table_name") or load.application.get("table_name")
            if table_name is None:
                raise ValueError(f"{load.kind} load point is missing table_name")
            lines.append(f"F,{point['node_id']},{str(point['dof']).upper()},%{table_name}%")
    lines.append("FCUM,REPL")
    return "\n".join(lines)


def _nodal_load_block_count(loads: Iterable[TimeHistoryLoad]) -> int:
    return sum(
        1
        for load in loads
        if load.kind != "earthquake"
        and load.application.get("type") != "stbridge_traffic_macro"
        and load.application.get("load_points")
    )


def _render_nodal_loads(load: TimeHistoryLoad, table_name: str | None) -> str:
    lines = [f"! {load.kind} nodal time-history loads"]
    for point in load.application.get("load_points", ()):
        point_table_name = point.get("table_name") or table_name or load.application.get("table_name")
        if point_table_name is None:
            raise ValueError(f"{load.kind} load point is missing table_name")
        lines.append(f"F,{point['node_id']},{point['dof']},%{point_table_name}%")
    return "\n".join(lines)


def _render_traffic_macro(load: TimeHistoryLoad) -> str:
    source_path = load.metadata.get("source_path")
    if not source_path:
        raise ValueError("ANSYS traffic macro loading requires stbridge_random_traffic_load.mac")
    source = Path(str(source_path))
    solution_mode = str(load.application.get("solution_mode") or "transient")
    scale = load.metadata.get("scale")
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


def _apdl_quoted(value: Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"
