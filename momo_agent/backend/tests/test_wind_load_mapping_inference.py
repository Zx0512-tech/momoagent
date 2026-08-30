"""逐节点风荷载表的多通道映射推断。

这里守的核心性质是"通道与节点的位置绑定"：标准荷载制品里不带节点号，
_apply_agent_standard_wind_load 是按 channel_1..channel_N 的顺序去对应目标集的
节点顺序的。所以列序必须按列名里的节点号重排到目标集登记顺序，且列集合与目标集
必须完全一致——否则就是"求解成功但荷载装错节点"，量纲上看不出来。

力单位另一条：N 与 kN 差 1000 倍且量级完全重叠，一个主梁节点上 800 N 与 800 kN
都讲得通，所以只认文件里写明的声明，绝不按量级猜。
"""

from __future__ import annotations

import pytest

from app.services.load_artifact_service import (
    BUNDLED_WIND_RELATIVE_PATH,
    BUNDLED_WIND_TARGET_SET_ID,
    REPO_ROOT,
)
from app.services.load_import_service import load_import_service
from app.services.load_mapping_inference import (
    DECISION_ASK,
    DECISION_AUTO,
    UNIT_SOURCE_COLUMN_NAME,
    UNIT_SOURCE_UNKNOWN,
    infer_wind_mapping,
)
from app.services.platform_store import AGENT_LOAD_TARGET_SETS, AGENT_WIND_FORCE_COMPONENT


TARGET_NODES = tuple(AGENT_LOAD_TARGET_SETS[BUNDLED_WIND_TARGET_SET_ID])


def _suggest(content: bytes, file_name: str = 'wind.csv'):
    inspection, rows = load_import_service.inspect(file_name, content)
    return infer_wind_mapping(
        inspection,
        rows,
        target_set_id=BUNDLED_WIND_TARGET_SET_ID,
        target_nodes=TARGET_NODES,
        component=AGENT_WIND_FORCE_COMPONENT,
        file_name=file_name,
    )


def _csv_for(columns: list[str], rows: int = 4) -> bytes:
    """造一张 time + 给定列名的等步长表，数值只要求全数值、不为零。"""

    head = ','.join(['time', *columns])
    body = [
        ','.join([
            f'{index * 0.01:.2f}',
            *[f'{100.0 + index + offset}' for offset in range(len(columns))],
        ])
        for index in range(rows)
    ]
    return ('\n'.join([head, *body]) + '\n').encode('utf-8')


def _columns_of(result) -> list[str]:
    return [channel['valueColumn'] for channel in (result.mapping.get('channels') or [])]


def test_bundled_wind_file_yields_one_channel_per_node() -> None:
    """内置 8 节点风文件：一节点一通道，列序即登记序，未声明单位所以要人工确认。"""

    source = REPO_ROOT / BUNDLED_WIND_RELATIVE_PATH
    result = _suggest(source.read_bytes(), source.name)
    channels = result.mapping.get('channels') or []

    assert len(channels) == len(TARGET_NODES)
    assert _columns_of(result) == [f'fy_node_{node}' for node in TARGET_NODES]
    assert result.mapping['loadKind'] == 'WIND'
    assert result.mapping['time']['column'] == 'time'
    assert channels == [
        {
            'valueColumn': f'fy_node_{node}',
            'applicationType': 'NODAL_FORCE',
            'targetType': 'NODE_GROUP',
            'targetId': BUNDLED_WIND_TARGET_SET_ID,
            'component': AGENT_WIND_FORCE_COMPONENT,
            'quantity': 'FORCE',
            # 文件里没写单位，量级区分不了 N 与 kN，只能留空让人来定。
            'sourceUnit': None,
            'scale': 1.0,
        }
        for node in TARGET_NODES
    ]
    assert result.unit_source == UNIT_SOURCE_UNKNOWN
    assert result.standardize_decision == DECISION_ASK
    assert result.alternatives['sourceUnits'] == ['N', 'kN']


def test_shuffled_columns_are_reordered_to_registration_order() -> None:
    """列序被打乱也必须重排回登记顺序——位置绑定全靠这一步。"""

    result = _suggest(_csv_for([f'fy_node_{node}' for node in sorted(TARGET_NODES)]))

    assert _columns_of(result) == [f'fy_node_{node}' for node in TARGET_NODES]


def test_missing_node_column_fails_closed() -> None:
    """少一列就判定不了通道与节点的对应关系，不出映射，并点名缺哪个节点。"""

    result = _suggest(_csv_for([f'fy_node_{node}' for node in TARGET_NODES[:-1]]))

    assert not result.mapping
    assert any('不一致' in warning for warning in result.warnings)
    assert any(str(TARGET_NODES[-1]) in warning for warning in result.warnings)


def test_unregistered_extra_node_column_fails_closed() -> None:
    """多一列未登记节点同样无法判定对应关系。"""

    result = _suggest(_csv_for([f'fy_node_{node}' for node in (*TARGET_NODES, 999)]))

    assert not result.mapping
    assert any('999' in warning for warning in result.warnings)


def test_uniform_column_name_unit_declaration_auto_passes() -> None:
    """列名里一致声明了单位，属于文件内容里的声明，够格自动标准化。"""

    result = _suggest(_csv_for([f'fy_node_{node}(N)' for node in TARGET_NODES]))
    channels = result.mapping.get('channels') or []

    assert len(channels) == len(TARGET_NODES)
    assert {channel['sourceUnit'] for channel in channels} == {'N'}
    assert result.unit_source == UNIT_SOURCE_COLUMN_NAME
    assert result.standardize_decision == DECISION_AUTO
    assert _columns_of(result) == [f'fy_node_{node}(N)' for node in TARGET_NODES]


def test_inconsistent_column_units_refuse_to_pick() -> None:
    """各列声明的单位不一致时不能选边——选错就给另一批列套上 1000 倍误差。"""

    mixed = (
        [f'fy_node_{node}(N)' for node in TARGET_NODES[:4]]
        + [f'fy_node_{node}(kN)' for node in TARGET_NODES[4:]]
    )
    result = _suggest(_csv_for(mixed))
    channels = result.mapping.get('channels') or []

    assert {channel['sourceUnit'] for channel in channels} == {None}
    assert result.standardize_decision == DECISION_ASK


def test_file_name_unit_declaration_is_not_enough_for_auto() -> None:
    """文件名不是文件内容：单位可以预填，但仍要人工确认。"""

    result = _suggest(_csv_for([f'fy_node_{node}' for node in TARGET_NODES]), 'wind_kN.csv')
    channels = result.mapping.get('channels') or []

    assert {channel['sourceUnit'] for channel in channels} == {'kN'}
    assert result.standardize_decision == DECISION_ASK


def test_non_per_node_table_is_left_to_manual_mapping() -> None:
    """两列地震表没有逐节点列形态，不硬凑映射，告警点名期望的列写法。"""

    result = _suggest(b'time,acc\n0,0.1\n0.01,0.2\n0.02,0.15\n')

    assert not result.mapping
    assert any('fy_node_' in warning for warning in result.warnings)


@pytest.mark.parametrize('suffix', ['_of_8', 'a'])
def test_ambiguous_node_suffix_is_not_read_as_node_id(suffix: str) -> None:
    """列名里的节点号必须是明确写法，宁可退回人工也不能把别的数字当节点号。"""

    result = _suggest(_csv_for([f'fy_node_{node}{suffix}' for node in TARGET_NODES]))

    assert not result.mapping
