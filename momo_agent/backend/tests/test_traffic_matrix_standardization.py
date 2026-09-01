"""车流矩阵标准化：产物形态必须正好是执行侧要求的稠密矩阵 + 逐节点 mapping。

执行侧 platform_store._apply_agent_standard_traffic_load 有三条硬约束：
source_column 恰为 1..N（按 mapping 数组顺序）、node_columns[i] 必须等于
node_{mapped_nodes[i]}_fy_N、节点集合等于冻结目标集。这里逐条盯住。
"""

from __future__ import annotations

import csv
import io
import json

import pytest
from fastapi import HTTPException

from app.services.load_artifact_service import BUNDLED_TRAFFIC_TARGET_SET_ID
from app.services.load_import_service import load_import_service
from app.services.platform_store import AGENT_LOAD_TARGET_SETS, AGENT_TRAFFIC_FORCE_COMPONENT


TARGET_NODES = tuple(AGENT_LOAD_TARGET_SETS[BUNDLED_TRAFFIC_TARGET_SET_ID])


def _matrix_csv(columns: list[str], values: list[list[float]], *, dt: float = 0.5) -> bytes:
    output = io.StringIO(newline='')
    writer = csv.writer(output, lineterminator='\n')
    writer.writerow(['time_s', *columns])
    for index, row in enumerate(values):
        writer.writerow([f'{index * dt:.6g}', *[f'{value:.6g}' for value in row]])
    return output.getvalue().encode('utf-8')


def _mapping(source_unit: str = 'N', *, scale: float = 1.0, column_count: int | None = None) -> dict:
    return {
        'version': 2,
        'loadKind': 'TRAFFIC',
        'time': {'column': 'time_s', 'stepS': None, 'unit': 's'},
        'channels': [{
            'valueColumn': 'node_fy_N_matrix',
            'applicationType': 'NODAL_FORCE_MATRIX',
            'targetType': 'NODE_GROUP',
            'targetId': BUNDLED_TRAFFIC_TARGET_SET_ID,
            'component': AGENT_TRAFFIC_FORCE_COMPONENT,
            'quantity': 'FORCE',
            'sourceUnit': source_unit,
            'scale': scale,
            **({'matrixColumnCount': column_count} if column_count is not None else {}),
        }],
        'solver': 'ANSYS',
    }


def _three_nodes() -> tuple[list[str], list[list[float]]]:
    columns = ['node_1_fy_N', 'node_2_fy_N', 'node_3_fy_N']
    values = [[10.0, 20.0, 30.0], [11.0, 21.0, 31.0], [12.0, 22.0, 32.0]]
    return columns, values


def test_matrix_standardization_writes_dense_matrix_not_long_form():
    columns, values = _three_nodes()
    result = load_import_service.standardize(
        file_name='traffic.csv',
        content=_matrix_csv(columns, values),
        mapping=_mapping(),
    )

    lines = result.content.decode('utf-8').splitlines()
    assert lines[0] == 'time_s,node_1_fy_N,node_2_fy_N,node_3_fy_N'
    # 矩阵形态：一行一个时刻，不是长表的每通道每时刻一行。
    assert len(lines) == 1 + len(values)
    assert lines[1] == '0,10,20,30'
    assert result.report['applicationType'] == 'NODAL_FORCE_MATRIX'
    assert result.report['nodeCount'] == 3
    assert result.report['outputRowCount'] == 3
    assert result.report['standardUnit'] == 'N'
    assert result.report['distribution'] == 'PER_NODE_INDEPENDENT_TIME_HISTORY'


def test_point_mapping_biject_source_columns_one_based():
    columns, values = _three_nodes()
    result = load_import_service.standardize(
        file_name='traffic.csv',
        content=_matrix_csv(columns, values),
        mapping=_mapping(),
    )

    assert result.point_mapping is not None
    node_mappings = json.loads(result.point_mapping.decode('utf-8'))
    assert [item['source_column'] for item in node_mappings] == [1, 2, 3]
    assert [item['fem_node_id'] for item in node_mappings] == [1, 2, 3]
    assert all(item['dof'] == 'FY' for item in node_mappings)
    assert all(item['scale'] == 1.0 for item in node_mappings)
    # 执行侧逐对校验 node_columns[i] == f'node_{mapped_nodes[i]}_fy_N'
    header = result.content.decode('utf-8').splitlines()[0].split(',')[1:]
    assert header == [f'node_{item["fem_node_id"]}_fy_N' for item in node_mappings]
    assert result.report['pointMappingSha256']


def test_kn_source_is_converted_to_newton_and_column_renamed():
    """源文件写 kN：值 ×1000，列名统一改写成执行侧要求的 _fy_N 口径。"""

    columns = ['node_1_fy_kN', 'node_2_fy_kN']
    values = [[1.0, 2.0], [1.5, 2.5], [2.0, 3.0]]
    result = load_import_service.standardize(
        file_name='traffic_kn.csv',
        content=_matrix_csv(columns, values),
        mapping=_mapping('kN'),
    )

    lines = result.content.decode('utf-8').splitlines()
    assert lines[0] == 'time_s,node_1_fy_N,node_2_fy_N'
    assert lines[1] == '0,1000,2000'
    assert lines[2] == '0.5,1500,2500'
    assert result.report['conversionFactor'] == 1000.0
    assert result.report['sourceUnit'] == 'kN'
    assert result.report['standardUnit'] == 'N'
    assert result.report['sourceColumns'] == ['node_1_fy_kN', 'node_2_fy_kN']


def test_scale_is_applied_on_top_of_unit_conversion():
    columns, values = _three_nodes()
    result = load_import_service.standardize(
        file_name='traffic.csv',
        content=_matrix_csv(columns, values),
        mapping=_mapping('kN', scale=2.0),
    )

    assert result.content.decode('utf-8').splitlines()[1] == '0,20000,40000,60000'
    assert result.report['scale'] == 2.0


def test_digest_is_stable_across_repeated_standardization():
    """两次 standardize()（算 expectedSha256 与执行时校验）必须同值，否则审批必然失败。"""

    columns, values = _three_nodes()
    content = _matrix_csv(columns, values)

    first = load_import_service.standardize(file_name='t.csv', content=content, mapping=_mapping())
    second = load_import_service.standardize(file_name='t.csv', content=content, mapping=_mapping())

    assert first.digest == second.digest
    assert first.point_mapping == second.point_mapping


def test_column_order_follows_source_file_and_mapping_tracks_it():
    """列序按源文件原样，mapping 跟着走——绑定是显式的，不靠列序。"""

    columns = ['node_3_fy_N', 'node_1_fy_N', 'node_2_fy_N']
    values = [[30.0, 10.0, 20.0], [31.0, 11.0, 21.0], [32.0, 12.0, 22.0]]
    result = load_import_service.standardize(
        file_name='traffic.csv',
        content=_matrix_csv(columns, values),
        mapping=_mapping(),
    )

    lines = result.content.decode('utf-8').splitlines()
    assert lines[0] == 'time_s,node_3_fy_N,node_1_fy_N,node_2_fy_N'
    assert lines[1] == '0,30,10,20'
    node_mappings = json.loads(result.point_mapping.decode('utf-8'))
    assert [item['fem_node_id'] for item in node_mappings] == [3, 1, 2]
    assert [item['source_column'] for item in node_mappings] == [1, 2, 3]


def test_declared_column_count_mismatch_fails_closed():
    columns, values = _three_nodes()

    with pytest.raises(HTTPException) as excinfo:
        load_import_service.standardize(
            file_name='traffic.csv',
            content=_matrix_csv(columns, values),
            mapping=_mapping(column_count=163),
        )

    assert excinfo.value.detail['code'] == 'MATRIX_COLUMN_COUNT_MISMATCH'


def test_no_node_columns_fails_closed():
    content = b'time_s,acc_g\n0,0.01\n0.5,0.02\n1,0.03\n'

    with pytest.raises(HTTPException) as excinfo:
        load_import_service.standardize(file_name='q.csv', content=content, mapping=_mapping())

    assert excinfo.value.detail['code'] == 'UNKNOWN_MATRIX_COLUMNS'


def test_duplicate_node_column_fails_closed():
    columns = ['node_1_fy_N', 'node_1_fy_kN']
    values = [[10.0, 20.0], [11.0, 21.0]]

    with pytest.raises(HTTPException) as excinfo:
        load_import_service.standardize(
            file_name='traffic.csv',
            content=_matrix_csv(columns, values),
            mapping=_mapping(),
        )

    assert excinfo.value.detail['code'] == 'UNKNOWN_MATRIX_COLUMNS'


def test_full_target_set_matrix_standardizes_end_to_end():
    """163 个登记节点的完整矩阵：产物应当能通过执行侧的三条双射校验。"""

    columns = [f'node_{node}_fy_N' for node in TARGET_NODES]
    values = [[float(node) for node in TARGET_NODES] for _ in range(3)]
    result = load_import_service.standardize(
        file_name='traffic_full.csv',
        content=_matrix_csv(columns, values),
        mapping=_mapping(column_count=len(TARGET_NODES)),
    )

    node_mappings = json.loads(result.point_mapping.decode('utf-8'))
    header = result.content.decode('utf-8').splitlines()[0].split(',')
    node_columns = header[1:]

    assert header[0] == 'time_s'
    assert len(node_mappings) == len(node_columns) == len(TARGET_NODES)
    mapped_nodes = [item['fem_node_id'] for item in node_mappings]
    assert [item['source_column'] for item in node_mappings] == list(range(1, len(node_columns) + 1))
    assert all(
        column == f'node_{node}_fy_N'
        for node, column in zip(mapped_nodes, node_columns)
    )
    assert sorted(mapped_nodes) == sorted(TARGET_NODES)
