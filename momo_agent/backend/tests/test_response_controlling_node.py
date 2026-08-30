"""响应通道取控制节点自身时程（而非跨节点包络）的回归测试。

原实现对 response_nodes 逐时间步取 max-abs 包络，得到的曲线在任何一个真实节点
上都没发生过。峰值标量不受影响（max 可交换），但累计位移是路径依赖量：在包络列
上累加会同时把控制权切换处的跳变计入、把落选节点的行程整段丢掉，两者互相抵消
且净方向不定，所以既不保守也不可比。这里用两个交替控制的节点锁定新口径。
"""

from __future__ import annotations

import pytest

from pyansys_bridge.core.postprocessor import _controlling_response_node
from pyansys_bridge.core.result_summary import (
    _cumulative_displacement_objective,
    objectives_from_timeseries,
)


def _alternating_series() -> dict[str, list[float]]:
    """两个节点交替占据峰值：包络列会在每次切换处产生真实节点没有的跳变。"""
    return {
        'time': [0.0, 1.0, 2.0, 3.0, 4.0],
        # 节点 36 峰值 0.30，行程 0.30+0.30+0.10+0.10 = 0.80
        36: [0.0, 0.30, 0.0, 0.10, 0.0],
        # 节点 107 峰值 0.40，行程 0.10+0.10+0.40+0.40 = 1.00
        107: [0.0, -0.10, 0.0, -0.40, 0.0],
    }


def _envelope(series: dict[str, list[float]], nodes: tuple[int, ...]) -> list[float]:
    return [
        max((series[node][index] for node in nodes), key=abs)
        for index in range(len(series['time']))
    ]


def test_controlling_node_is_peak_of_own_history() -> None:
    series = _alternating_series()

    assert _controlling_response_node({36: series[36], 107: series[107]}) == 107


def test_peak_objective_is_unchanged_by_the_reorder() -> None:
    """max 可交换：逐节点求峰再比最大，与逐步包络后求峰完全相同。"""
    series = _alternating_series()
    nodes = (36, 107)

    envelope_peak = max(abs(value) for value in _envelope(series, nodes))
    per_node_peak = max(max(abs(value) for value in series[node]) for node in nodes)

    assert envelope_peak == pytest.approx(per_node_peak)
    assert per_node_peak == pytest.approx(0.40)


def test_cumulative_displacement_no_longer_accumulates_on_the_envelope() -> None:
    """包络列的路径长度既不等于任何真实节点，也不是候选中的最大者。"""
    series = _alternating_series()
    nodes = (36, 107)
    node = _controlling_response_node({node: series[node] for node in nodes})

    controlling = _cumulative_displacement_objective({
        'time': series['time'],
        'displacement': series[node],
    })
    enveloped = _cumulative_displacement_objective({
        'time': series['time'],
        'displacement': _envelope(series, nodes),
    })

    assert controlling == pytest.approx(1.00)
    # 包络列把 36 的行程也算了进去，虚高到真实控制节点的 1.4 倍。
    assert enveloped == pytest.approx(1.40)
    assert enveloped > controlling


def test_objectives_come_from_the_same_node() -> None:
    """峰值与累计位移同源：两者都在控制节点这一条曲线上取。"""
    series = _alternating_series()
    node = _controlling_response_node({36: series[36], 107: series[107]})

    objectives = objectives_from_timeseries({
        'time': series['time'],
        'displacement': series[node],
    })

    assert objectives['max_displacement'] == pytest.approx(0.40)
    assert objectives['cumulative_displacement'] == pytest.approx(1.00)


def test_tie_break_is_deterministic_across_node_order() -> None:
    """镜像对称节点的峰值只差浮点末位时，选中的节点不能由噪声或插入顺序决定。"""
    series = _alternating_series()
    # 让 36 的峰值与 107 只差 1e-12 相对量级：仍属同一物理峰值。
    series[36] = [value * (0.40 / 0.30) * (1.0 - 1.0e-12) for value in series[36]]

    selections = {
        _controlling_response_node({node: series[node] for node in order})
        for order in ((36, 107), (107, 36))
    }

    assert selections == {36}


def test_single_candidate_node_keeps_its_own_history() -> None:
    """风/车流优化链把 response_nodes 收窄成单节点，此时口径必须与旧实现一致。"""
    series = _alternating_series()

    assert _controlling_response_node({107: series[107]}) == 107
    assert _cumulative_displacement_objective({
        'time': series['time'],
        'displacement': series[107],
    }) == pytest.approx(1.00)


def test_empty_candidate_set_is_rejected() -> None:
    with pytest.raises(ValueError):
        _controlling_response_node({})
    with pytest.raises(ValueError):
        _controlling_response_node({36: []})


def test_cumulative_displacement_requires_aligned_samples() -> None:
    with pytest.raises(ValueError):
        _cumulative_displacement_objective({
            'time': [0.0, 1.0, 2.0],
            'displacement': [0.0, 1.0],
        })
