"""DOE 批量完成计数落盘，以及返回顺序必须与 designs 一致。

顺序是硬约束：下游代理模型按位置把 DamperDOERecord 和 design 矩阵配对，
顺序错乱会静默产出错误拟合，不会报错。
"""

from __future__ import annotations

import numpy as np
import pytest

from pyansys_bridge.batch import doe_runner
from pyansys_bridge.core.progress_sink import read_batch_progress
from pyansys_bridge.models import BridgeModel, LoadCase


@pytest.fixture
def bridge_model() -> BridgeModel:
    return BridgeModel(name='fixture', source_path='fixture.txt')


@pytest.fixture
def load_cases() -> list[LoadCase]:
    return [LoadCase(name='earthquake', load_type='earthquake', scale=1.0)]


DESIGNS = np.array([
    [1000.0, 0.5],
    [2000.0, 0.6],
    [3000.0, 0.7],
    [4000.0, 0.8],
    [5000.0, 0.9],
])


@pytest.fixture
def recorded_counts(monkeypatch) -> list[tuple[int, int]]:
    """记录每次写入的 (completed, total)，因为文件只保留最后一次快照。"""

    seen: list[tuple[int, int]] = []
    original = doe_runner.write_batch_progress

    def spy(progress_dir, *, completed, total):
        seen.append((completed, total))
        original(progress_dir, completed=completed, total=total)

    monkeypatch.setattr(doe_runner, 'write_batch_progress', spy)
    return seen


def test_serial_reports_progress_and_keeps_order(
    tmp_path, bridge_model, load_cases, recorded_counts
) -> None:
    records = doe_runner.run_damper_doe_batch(
        bridge_model,
        load_cases,
        DESIGNS,
        output_dir=tmp_path,
        solver='mock',
        parallel_workers=1,
        progress_dir=tmp_path / 'progress',
    )

    assert recorded_counts == [(0, 5), (1, 5), (2, 5), (3, 5), (4, 5), (5, 5)]
    assert [record.design[0] for record in records] == [1000.0, 2000.0, 3000.0, 4000.0, 5000.0]
    assert read_batch_progress(tmp_path / 'progress') == {
        'completedCases': 5,
        'totalCases': 5,
        'percent': 100,
    }


def test_parallel_keeps_designs_order(tmp_path, bridge_model, load_cases) -> None:
    """即使完成顺序被打乱，返回顺序仍须与 designs 行序一致。"""

    records = doe_runner.run_damper_doe_batch(
        bridge_model,
        load_cases,
        DESIGNS,
        output_dir=tmp_path,
        solver='mock',
        parallel_workers=4,
        parallel_mode='thread',
    )

    assert [record.design[0] for record in records] == [1000.0, 2000.0, 3000.0, 4000.0, 5000.0]
    assert [record.design[1] for record in records] == [0.5, 0.6, 0.7, 0.8, 0.9]


def test_parallel_order_survives_shuffled_completion(
    tmp_path, bridge_model, load_cases, monkeypatch
) -> None:
    """显式让后提交的先完成，验证顺序不是碰巧对的。"""

    original = doe_runner._run_one_design

    def delayed(bridge, cases, design, **kwargs):
        # 第一行设计睡最久，保证它最后完成。
        import time
        time.sleep(0.05 if float(design[0]) == 1000.0 else 0.0)
        return original(bridge, cases, design, **kwargs)

    monkeypatch.setattr(doe_runner, '_run_one_design', delayed)

    records = doe_runner.run_damper_doe_batch(
        bridge_model,
        load_cases,
        DESIGNS,
        output_dir=tmp_path,
        solver='mock',
        parallel_workers=4,
        parallel_mode='thread',
    )

    assert [record.design[0] for record in records] == [1000.0, 2000.0, 3000.0, 4000.0, 5000.0]


def test_parallel_progress_counts_reach_total(
    tmp_path, bridge_model, load_cases, recorded_counts
) -> None:
    doe_runner.run_damper_doe_batch(
        bridge_model,
        load_cases,
        DESIGNS,
        output_dir=tmp_path,
        solver='mock',
        parallel_workers=4,
        parallel_mode='thread',
        progress_dir=tmp_path / 'progress',
    )

    # 并行下完成顺序不定，但计数必须是 0..5 递增且总数恒定。
    assert [done for done, _ in recorded_counts] == [0, 1, 2, 3, 4, 5]
    assert {total for _, total in recorded_counts} == {5}


def test_without_progress_dir_nothing_is_written(
    tmp_path, bridge_model, load_cases, recorded_counts
) -> None:
    """进度必须可关：不给 progress_dir 就一次也不写。"""

    records = doe_runner.run_damper_doe_batch(
        bridge_model,
        load_cases,
        DESIGNS,
        output_dir=tmp_path,
        solver='mock',
        parallel_workers=2,
        parallel_mode='thread',
    )

    assert len(records) == 5
    assert recorded_counts == []


def test_single_design_uses_serial_path(
    tmp_path, bridge_model, load_cases, recorded_counts
) -> None:
    records = doe_runner.run_damper_doe_batch(
        bridge_model,
        load_cases,
        DESIGNS[:1],
        output_dir=tmp_path,
        solver='mock',
        parallel_workers=4,
        progress_dir=tmp_path / 'progress',
    )

    assert len(records) == 1
    assert recorded_counts == [(0, 1), (1, 1)]


def test_progress_offset_combines_baseline_with_doe(tmp_path, bridge_model, load_cases, recorded_counts) -> None:
    doe_runner.run_damper_doe_batch(
        bridge_model,
        load_cases,
        DESIGNS,
        output_dir=tmp_path,
        solver='mock',
        parallel_workers=1,
        progress_dir=tmp_path / 'progress',
        progress_completed_offset=1,
        progress_total_cases=6,
    )

    assert recorded_counts == [(1, 6), (2, 6), (3, 6), (4, 6), (5, 6), (6, 6)]
