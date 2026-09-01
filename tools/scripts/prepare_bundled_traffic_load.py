"""把 momo 随机车流稀疏时程转成平台内置的稠密节点力矩阵。

来源是 RandomTrafficLoadGeneration 的 traffic_base 算例（基准随机车流），
格式为稀疏长表 `time_s,node_id,fy_N,fy_kN`：只有当前时刻有车辆经过的节点
才出现一行。平台的荷载渲染需要稠密矩阵（每个时刻一行、每个受载节点一列），
无车时刻按 0 填充。

用法：
    python tools/scripts/prepare_bundled_traffic_load.py \
        --source D:/momo/.../stbridge_traffic_node_load_time_history.csv \
        --output analysis_data/traffic_inputs/traffic_random_base_3600s.csv
"""

from __future__ import annotations

import argparse
import csv
import json
from hashlib import sha256
from pathlib import Path


def parse_sparse_traffic(source: Path) -> tuple[list[float], list[int], dict[tuple[float, int], float]]:
    """读取稀疏长表，返回 (时刻列表, 节点列表, {(时刻, 节点): 力})。"""

    values: dict[tuple[float, int], float] = {}
    times: set[float] = set()
    nodes: set[int] = set()
    with source.open('r', encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle)
        required = {'time_s', 'node_id', 'fy_N'}
        missing = required - set(reader.fieldnames or ())
        if missing:
            raise ValueError(f'源文件缺少必需列: {sorted(missing)}')
        for row_number, row in enumerate(reader, start=2):
            try:
                time_s = float(row['time_s'])
                node_id = int(row['node_id'])
                fy_n = float(row['fy_N'])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f'第 {row_number} 行数值无效: {row}') from exc
            key = (time_s, node_id)
            # 同一时刻同一节点可能有多辆车叠加，累加而不是覆盖。
            values[key] = values.get(key, 0.0) + fy_n
            times.add(time_s)
            nodes.add(node_id)
    return sorted(times), sorted(nodes), values


def write_dense_matrix(
    output: Path,
    times: list[float],
    nodes: list[int],
    values: dict[tuple[float, int], float],
) -> None:
    """写出稠密矩阵：第一列 time_s，其后每个受载节点一列。"""

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.writer(handle, lineterminator='\n')
        writer.writerow(['time_s', *(f'node_{node}_fy_N' for node in nodes)])
        for time_s in times:
            writer.writerow([
                format(time_s, '.15g'),
                *(format(values.get((time_s, node), 0.0), '.15g') for node in nodes),
            ])


def verify_against_source(
    times: list[float],
    nodes: list[int],
    values: dict[tuple[float, int], float],
) -> dict[str, float]:
    """按时刻求和，用于与源算例的 max_abs_total_fy_N 对照。"""

    totals = [
        sum(values.get((time_s, node), 0.0) for node in nodes)
        for time_s in times
    ]
    return {
        'maxAbsTotalFyN': max(abs(total) for total in totals),
        'minTotalFyN': min(totals),
        'maxTotalFyN': max(totals),
        'sumAllFyN': sum(totals),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path, help='稀疏长表 CSV 路径')
    parser.add_argument('--output', required=True, type=Path, help='稠密矩阵输出路径')
    parser.add_argument(
        '--mapping-output',
        type=Path,
        default=None,
        help='逐节点 mapping JSON 输出路径（默认与 output 同目录同名 _mappings.json）',
    )
    parser.add_argument(
        '--expected-max-abs-total-fy-n',
        type=float,
        default=None,
        help='源算例 input_config.json 的 max_abs_total_fy_N，用于校验转换无损',
    )
    args = parser.parse_args()

    times, nodes, values = parse_sparse_traffic(args.source)
    if len(times) < 2:
        raise SystemExit('源文件至少需要两个时刻')
    dt = times[1] - times[0]
    tolerance = max(abs(dt), 1.0) * 1.0e-9
    non_uniform = [
        (previous, current)
        for previous, current in zip(times, times[1:])
        if abs((current - previous) - dt) > tolerance
    ]
    if non_uniform:
        raise SystemExit(f'源时间轴不是等步长，首个异常区间: {non_uniform[0]}')

    write_dense_matrix(args.output, times, nodes, values)
    stats = verify_against_source(times, nodes, values)

    if args.expected_max_abs_total_fy_n is not None:
        actual = stats['maxAbsTotalFyN']
        expected = args.expected_max_abs_total_fy_n
        if abs(actual - expected) > max(abs(expected), 1.0) * 1.0e-9:
            raise SystemExit(
                f'转换后峰值总力 {actual!r} 与源算例声明值 {expected!r} 不一致'
            )

    mapping_path = args.mapping_output or args.output.with_name(
        f'{args.output.stem}_mappings.json'
    )
    mappings = [
        {
            'fem_node_id': node,
            'dof': 'FY',
            'scale': 1.0,
            # 稠密矩阵第 1 列是 time_s，节点列从 1 开始（1-based 数据列号）。
            'source_column': index,
            'group': 'traffic',
            'label': f'traffic_{index}',
        }
        for index, node in enumerate(nodes, start=1)
    ]
    mapping_path.parent.mkdir(parents=True, exist_ok=True)
    mapping_path.write_text(
        json.dumps(mappings, ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8',
    )

    report = {
        'source': str(args.source),
        'sourceSha256': sha256(args.source.read_bytes()).hexdigest(),
        'output': str(args.output),
        'outputSha256': sha256(args.output.read_bytes()).hexdigest(),
        'mappingOutput': str(mapping_path),
        'mappingSha256': sha256(mapping_path.read_bytes()).hexdigest(),
        'timeStepS': dt,
        'durationS': times[-1] - times[0],
        'sampleCount': len(times),
        'nodeCount': len(nodes),
        'nodes': nodes,
        **stats,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
