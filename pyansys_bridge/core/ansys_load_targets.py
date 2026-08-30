"""ANSYS operation-load target helpers."""

from __future__ import annotations

from pathlib import Path

from pyansys_bridge.models import LoadPointMapping

DEFAULT_STBRIDGE_WIND_GIRDER_NODES = (1, 23, 43, 61, 132, 114, 94, 72)
DEFAULT_STBRIDGE_WIND_NORTH_TOWER_NODES = (588, 587, 586, 585)
DEFAULT_STBRIDGE_WIND_SOUTH_TOWER_NODES = (596, 595, 594, 593)
DEFAULT_STBRIDGE_TRAFFIC_LOAD_NODES = DEFAULT_STBRIDGE_WIND_GIRDER_NODES
# 随机车流是移动荷载：每个甲板节点有独立的 fy 时程，不能像风那样把总力等权
# 摊到少数节点上。这 163 个节点取自 momo 已跑算例
# RandomTrafficLoadGeneration/.../traffic_base/stbridge_traffic_node_load_time_history.csv
# 的实际受载节点集（主梁 1-141 + 横梁 551-554 + 吊点 567-584），全部存在于
# STbridge APDL 模型与 OpenSees builder。上面的 8 节点别名只是 operation 联合
# 工况的回退默认值，承担总 |fy| 不到 5%，不能用于真实车流分析。
DEFAULT_STBRIDGE_RANDOM_TRAFFIC_DECK_NODES = (
    *range(1, 142),
    *range(551, 555),
    *range(567, 585),
)
STBRIDGE_TRAFFIC_MACRO_APPLICATIONS = {
    "STBRIDGE_SINGLE_ROAD_TRANSIENT_MACRO",
    "STBRIDGE_SINGLE_ROAD_QUASI_STATIC_MACRO",
}


def build_operation_load_targets(model_metadata: dict[str, object], load_context: dict[str, object] | None) -> dict[str, object]:
    wind_points = _wind_load_points(model_metadata)
    traffic_points = _traffic_load_points(model_metadata)
    application = traffic_application(load_context)
    return {
        "ansys_wind_load_points": wind_points,
        "ansys_traffic_load_points": traffic_points,
        "ansys_wind_load_component": model_metadata.get("wind_load_component"),
        "ansys_traffic_load_component": model_metadata.get("traffic_load_component"),
        "ansys_traffic_load_application": application,
        "ansys_traffic_macro_path": traffic_macro_path(load_context),
    }


def operation_load_target_metadata(
    targets: dict[str, object],
    load_context: dict[str, object] | None,
) -> dict[str, object]:
    metadata = {
        "wind_load_distribution": "equal_weight_over_discrete_nodes",
        "wind_load_points": _metadata_load_points(targets["ansys_wind_load_points"]),
        "traffic_load_application": targets["ansys_traffic_load_application"],
        "traffic_load_distribution": traffic_load_distribution_label(targets["ansys_traffic_load_application"]),
        "traffic_load_points": (
            []
            if targets["ansys_traffic_load_application"] in STBRIDGE_TRAFFIC_MACRO_APPLICATIONS
            else _metadata_load_points(targets["ansys_traffic_load_points"])
        ),
    }
    if targets["ansys_traffic_load_application"] in STBRIDGE_TRAFFIC_MACRO_APPLICATIONS:
        metadata["traffic_macro_path"] = targets["ansys_traffic_macro_path"]
        metadata["traffic_macro_contract"] = {
            "single_road": True,
            "direction_mode": "bidirectional",
            "solution_mode": traffic_macro_solution_mode(load_context),
            "load_axis": "FY",
            "vertical_direction": "negative FY is downward",
            "node_distribution": "generated centerline nodes with longitudinal interpolation",
            "response_nodes": [1],
        }
    return metadata


def traffic_application(context: dict[str, object] | None) -> str:
    if not context or "traffic_name" not in context:
        return "NONE"
    path = context.get("traffic_path")
    if path is not None and Path(str(path)).suffix.lower() == ".mac":
        solution_mode = traffic_macro_solution_mode(context)
        if solution_mode == "quasi-static":
            return "STBRIDGE_SINGLE_ROAD_QUASI_STATIC_MACRO"
        return "STBRIDGE_SINGLE_ROAD_TRANSIENT_MACRO"
    return "DISTRIBUTED_VECTOR_TABLE"


def traffic_macro_path(context: dict[str, object] | None) -> str | None:
    if traffic_application(context) not in STBRIDGE_TRAFFIC_MACRO_APPLICATIONS:
        return None
    return str(context.get("traffic_path"))


def traffic_load_distribution_label(application: object) -> str:
    if application == "STBRIDGE_SINGLE_ROAD_TRANSIENT_MACRO":
        return "single_road_centerline_longitudinal_interpolation_transient_generated_macro"
    if application == "STBRIDGE_SINGLE_ROAD_QUASI_STATIC_MACRO":
        return "single_road_centerline_longitudinal_interpolation_generated_macro"
    return "equal_weight_over_discrete_nodes"


def traffic_macro_solution_mode(context: dict[str, object] | None) -> str:
    if not context:
        return "transient"
    path = context.get("traffic_path")
    if path is None:
        return "transient"
    source = Path(str(path))
    source_text = ""
    if source.exists() and source.is_file():
        source_text = source.read_text(encoding="utf-8", errors="ignore")[:20000].upper()
    lowered_path = str(source).lower()
    if "quasi-static" in lowered_path or "QUASI-STATIC" in source_text:
        return "quasi-static"
    if "transient" in lowered_path or "ANTYPE,TRANS" in source_text or "TRNOPT,FULL" in source_text:
        return "transient"
    return "transient"


def is_zero(value) -> bool:
    try:
        return float(value) == 0.0
    except (TypeError, ValueError):
        return False


def _wind_load_points(model_metadata: dict[str, object]) -> tuple[dict[str, object], ...]:
    mapped = _mapped_load_points(model_metadata, "wind")
    if mapped:
        return mapped
    groups = (
        (
            "girder",
            _int_tuple(
                model_metadata.get(
                    "wind_girder_load_nodes",
                    model_metadata.get("wind_girder_nodes", DEFAULT_STBRIDGE_WIND_GIRDER_NODES),
                )
            ),
        ),
    )
    return _equal_weight_points(groups)


def _traffic_load_points(model_metadata: dict[str, object]) -> tuple[dict[str, object], ...]:
    mapped = _mapped_load_points(model_metadata, "traffic")
    if mapped:
        return mapped
    nodes = _int_tuple(model_metadata.get("traffic_load_nodes", DEFAULT_STBRIDGE_TRAFFIC_LOAD_NODES))
    return _equal_weight_points((("traffic", nodes),))


def _mapped_load_points(model_metadata: dict[str, object], load_kind: str) -> tuple[dict[str, object], ...]:
    records = list(model_metadata.get(f"{load_kind}_load_mappings") or ())
    records.extend(model_metadata.get(f"{load_kind}_load_point_mappings") or ())
    for record in model_metadata.get("load_point_mappings") or ():
        kind = str(record.get("kind") or record.get("load_kind") or "")
        if kind == load_kind:
            records.append(record)
    points = []
    for record in records:
        payload = dict(record)
        payload.setdefault("group", load_kind)
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


def _equal_weight_points(groups: tuple[tuple[str, tuple[int, ...]], ...]) -> tuple[dict[str, object], ...]:
    total = sum(len(nodes) for _, nodes in groups)
    if total <= 0:
        return ()
    weight = 1.0 / total
    points = []
    for group, nodes in groups:
        for group_index, node_id in enumerate(nodes, start=1):
            points.append(
                {
                    "group": group,
                    "label": f"{group}_{group_index}",
                    "node_id": int(node_id),
                    "weight": weight,
                }
            )
    return tuple(points)


def _metadata_load_points(points) -> list[dict[str, object]]:
    metadata = []
    for point in points:
        item = {
            "group": str(point["group"]),
            "label": str(point["label"]),
            "node_id": int(point["node_id"]),
            "weight": float(point["weight"]),
        }
        if "dof" in point:
            item["dof"] = str(point["dof"])
        if "source_column" in point:
            item["source_column"] = int(point["source_column"])
        for key in ("target_x_m", "actual_x_m", "tributary_length_m", "mean_force_N"):
            if key in point:
                item[key] = float(point[key])
        if "dynamic_data_column" in point:
            item["dynamic_data_column"] = int(point["dynamic_data_column"])
        metadata.append(item)
    return metadata


def _int_tuple(value) -> tuple[int, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        items = [item.strip() for item in value.split(",")]
    else:
        items = list(value)
    return tuple(int(item) for item in items if str(item).strip())
