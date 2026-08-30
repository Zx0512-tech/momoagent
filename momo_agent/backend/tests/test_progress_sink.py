"""进度文件读写的正确性与健壮性。"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pyansys_bridge.core.progress_sink import (
    clear_progress,
    refresh_ansys_output_probes,
    read_all_progress,
    read_batch_progress,
    register_ansys_output_probe,
    safe_progress_filename,
    write_batch_progress,
    write_batch_component_progress,
    write_case_progress,
)


def test_write_then_read_roundtrip(tmp_path: Path) -> None:
    write_case_progress(tmp_path, 'case_c7600_alpha08', step=250, total_steps=500)

    entries = read_all_progress(tmp_path)
    assert len(entries) == 1
    assert entries[0]['caseId'] == 'case_c7600_alpha08'
    assert entries[0]['step'] == 250
    assert entries[0]['totalSteps'] == 500
    assert entries[0]['percent'] == 50


def test_case_id_with_unsafe_characters_is_sanitized(tmp_path: Path) -> None:
    """真实 case_id 含 = 和 . 等字符，不能直接做文件名。"""

    write_case_progress(tmp_path, 'stbridge|c=7600,alpha=0.8', step=1, total_steps=10)

    files = list(tmp_path.glob('*.json'))
    assert len(files) == 1
    # 原始 caseId 必须保留在内容里，只有文件名被规范化。
    assert read_all_progress(tmp_path)[0]['caseId'] == 'stbridge|c=7600,alpha=0.8'


def test_safe_filename_never_empty() -> None:
    assert safe_progress_filename('///').endswith('.json')
    assert safe_progress_filename('') == 'case.json'


def test_repeated_writes_overwrite_same_file(tmp_path: Path) -> None:
    for step in (10, 20, 30):
        write_case_progress(tmp_path, 'case_a', step=step, total_steps=100)

    entries = read_all_progress(tmp_path)
    assert len(entries) == 1
    assert entries[0]['step'] == 30


def test_multiple_cases_are_all_reported(tmp_path: Path) -> None:
    write_case_progress(tmp_path, 'case_a', step=80, total_steps=100)
    write_case_progress(tmp_path, 'case_b', step=50, total_steps=100)
    write_case_progress(tmp_path, 'case_c', step=20, total_steps=100)

    entries = read_all_progress(tmp_path)
    assert [item['caseId'] for item in entries] == ['case_a', 'case_b', 'case_c']
    assert [item['percent'] for item in entries] == [80, 50, 20]


def test_concurrent_writes_from_threads_do_not_corrupt(tmp_path: Path) -> None:
    """模拟并行 case 同时写入；每个文件都必须是完整合法 JSON。"""

    def write(index: int) -> None:
        for step in range(0, 100, 5):
            write_case_progress(tmp_path, f'case_{index}', step=step, total_steps=100)

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(write, range(8)))

    entries = read_all_progress(tmp_path)
    assert len(entries) == 8
    for path in tmp_path.glob('*.json'):
        json.loads(path.read_text(encoding='utf-8'))


def test_missing_directory_returns_empty(tmp_path: Path) -> None:
    assert read_all_progress(tmp_path / 'nope') == []


def test_corrupt_file_is_skipped(tmp_path: Path) -> None:
    write_case_progress(tmp_path, 'good_case', step=5, total_steps=10)
    (tmp_path / 'broken.json').write_text('{not json', encoding='utf-8')

    entries = read_all_progress(tmp_path)
    assert [item['caseId'] for item in entries] == ['good_case']


def test_write_failure_is_swallowed(tmp_path: Path) -> None:
    """进度目录不可写时不能让求解崩掉。"""

    blocker = tmp_path / 'blocked'
    blocker.write_text('I am a file, not a directory', encoding='utf-8')

    write_case_progress(blocker, 'case_a', step=1, total_steps=10)  # 不抛异常即通过
    assert read_all_progress(blocker) == []


def test_percent_edge_cases(tmp_path: Path) -> None:
    write_case_progress(tmp_path, 'zero_total', step=5, total_steps=0)
    assert read_all_progress(tmp_path)[0]['percent'] == 0

    clear_progress(tmp_path)
    write_case_progress(tmp_path, 'overshoot', step=120, total_steps=100)
    assert read_all_progress(tmp_path)[0]['percent'] == 100


def test_clear_progress_empties_directory(tmp_path: Path) -> None:
    write_case_progress(tmp_path, 'case_a', step=1, total_steps=10)
    write_case_progress(tmp_path, 'case_b', step=2, total_steps=10)

    clear_progress(tmp_path)

    assert read_all_progress(tmp_path) == []


def test_batch_progress_roundtrip(tmp_path: Path) -> None:
    write_batch_progress(tmp_path, completed=8, total=15)

    assert read_batch_progress(tmp_path) == {
        'completedCases': 8,
        'totalCases': 15,
        'percent': 53,
    }


def test_batch_component_progress_aggregates_parallel_stages(tmp_path: Path) -> None:
    write_batch_component_progress(tmp_path, component='baseline', completed=0, total=1)
    write_batch_component_progress(tmp_path, component='doe', completed=4, total=15)
    assert read_batch_progress(tmp_path) == {
        'completedCases': 4,
        'totalCases': 16,
        'percent': 25,
        'components': ['baseline', 'doe'],
    }
    write_batch_component_progress(tmp_path, component='baseline', completed=1, total=1)
    assert read_batch_progress(tmp_path)['completedCases'] == 5


def test_batch_progress_missing_returns_none(tmp_path: Path) -> None:
    assert read_batch_progress(tmp_path) is None
    assert read_batch_progress(tmp_path / 'nope') is None


def test_batch_progress_corrupt_returns_none(tmp_path: Path) -> None:
    write_batch_progress(tmp_path, completed=1, total=2)
    (tmp_path / '_batch.json').write_text('{ truncated', encoding='utf-8')

    assert read_batch_progress(tmp_path) is None


def test_batch_summary_is_not_mistaken_for_a_case(tmp_path: Path) -> None:
    """summary 和 per-case 文件同目录，读取方不能把它当成一个算例。"""

    write_batch_progress(tmp_path, completed=1, total=3)
    write_case_progress(tmp_path, 'case_a', step=5, total_steps=10)

    entries = read_all_progress(tmp_path)
    assert [entry['caseId'] for entry in entries] == ['case_a']


def test_batch_progress_survives_repeated_writes(tmp_path: Path) -> None:
    for completed in range(4):
        write_batch_progress(tmp_path, completed=completed, total=3)

    assert read_batch_progress(tmp_path)['completedCases'] == 3


def test_batch_progress_unwritable_dir_is_swallowed(tmp_path: Path) -> None:
    """进度写失败不能中断求解。"""

    blocker = tmp_path / 'blocked'
    blocker.write_text('not a directory', encoding='utf-8')

    write_batch_progress(blocker, completed=1, total=2)

    assert read_batch_progress(blocker) is None


def test_ansys_output_probe_converts_latest_time_to_case_progress(tmp_path: Path) -> None:
    output = tmp_path / 'solver' / 'ansys.out'
    output.parent.mkdir()
    register_ansys_output_probe(
        tmp_path / 'progress',
        'ansys_case',
        output_path=output,
        dt=0.02,
        duration=10.0,
    )
    output.write_text(
        'TIME= 0.001\nLOAD STEP 1 COMPLETED\nTIME = 2.5000\nTIME=5.0000\n',
        encoding='utf-8',
    )

    refresh_ansys_output_probes(tmp_path / 'progress')

    assert read_all_progress(tmp_path / 'progress') == [{
        'caseId': 'ansys_case',
        'step': 250,
        'totalSteps': 500,
        'percent': 50,
        'phase': 'transient',
    }]


def test_ansys_output_probe_ignores_unrecognised_or_partial_output(tmp_path: Path) -> None:
    output = tmp_path / 'ansys.out'
    register_ansys_output_probe(
        tmp_path / 'progress',
        'ansys_case',
        output_path=output,
        dt=0.02,
        duration=10.0,
    )
    output.write_text('TIME=IACC*DTSTEP\nTIME=', encoding='utf-8')

    refresh_ansys_output_probes(tmp_path / 'progress')

    assert read_all_progress(tmp_path / 'progress') == []


def test_ansys_output_probe_invalid_observation_config_is_a_noop(tmp_path: Path) -> None:
    register_ansys_output_probe(
        tmp_path / 'progress',
        'ansys_case',
        output_path=tmp_path / 'ansys.out',
        dt=float('nan'),
        duration=10.0,
    )

    assert list((tmp_path / 'progress').glob('*')) == []
