"""塔底剪力四截面求和、弯矩取峰值最大节点时程的回归测试。

剪力按 835--838 四个塔底截面在同一全局轴上求和；弯矩继续锁定已验证的
单塔截面 SMISC/MMOM 口径，避免镜像双塔弯矩相消。
"""

from __future__ import annotations

import pytest

from pyansys_bridge.core.postprocessor import (
    _peak_absolute_column,
    _tower_base_member_moment_resultants,
    _tower_base_peak_node_resultants,
    _tower_base_section_resultants,
)


def _mirror_rows(peak: float = 6.2e6, steps: int = 9) -> list[dict[str, float]]:
    """构造 835/836 与 837/838 反号的镜像塔脚时程，首行为基线。"""
    rows = []
    for step in range(steps):
        value = peak * step / (steps - 1)
        rows.append({
            'time': float(step),
            'Elem835_FY': -4.29e5 * step / (steps - 1),
            'Elem836_FY': -4.29e5 * step / (steps - 1),
            'Elem837_FY': -4.29e5 * step / (steps - 1),
            'Elem838_FY': -4.29e5 * step / (steps - 1),
            'Elem835_MZ': value,
            'Elem836_MZ': value,
            'Elem837_MZ': -value,
            'Elem838_MZ': -value,
        })
    return rows


def test_four_tower_base_section_shears_are_summed() -> None:
    rows = _mirror_rows()

    # 求和口径：MZ 跨四个塔脚精确抵消。
    summed = [
        sum(row[f'Elem{elem}_MZ'] - rows[0][f'Elem{elem}_MZ'] for elem in (835, 836, 837, 838))
        for row in rows
    ]
    assert max(abs(value) for value in summed) == 0.0

    resultants = _tower_base_section_resultants(rows)

    assert len(resultants) == len(rows)
    assert max(abs(item['tower_base_moment']) for item in resultants) == pytest.approx(6.2e6)
    peak_shear = max(abs(item['tower_base_shear']) for item in resultants)
    assert peak_shear == pytest.approx(4 * 4.29e5)


def test_four_section_shear_sums_each_axis_before_selecting_envelope() -> None:
    rows = [
        {
            'time': 0.0,
            'Elem835_FX': 1.0,
            'Elem836_FX': 2.0,
            'Elem837_FX': 3.0,
            'Elem838_FX': 4.0,
            'Elem835_FY': -1.0,
            'Elem836_FY': -2.0,
            'Elem837_FY': -3.0,
            'Elem838_FY': -4.0,
        },
        {
            'time': 1.0,
            'Elem835_FX': 11.0,
            'Elem836_FX': 22.0,
            'Elem837_FX': 33.0,
            'Elem838_FX': 44.0,
            'Elem835_FY': -6.0,
            'Elem836_FY': -12.0,
            'Elem837_FY': -18.0,
            'Elem838_FY': -24.0,
        },
    ]

    resultants = _tower_base_section_resultants(rows)

    assert resultants[0]['tower_base_shear'] == 0.0
    assert resultants[1]['tower_base_shear'] == pytest.approx(100.0)


def test_selected_series_is_one_node_history_relative_to_baseline() -> None:
    rows = _mirror_rows()
    resultants = _tower_base_section_resultants(rows)

    # 基线行必须归零，其余行等于所选单节点的基线相对时程。
    assert resultants[0] == {'tower_base_shear': 0.0, 'tower_base_moment': 0.0}
    column = _peak_absolute_column(
        rows,
        ['Elem835_MZ', 'Elem836_MZ', 'Elem837_MZ', 'Elem838_MZ'],
        rows[0],
    )
    assert [item['tower_base_moment'] for item in resultants] == [
        row[column] - rows[0][column] for row in rows
    ]


def test_tie_break_keeps_sign_deterministic_across_column_order() -> None:
    """镜像塔脚峰值只差浮点末位时，选列不能由字典顺序或噪声决定。"""
    rows = _mirror_rows()
    # 让 838 的峰值比 835 大出 1e-10 相对量级：仍属同一物理峰值。
    for row in rows:
        row['Elem838_MZ'] *= 1.0 + 1.0e-10

    selections = set()
    for order in ([835, 836, 837, 838], [838, 837, 836, 835], [837, 835, 838, 836]):
        shuffled = [
            {'time': row['time'], **{
                f'Elem{elem}_{comp}': row[f'Elem{elem}_{comp}']
                for elem in order for comp in ('FY', 'MZ')
            }}
            for row in rows
        ]
        moments = [item['tower_base_moment'] for item in _tower_base_section_resultants(shuffled)]
        selections.add((round(max(moments), 6), round(min(moments), 6)))

    assert len(selections) == 1


def test_member_moment_resultants_align_with_rows() -> None:
    rows = _mirror_rows()
    moments = _tower_base_member_moment_resultants(rows)

    assert len(moments) == len(rows)
    assert moments[0] == 0.0
    assert max(abs(value) for value in moments) == pytest.approx(6.2e6)


def test_degenerate_inputs_do_not_raise() -> None:
    assert _tower_base_section_resultants([]) == []
    assert _tower_base_member_moment_resultants([]) == []
    assert _peak_absolute_column([{'a': 1.0}], [], {'a': 0.0}) is None
    # 没有 FX/FY/MY/MZ 列时返回 0，而不是 KeyError。
    assert _tower_base_peak_node_resultants([{'time': 0.0}], prefix='Elem') == [
        {'tower_base_shear': 0.0, 'tower_base_moment': 0.0}
    ]


def test_baseline_index_is_clamped_into_range() -> None:
    rows = _mirror_rows()
    far = _tower_base_section_resultants(rows, baseline_index=10 ** 6)
    last = _tower_base_section_resultants(rows, baseline_index=len(rows) - 1)

    assert far == last
