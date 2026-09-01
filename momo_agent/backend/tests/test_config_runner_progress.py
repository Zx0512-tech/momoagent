"""progress_dir 只能注入声明了 case_step_progress 的 solver。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pyansys_bridge.core.solver_factory import SolverFactory
from pyansys_bridge.models import AnalysisResult
from pyansys_bridge.optimization.config_runner import (
    _progress_dir,
    _with_case_step_progress,
    run_solver_acceptance_case_config,
    run_undamped_baseline_config,
)


def test_openseespy_inproc_declares_case_step_progress() -> None:
    capability = SolverFactory.capability('openseespy_inproc')
    assert 'case_step_progress' in capability['supported_features']


def test_ansys_declares_output_probe_case_step_progress() -> None:
    capability = SolverFactory.capability('ansys')
    assert 'case_step_progress' in capability['supported_features']
    assert 'ansys_output_time_probe' in capability['supported_features']
    assert 'case_step_progress' not in capability['not_implemented']


def test_injects_into_capable_solver() -> None:
    kwargs = _with_case_step_progress(
        'openseespy_inproc',
        {'execution_mode': 'run'},
        Path('output/run/_progress'),
    )

    assert kwargs == {
        'execution_mode': 'run',
        'progress_dir': Path('output/run/_progress'),
    }


def test_injects_into_ansys_output_probe() -> None:
    original = {'execution_mode': 'run'}

    kwargs = _with_case_step_progress('ansys', original, Path('output/run/_progress'))

    assert kwargs == {
        'execution_mode': 'run',
        'progress_dir': Path('output/run/_progress'),
    }


def test_capable_solver_actually_accepts_the_injected_kwarg(tmp_path: Path) -> None:
    """光看 capability 不够，构造一次确认参数真的收得下。"""

    kwargs = _with_case_step_progress(
        'openseespy_inproc',
        {'output_dir': tmp_path},
        tmp_path / '_progress',
    )

    solver = SolverFactory.create('openseespy_inproc', **kwargs)

    assert solver.progress_dir == tmp_path / '_progress'


def test_ansys_accepts_the_injected_kwarg(tmp_path: Path) -> None:
    solver = SolverFactory.create('ansys', progress_dir=tmp_path)

    assert solver.progress_dir == tmp_path


def test_no_progress_dir_leaves_kwargs_untouched() -> None:
    original = {'execution_mode': 'run'}

    assert _with_case_step_progress('openseespy_inproc', original, None) is original
    assert _with_case_step_progress('ansys', None, None) is None


def test_progress_dir_config_key_is_optional() -> None:
    assert _progress_dir({}, Path('.')) is None


def test_progress_dir_resolves_relative_to_config_dir(tmp_path: Path) -> None:
    resolved = _progress_dir({'progress_dir': '_progress'}, tmp_path)

    assert resolved == tmp_path / '_progress'


def _write_single_case_config(tmp_path: Path, *, solver_type: str, progress: bool) -> Path:
    """一个能跑通配置解析的最小单算例配置（不真的求解）。"""

    model_path = tmp_path / 'model.txt'
    model_path.write_text('* fixture model\n', encoding='utf-8')
    config: dict = {
        'bridge_model': {'name': 'stbridge', 'source_path': str(model_path)},
        'load_case': {'name': 'earthquake', 'load_type': 'earthquake', 'scale': 1.0},
        'solver': {'type': solver_type},
        'summary_path': str(tmp_path / 'summary.json'),
    }
    if progress:
        config['progress_dir'] = str(tmp_path / '_progress')
    config_path = tmp_path / 'single_case.json'
    config_path.write_text(json.dumps(config), encoding='utf-8')
    return config_path


class _SpyStore:
    def save(self, result) -> None:
        return None


@pytest.fixture
def solver_kwargs_seen(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """记录每次 solver 构造 kwargs，但仍用真实 solver 类。

    不能整体替换 SolverFactory.create：能力门自己要靠 ``create(name)`` 查
    capability，替换掉之后测的就不是真实能力声明了。这里只拦截构造记录参数，
    再把 run_analysis 换成不启动求解器的桩。
    """

    seen: list[dict] = []
    original = SolverFactory.create

    def spy_create(name, **kwargs):
        seen.append(dict(kwargs))
        solver = original(name, **kwargs)
        solver.run_analysis = lambda *_args, **_kwargs: AnalysisResult(
            case_id='spy_case', solver=str(name), status='completed'
        )
        return solver

    monkeypatch.setattr(SolverFactory, 'create', staticmethod(spy_create))
    return seen


def test_single_baseline_case_receives_progress_dir(tmp_path: Path, solver_kwargs_seen) -> None:
    """单次计算也要有进度：基线分析必须把 progress_dir 传给 solver。"""

    config_path = _write_single_case_config(tmp_path, solver_type='openseespy_inproc', progress=True)

    run_undamped_baseline_config(config_path, use_cache=False)

    assert solver_kwargs_seen[-1]['progress_dir'] == tmp_path / '_progress'


def test_single_baseline_case_without_progress_dir_is_unchanged(tmp_path: Path, solver_kwargs_seen) -> None:
    """没配 progress_dir 时不能凭空注入，保持原有行为。"""

    config_path = _write_single_case_config(tmp_path, solver_type='openseespy_inproc', progress=False)

    run_undamped_baseline_config(config_path, use_cache=False)

    assert 'progress_dir' not in solver_kwargs_seen[-1]


def test_single_baseline_case_injects_ansys_output_probe(tmp_path: Path, solver_kwargs_seen) -> None:
    """ANSYS 单次分析通过 ansys.out 探针接收同一进度目录。"""

    config_path = _write_single_case_config(tmp_path, solver_type='ansys', progress=True)

    run_undamped_baseline_config(config_path, use_cache=False)

    assert solver_kwargs_seen[-1]['progress_dir'] == tmp_path / '_progress'


class _SpyBatchAnalyzer:
    seen_kwargs: dict = {}

    def __init__(self, *, solver, output_dir, solver_kwargs) -> None:
        type(self).seen_kwargs = dict(solver_kwargs or {})
        self.store = _SpyStore()

    def run_combinations(self, bridge_model, params, load_cases) -> list[AnalysisResult]:
        return [AnalysisResult(case_id='spy_case', solver='spy', status='completed')]


def test_acceptance_case_receives_progress_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """阻尼器对比走的单算例通道同样要上报进度。"""

    _SpyBatchAnalyzer.seen_kwargs = {}
    monkeypatch.setattr('pyansys_bridge.batch.BatchAnalyzer', _SpyBatchAnalyzer)
    config_path = _write_single_case_config(tmp_path, solver_type='openseespy_inproc', progress=True)

    run_solver_acceptance_case_config(config_path, omit_dampers=True)

    assert _SpyBatchAnalyzer.seen_kwargs['progress_dir'] == tmp_path / '_progress'


def test_acceptance_case_without_progress_dir_is_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _SpyBatchAnalyzer.seen_kwargs = {}
    monkeypatch.setattr('pyansys_bridge.batch.BatchAnalyzer', _SpyBatchAnalyzer)
    config_path = _write_single_case_config(tmp_path, solver_type='openseespy_inproc', progress=False)

    run_solver_acceptance_case_config(config_path, omit_dampers=True)

    assert 'progress_dir' not in _SpyBatchAnalyzer.seen_kwargs
