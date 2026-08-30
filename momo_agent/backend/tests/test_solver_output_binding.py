from __future__ import annotations

from pathlib import Path

import pytest

from app.services.platform_store import platform_store


@pytest.mark.parametrize('solver', ['ANSYS', 'OPENSEESPY_INPROC'])
def test_earthquake_baseline_binds_solver_outputs_to_current_run(
    solver: str,
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / 'templates'
    run_dir = tmp_path / 'real_workflows' / 'run_1'
    config_dir.mkdir()
    config = {
        'bridge_model': {'name': 'STbridge', 'source_path': 'model.txt'},
        'load_case': {'name': 'earthquake', 'load_type': 'earthquake', 'path': 'load.txt'},
        'solver': (
            'ansys'
            if solver == 'ANSYS'
            else {'type': 'openseespy_inproc', 'model_path': 'model.py'}
        ),
        'solver_kwargs': {'execution_mode': 'run'} if solver == 'ANSYS' else {},
    }

    prepared = platform_store._earthquake_baseline_config(
        config,
        config_dir,
        run_dir,
        solver,
    )

    solver_options = prepared['solver_kwargs'] if solver == 'ANSYS' else prepared['solver']
    assert Path(solver_options['output_dir']) == run_dir / 'solver_outputs'
    assert 'output_dir' not in (config.get('solver_kwargs') or {})
    if isinstance(config['solver'], dict):
        assert 'output_dir' not in config['solver']


@pytest.mark.parametrize(('load_kind', 'expected_name'), [
    ('EARTHQUAKE', 'undamped_earthquake_summary.json'),
    ('WIND', 'undamped_wind_summary.json'),
])
def test_undamped_baseline_summary_is_named_after_the_load_kind(
    load_kind: str,
    expected_name: str,
    tmp_path: Path,
) -> None:
    """风工况的无控基线结论不应写进名为 earthquake 的文件。

    与 OPTIMIZATION_OVERVIEW_ARTIFACT_NAMES 同一口径：概览已按工况分名，
    基线摘要此前无条件写 undamped_earthquake_summary.json，下游读取方和
    证据审查会把风的基线当成地震的。
    """
    prepared = platform_store._earthquake_baseline_config(
        {'load_case': {'name': 'wind', 'load_type': 'wind'}, 'solver_kwargs': {}},
        tmp_path,
        tmp_path / 'run',
        'ANSYS',
        load_kind,
    )

    assert Path(prepared['summary_path']).name == expected_name


def test_undamped_baseline_summary_defaults_to_earthquake_name(tmp_path: Path) -> None:
    """省略 load_kind 时保持地震命名，未迁移的调用点行为不变。"""
    prepared = platform_store._earthquake_baseline_config(
        {'load_case': {'name': 'earthquake', 'load_type': 'earthquake'}, 'solver_kwargs': {}},
        tmp_path,
        tmp_path / 'run',
        'ANSYS',
    )

    assert Path(prepared['summary_path']).name == 'undamped_earthquake_summary.json'
