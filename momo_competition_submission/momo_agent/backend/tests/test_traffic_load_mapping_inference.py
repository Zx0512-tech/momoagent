"""逐节点车流荷载表的列语义推断。

用真实随附文件（analysis_data/traffic_inputs/traffic_random_base_3600s.csv）作为
基准形态，再用构造样本覆盖列集合不一致、单位声明缺失/混用等退化情况。车流与风的
关键区别是绑定方式：风按 channel_i 位置对应目标集第 i 个节点，车流由 mapping 制品
逐条声明 {fem_node_id, source_column}，所以列序不参与语义。
"""

from __future__ import annotations

import csv
import io

import pytest

from app.services.load_artifact_service import (
    BUNDLED_TRAFFIC_RELATIVE_PATH,
    BUNDLED_TRAFFIC_TARGET_SET_ID,
    REPO_ROOT,
)
from app.services.load_import_service import load_import_service
from app.services.load_mapping_inference import (
    DECISION_ASK,
    DECISION_AUTO,
    UNIT_SOURCE_COLUMN_NAME,
    UNIT_SOURCE_FILE_NAME,
    UNIT_SOURCE_UNKNOWN,
    infer_traffic_mapping,
    traffic_matrix_columns,
)
from app.services.platform_store import AGENT_LOAD_TARGET_SETS, AGENT_TRAFFIC_FORCE_COMPONENT


TARGET_NODES = tuple(AGENT_LOAD_TARGET_SETS[BUNDLED_TRAFFIC_TARGET_SET_ID])


def _infer(file_name: str, content: bytes):
    inspection, rows = load_import_service.inspect(file_name, content)
    return infer_traffic_mapping(
        inspection,
        rows,
        target_set_id=BUNDLED_TRAFFIC_TARGET_SET_ID,
        target_nodes=TARGET_NODES,
        component=AGENT_TRAFFIC_FORCE_COMPONENT,
        file_name=file_name,
    )


def _matrix_csv(columns: list[str], rows: int = 4, *, time_column: str = 'time_s') -> bytes:
    """构造一份 time + 逐节点列的矩阵表。"""

    output = io.StringIO(newline='')
    writer = csv.writer(output, lineterminator='\n')
    writer.writerow([time_column, *columns])
    for index in range(rows):
        writer.writerow([f'{index * 0.5:.6g}', *[f'{100.0 + index + offset:.6g}' for offset in range(len(columns))]])
    return output.getvalue().encode('utf-8')


def _bundled_head(data_rows: int = 24) -> bytes:
    """真实随附车流文件的表头 + 前若干数据行（全量 3601 行没必要在单测里跑）。"""

    source = REPO_ROOT / BUNDLED_TRAFFIC_RELATIVE_PATH
    if not source.exists():
        pytest.skip('随附车流文件不存在')
    with source.open('r', encoding='utf-8-sig', newline='') as handle:
        lines = [next(handle) for _ in range(data_rows + 1)]
    return ''.join(lines).encode('utf-8')


def test_bundled_traffic_file_infers_single_matrix_channel():
    suggestion = _infer('traffic_random_base_3600s.csv', _bundled_head())

    mapping = suggestion.mapping
    assert mapping['loadKind'] == 'TRAFFIC'
    assert mapping['time']['column'] == 'time_s'
    # 车流是单通道稠密矩阵：逐节点信息在列里，不是 163 个通道。
    assert len(mapping['channels']) == 1
    channel = mapping['channels'][0]
    assert channel['applicationType'] == 'NODAL_FORCE_MATRIX'
    assert channel['quantity'] == 'FORCE'
    assert channel['component'] == AGENT_TRAFFIC_FORCE_COMPONENT
    assert channel['targetType'] == 'NODE_GROUP'
    assert channel['targetId'] == BUNDLED_TRAFFIC_TARGET_SET_ID
    assert channel['matrixColumnCount'] == len(TARGET_NODES) == 163
    # 列名 node_1_fy_N 自带单位声明，够 AUTO 资格。
    assert channel['sourceUnit'] == 'N'
    assert suggestion.unit_source == UNIT_SOURCE_COLUMN_NAME
    assert suggestion.standardize_decision == DECISION_AUTO
    assert suggestion.alternatives['sourceUnits'] == ['N', 'kN']


def test_bundled_traffic_matrix_columns_bijects_to_target_nodes():
    content = _bundled_head(4)
    header = content.decode('utf-8').splitlines()[0].split(',')

    pairs = traffic_matrix_columns(header, 'time_s')

    assert len(pairs) == len(TARGET_NODES)
    # 节点集合必须与登记目标集一致；顺序不参与语义（由 mapping 制品逐条声明）。
    assert sorted(node for node, _ in pairs) == sorted(TARGET_NODES)
    assert [name for _, name in pairs] == [f'node_{node}_fy_N' for node, _ in pairs]


def test_shuffled_column_order_still_infers_full_matrix():
    """列序打乱不影响推断：车流绑定是显式的，不靠列序。"""

    shuffled = list(reversed([f'node_{node}_fy_N' for node in TARGET_NODES]))
    suggestion = _infer('traffic_shuffled.csv', _matrix_csv(shuffled))

    channel = suggestion.mapping['channels'][0]
    assert channel['matrixColumnCount'] == len(TARGET_NODES)
    assert suggestion.standardize_decision == DECISION_AUTO
    pairs = traffic_matrix_columns([f'node_{node}_fy_N' for node in TARGET_NODES][::-1], None)
    assert sorted(node for node, _ in pairs) == sorted(TARGET_NODES)


def test_missing_node_column_refuses_to_infer():
    columns = [f'node_{node}_fy_N' for node in TARGET_NODES[:-1]]
    suggestion = _infer('traffic_missing.csv', _matrix_csv(columns))

    assert suggestion.mapping == {}
    dropped = TARGET_NODES[-1]
    assert any(str(dropped) in warning and '缺少节点' in warning for warning in suggestion.warnings)


def test_unregistered_node_column_refuses_to_infer():
    columns = [f'node_{node}_fy_N' for node in TARGET_NODES] + ['node_9999_fy_N']
    suggestion = _infer('traffic_extra.csv', _matrix_csv(columns))

    assert suggestion.mapping == {}
    assert any('9999' in warning and '未登记节点' in warning for warning in suggestion.warnings)


def test_kn_column_names_are_read_as_kn():
    columns = [f'node_{node}_fy_kN' for node in TARGET_NODES]
    suggestion = _infer('traffic_kn.csv', _matrix_csv(columns))

    channel = suggestion.mapping['channels'][0]
    assert channel['sourceUnit'] == 'kN'
    assert suggestion.unit_source == UNIT_SOURCE_COLUMN_NAME
    assert suggestion.standardize_decision == DECISION_AUTO


def test_mixed_units_across_columns_refuse_to_guess():
    """一部分列写 N、一部分写 kN：按哪个都会给另一批列套上 1000 倍误差。"""

    columns = [
        f'node_{node}_fy_kN' if index % 2 else f'node_{node}_fy_N'
        for index, node in enumerate(TARGET_NODES)
    ]
    suggestion = _infer('traffic_mixed.csv', _matrix_csv(columns))

    channel = suggestion.mapping['channels'][0]
    assert channel['sourceUnit'] is None
    assert suggestion.unit_source == UNIT_SOURCE_UNKNOWN
    assert suggestion.standardize_decision == DECISION_ASK
    assert any('1000 倍' in warning for warning in suggestion.warnings)


def test_undeclared_units_fall_back_to_file_name_but_still_ask():
    """文件名声明不够格自动放行：改个名就变，它描述标签不是内容。"""

    columns = [f'node_{node}_fy' for node in TARGET_NODES]
    suggestion = _infer('traffic_kN.csv', _matrix_csv(columns))

    channel = suggestion.mapping['channels'][0]
    assert channel['sourceUnit'] == 'kN'
    assert suggestion.unit_source == UNIT_SOURCE_FILE_NAME
    assert suggestion.standardize_decision == DECISION_ASK


def test_earthquake_two_column_table_yields_no_traffic_mapping():
    content = b'time_s,acc_g\n0,0.01\n0.02,0.03\n0.04,-0.02\n'
    suggestion = _infer('quake.csv', content)

    assert suggestion.mapping == {}
    assert any('node_' in warning for warning in suggestion.warnings)


def test_duplicate_node_column_refuses_to_biject():
    header = ['time_s', 'node_1_fy_N', 'node_1_fy_kN']

    assert traffic_matrix_columns(header, 'time_s') == []
