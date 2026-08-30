"""生成 STbridge 主梁竖向风荷载时程（逐节点独立，非总力等权分配）。

旧默认记录 wind_vertical_10mps_3600s.csv 没有留下任何生成参数，只能靠统计量
反推。本脚本把参数写在代码里，后续调参直接改这里重跑即可。

节点数由 --nodes 指定（默认 8，即 agent 默认风工况记录）。节点集按"沿桥长等距取
N 个站位、各自吸附到最近主梁节点"选取，节点坐标从 APDL 模型现场解析，不另抄一份
几何。N=8 的结果与后端常量 DEFAULT_STBRIDGE_WIND_GIRDER_NODES 完全一致，脚本内
有断言守住这个一致性。

节点与列的对应：列顺序严格跟随选出的节点集，该集按主梁 X 坐标单调排列
（-1044 → +1044 m），因此第 i 个空间点（linspace 沿桥长）正好落在第 i 个节点上，
相干矩阵刻画的空间相关性与真实节点间距一致。

承载长度取桥长/N，故各节点力之和覆盖的桥长与 N 无关，但空间分辨率会变（N=8 间距
261 m，N=16 间距 130 m）。实测合力脉动标准差随 N 下降并收敛：N=2 为 1.27e6 N、
N=8 为 8.18e5 N、N=16 为 6.94e5 N、N=32 起稳定在 6.77e5 N。节点少时等于把一整段
承载长度当成完全相干，高估了合力脉动；N 增大才把跨内部分相干解析出来。这是真实的
物理效应，脚本不做归一化，但换 N 重跑基线时要预期荷载合力量级随之变化。

两处刻意与旧记录保持一致的选择：
- enforceCodeMinimumWindSpeed=False：规范最低风速会把 u10=10 m/s 夹到 24.5 m/s，
  荷载量级差一个数量级。旧记录（文件名 10mps，实测列 std 162 kN）显然按不夹取
  生成，这里沿用以免默认工况的荷载量级突变。真正做规范校核时应打开。
- 逐列去均值：linearized 荷载模型的 fv 含静风分量（本参数下约 2929 N/m），旧记录
  是纯脉动（实测列均值 ~1e-8）。引入恒定上举会把结构推到新的静力平衡位置，可能
  改变基线可行性判定，不适合在"先顶一顶"的默认记录里顺带引入。

用法：
    python analysis_data/wind_inputs/generate_wind_nodal_force.py
    python analysis_data/wind_inputs/generate_wind_nodal_force.py --nodes 12
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve()
SUBMISSION_ROOT = HERE.parents[2]
sys.path.insert(0, str(SUBMISSION_ROOT))
sys.path.insert(0, str(SUBMISSION_ROOT / 'momo_agent' / 'backend'))

from app.api.schemas import WindRequest  # noqa: E402
from app.services.workflow import run_full_workflow  # noqa: E402
from pyansys_bridge.core.ansys_load_targets import (  # noqa: E402
    DEFAULT_STBRIDGE_WIND_GIRDER_NODES,
)

# 主梁两端节点 1 / 72 的 X 坐标为 -1044 / +1044 m（STbridge_apdl_model.txt）。
BRIDGE_LENGTH_M = 2088.0
# duration 取 3601 而非 3600：time_axis 是 arange(0, duration, dt)，要得到
# 0..3600 s 共 3601 个采样点（与旧记录行数一致）必须多给一个步长。
DURATION_S = 3601.0
TIME_STEP_S = 1.0
SEED = 20260824
OUTPUT_NAME_TEMPLATE = 'wind_vertical_{count}nodes_10mps_3600s.csv'

APDL_MODEL_PATH = SUBMISSION_ROOT / 'bridge_models' / 'stbridge_ansys' / 'STbridge_apdl_model.txt'
APDL_PARSER_PATH = (
    SUBMISSION_ROOT / 'bridge_models' / 'stbridge_opensees' / 'parse_ansys_to_opensees.py'
)
# 主梁节点 id 为 1..141（STbridge_apdl_model.txt 中四个 *do 循环生成，z 全为 0）。
GIRDER_NODE_ID_MAX = 141
# WindRequest.girder.segmentCount 下限为 2。
MIN_NODE_COUNT = 2


def _load_apdl_parser():
    """按文件路径加载 APDL 解析器。

    不把 bridge_models/stbridge_opensees 加进 sys.path：该目录下有 openseespy
    垫片包和 sitecustomize.py，入 path 会遮蔽真实运行时。
    """
    spec = importlib.util.spec_from_file_location('_stbridge_apdl_parser', APDL_PARSER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _girder_nodes_ordered_by_x() -> list[tuple[int, float]]:
    """解析 APDL 模型，按 X 坐标升序返回主梁 (node_id, x)。"""
    nodes = _load_apdl_parser().parse_ansys(APDL_MODEL_PATH)[0]
    girder = [
        (node_id, coords[0])
        for node_id, coords in nodes.items()
        if node_id <= GIRDER_NODE_ID_MAX
    ]
    return sorted(girder, key=lambda item: item[1])


def select_girder_nodes(count: int) -> list[int]:
    """沿桥长等距取 count 个站位，各自吸附到最近的主梁节点。"""
    ordered = _girder_nodes_ordered_by_x()
    stations = np.linspace(ordered[0][1], ordered[-1][1], count)
    # 等距离并列时取节点号较小者，保证选取结果可复现。
    chosen = [
        min(ordered, key=lambda item: (abs(item[1] - station), item[0]))[0]
        for station in stations
    ]
    if len(set(chosen)) != count:
        raise SystemExit(
            f'--nodes {count} 超出主梁节点分辨率（{len(ordered)} 个节点），出现重复吸附'
        )
    return chosen


def build_request(point_count: int) -> WindRequest:
    return WindRequest.model_validate({
        'project': {
            'name': f'STbridge 主梁 {point_count} 节点竖向风荷载',
            'note': 'agent 默认风工况记录',
        },
        'site': {
            'u10': 10.0,
            'riskCoefficient': 1.0,
            'surfaceClass': 'B',
            'terrainCoefficient': 1.0,
            'airDensity': 1.25,
            'girderReferenceHeight': 50.0,
            'towerHeights': [10.0, 100.0, 200.0, 300.0],
            'enforceCodeMinimumWindSpeed': False,
        },
        'girder': {
            'length': BRIDGE_LENGTH_M,
            'segmentCount': point_count,
            'width': 35.0,
            'depth': 4.5,
            'ch': 1.2,
            'cv': 0.8,
            'cm': 0.15,
        },
        'tower': {'height': 300.0, 'segmentCount': 6, 'width': 8.0, 'cd': 1.1},
        'timeHistory': {
            'duration': DURATION_S,
            'timeStep': TIME_STEP_S,
            'frequencyCount': 1024,
            'seed': SEED,
            'spectrumModel': 'davenport',
            'verticalSpectrumModel': 'panofsky',
            'simulationMethod': 'harmonic',
            'arOrder': 4,
            'loadModel': 'linearized',
        },
        'overrides': {'enabled': False},
    })


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='生成 STbridge 主梁竖向风荷载时程（逐节点独立力）。',
    )
    parser.add_argument(
        '--nodes',
        type=int,
        default=len(DEFAULT_STBRIDGE_WIND_GIRDER_NODES),
        metavar='N',
        help='主梁加载节点数，沿桥长等距取点（默认 %(default)s）',
    )
    args = parser.parse_args(argv)
    if args.nodes < MIN_NODE_COUNT:
        parser.error(f'--nodes 至少为 {MIN_NODE_COUNT}')
    return args


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    nodes = select_girder_nodes(args.nodes)
    if args.nodes == len(DEFAULT_STBRIDGE_WIND_GIRDER_NODES):
        # 默认记录必须与后端常量逐位一致，否则列名与 target set 对不上。
        assert nodes == list(DEFAULT_STBRIDGE_WIND_GIRDER_NODES), nodes
    output_path = HERE.with_name(OUTPUT_NAME_TEMPLATE.format(count=len(nodes)))

    result = run_full_workflow(build_request(len(nodes)))
    girder = result['pointHistories']['girder']
    # fv 是竖向线荷载 N/m，乘承载长度得节点集中力。均分承载长度使 N 段之和
    # 恰好等于桥长，总竖向力与空间分辨率无关。
    tributary_length_m = BRIDGE_LENGTH_M / len(nodes)
    forces = np.asarray(girder['fv'], dtype=float) * tributary_length_m
    forces -= forces.mean(axis=1, keepdims=True)

    times = np.arange(forces.shape[1], dtype=float) * TIME_STEP_S
    header = ','.join(['time'] + [f'fy_node_{node}' for node in nodes])
    lines = [header]
    for index, time_value in enumerate(times):
        cells = [format(time_value, '.12g')]
        cells.extend(format(value, '.12g') for value in forces[:, index])
        lines.append(','.join(cells))
    output_path.write_text('\n'.join(lines) + '\n', encoding='utf-8', newline='\n')

    print(f'wrote {output_path.relative_to(SUBMISSION_ROOT).as_posix()}')
    print(f'  nodes={nodes}')
    print(f'  points={len(girder["points"])} samples={forces.shape[1]} dt={TIME_STEP_S} s')
    print(f'  tributary={tributary_length_m:.1f} m')
    print(f'  per-node std={forces.std(axis=1).mean():.4g} N  total std={forces.sum(axis=0).std():.4g} N')


if __name__ == '__main__':
    main()
