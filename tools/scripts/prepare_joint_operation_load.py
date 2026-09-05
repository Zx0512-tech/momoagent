"""从随发布包的风、车流源数据生成 STbridge 联合运行输入。"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WIND_SOURCE = ROOT / "analysis_data" / "wind_inputs" / "wind_vertical_10mps_3600s.csv"
TRAFFIC_SOURCE = ROOT / "analysis_data" / "traffic_inputs" / "traffic_random_base_3600s.csv"
TRAFFIC_MAPPING_SOURCE = (
    ROOT / "analysis_data" / "traffic_inputs" / "traffic_random_base_3600s_mappings.json"
)
DEFAULT_OUTPUT_DIR = (
    ROOT / "output" / "operation_staged_inputs" / "operation_3600s_dt1_10mps_precombined"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _wind_nodes(count: int) -> list[int]:
    generator_path = ROOT / "analysis_data" / "wind_inputs" / "generate_wind_nodal_force.py"
    spec = importlib.util.spec_from_file_location("_wind_generator", generator_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载风荷载节点选择器：{generator_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return list(module.select_girder_nodes(count))


def _read_wind() -> tuple[list[float], list[list[float]]]:
    rows = list(csv.reader(WIND_SOURCE.open("r", encoding="utf-8-sig", newline="")))
    data = [[float(cell) for cell in row] for row in rows if row]
    if not data or len(data[0]) < 2:
        raise ValueError("风荷载源必须是 time + 至少一个节点列")
    width = len(data[0])
    if any(len(row) != width for row in data):
        raise ValueError("风荷载源存在不一致的列数")
    return [row[0] for row in data], [row[1:] for row in data]


def _read_traffic() -> tuple[list[str], list[float], list[list[float]]]:
    with TRAFFIC_SOURCE.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    if len(rows) < 2 or rows[0][0] != "time_s":
        raise ValueError("车流荷载源必须以 time_s 表头开始")
    header = rows[0]
    values = [[float(cell) for cell in row] for row in rows[1:] if row]
    if any(len(row) != len(header) for row in values):
        raise ValueError("车流荷载源存在不一致的列数")
    return header, [row[0] for row in values], [row[1:] for row in values]


def _validate_traffic_mapping(header: list[str]) -> list[int]:
    mappings = json.loads(TRAFFIC_MAPPING_SOURCE.read_text(encoding="utf-8"))
    nodes = [int(item["fem_node_id"]) for item in mappings]
    columns = [int(item["source_column"]) for item in mappings]
    expected_columns = list(range(1, len(header)))
    if columns != expected_columns:
        raise ValueError("车流 mapping 与源 CSV 列序不一致")
    if [f"node_{node}_fy_N" for node in nodes] != header[1:]:
        raise ValueError("车流 mapping 与源 CSV 节点列不一致")
    return nodes


def prepare(output_dir: Path, *, force: bool = False) -> dict[str, object]:
    output_csv = output_dir / "operation_wind_traffic_3600s.csv"
    output_mapping = output_dir / "operation_main_girder_mappings.json"
    output_summary = output_dir / "combined_operation_input_summary.json"
    outputs = (output_csv, output_mapping, output_summary)
    if not force and any(path.exists() for path in outputs):
        raise FileExistsError("联合运行输入已存在；如需重新生成，请显式传入 --force")

    wind_times, wind_values = _read_wind()
    traffic_header, traffic_times, traffic_values = _read_traffic()
    if len(wind_times) != len(traffic_times) or any(
        abs(left - right) > 1.0e-9 for left, right in zip(wind_times, traffic_times)
    ):
        raise ValueError("风、车流源必须具有相同的时间轴")
    wind_nodes = _wind_nodes(len(wind_values[0]))
    traffic_nodes = _validate_traffic_mapping(traffic_header)
    index_by_node = {node: index for index, node in enumerate(traffic_nodes)}
    if len(index_by_node) != len(traffic_nodes) or any(node not in index_by_node for node in wind_nodes):
        raise ValueError("风荷载节点必须是车流节点集的无重复子集")

    output_dir.mkdir(parents=True, exist_ok=True)
    with output_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(traffic_header)
        for time_value, traffic_row, wind_row in zip(wind_times, traffic_values, wind_values):
            combined = list(traffic_row)
            for node, value in zip(wind_nodes, wind_row):
                combined[index_by_node[node]] += value
            writer.writerow([format(time_value, ".12g"), *(format(value, ".12g") for value in combined)])

    mapping = [
        {
            "fem_node_id": node,
            "dof": "FY",
            "scale": 1.0,
            "source_column": index,
            "group": "operation",
            "label": f"operation_{node}",
        }
        for index, node in enumerate(traffic_nodes, start=1)
    ]
    output_mapping.write_text(json.dumps(mapping, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = {
        "kind": "STBRIDGE_PRECOMBINED_WIND_TRAFFIC",
        "timeStepS": wind_times[1] - wind_times[0],
        "sampleCount": len(wind_times),
        "durationS": wind_times[-1] - wind_times[0],
        "windNodes": wind_nodes,
        "trafficNodeCount": len(traffic_nodes),
        "source": {
            "wind": {"path": str(WIND_SOURCE.relative_to(ROOT)), "sha256": _sha256(WIND_SOURCE)},
            "traffic": {"path": str(TRAFFIC_SOURCE.relative_to(ROOT)), "sha256": _sha256(TRAFFIC_SOURCE)},
            "trafficMapping": {
                "path": str(TRAFFIC_MAPPING_SOURCE.relative_to(ROOT)),
                "sha256": _sha256(TRAFFIC_MAPPING_SOURCE),
            },
        },
        "outputs": {
            "csv": {"path": output_csv.name, "sha256": _sha256(output_csv)},
            "mapping": {"path": output_mapping.name, "sha256": _sha256(output_mapping)},
        },
    }
    output_summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="生成 STbridge 风+车流联合运行输入")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--force", action="store_true", help="允许覆盖已有输出")
    args = parser.parse_args()
    summary = prepare(args.output_dir, force=args.force)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
