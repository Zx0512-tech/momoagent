"""solver 层 progress_dir 透传：必须不污染算例指纹与归档元数据。"""

from __future__ import annotations

from pathlib import Path

import pytest

from pyansys_bridge.core.openseespy_inproc_solver import OpenSeesPyInProcSolver
from pyansys_bridge.core.ansys_solver import AnsysSolver
from pyansys_bridge.core.progress_sink import refresh_ansys_output_probes, read_all_progress
from pyansys_bridge.models import BridgeModel, DamperParams, LoadCase


@pytest.fixture
def bridge_model() -> BridgeModel:
    return BridgeModel(name='stbridge', source_path='fixture.txt', metadata={'modal_modes': 20})


@pytest.fixture
def load_case(tmp_path: Path) -> LoadCase:
    record = tmp_path / 'eq.txt'
    record.write_text('0.0\n0.01\n0.02\n', encoding='utf-8')
    return LoadCase(
        name='EQ-01',
        load_type='earthquake',
        path=str(record),
        dt=0.02,
        duration=10.0,
        scale=1.0,
        direction={'x': 1.0, 'y': 0.0, 'z': 0.0},
    )


@pytest.fixture
def damper_params() -> DamperParams:
    return DamperParams(c=7600.0, alpha=0.8)


def _configured(solver: OpenSeesPyInProcSolver, bridge_model, load_case, damper_params):
    solver.prepare_model(bridge_model)
    solver.apply_load_case(load_case)
    solver.set_damper_params(damper_params)
    return solver


def test_case_fingerprint_ignores_progress_dir(tmp_path, bridge_model, load_case, damper_params) -> None:
    """开关进度不能改变算例指纹，否则缓存复用和幂等会失效。"""

    without = _configured(OpenSeesPyInProcSolver(), bridge_model, load_case, damper_params)
    with_progress = _configured(
        OpenSeesPyInProcSolver(progress_dir=tmp_path / 'progress'),
        bridge_model,
        load_case,
        damper_params,
    )

    assert without.case_fingerprint() == with_progress.case_fingerprint()


def test_design_metadata_ignores_progress_dir(tmp_path, bridge_model, load_case, damper_params) -> None:
    """归档元数据不能出现 progress_dir，否则同一算例的归档会因观测配置而不同。"""

    without = _configured(OpenSeesPyInProcSolver(), bridge_model, load_case, damper_params)
    with_progress = _configured(
        OpenSeesPyInProcSolver(progress_dir=tmp_path / 'progress'),
        bridge_model,
        load_case,
        damper_params,
    )

    assert without.design_metadata() == with_progress.design_metadata()
    assert 'progress' not in repr(with_progress.design_metadata()).lower()


def test_command_context_without_progress_dir_has_no_progress_keys(
    bridge_model, load_case, damper_params
) -> None:
    solver = _configured(OpenSeesPyInProcSolver(), bridge_model, load_case, damper_params)

    context = solver._command_context()

    assert 'progress_path' not in context
    assert 'progress_case_id' not in context
    assert 'progress_filename' not in context


def test_command_context_with_progress_dir_injects_keys(
    tmp_path, bridge_model, load_case, damper_params
) -> None:
    progress_dir = tmp_path / 'progress'
    solver = _configured(
        OpenSeesPyInProcSolver(progress_dir=progress_dir),
        bridge_model,
        load_case,
        damper_params,
    )

    context = solver._command_context()

    assert context['progress_path'] == progress_dir.as_posix()
    expected_case_id = solver.build_case_id(bridge_model, load_case, damper_params)
    assert context['progress_case_id'] == expected_case_id
    # 文件名必须是安全形式（case_id 含 = 和 . 等字符）。
    assert context['progress_filename'].endswith('.json')
    assert '=' not in context['progress_filename']


def test_command_context_uses_business_progress_case_id(
    tmp_path, bridge_model, load_case, damper_params
) -> None:
    """批量任务向用户展示业务 caseId，不能暴露求解器内部指纹。"""
    without = _configured(OpenSeesPyInProcSolver(), bridge_model, load_case, damper_params)
    solver = _configured(
        OpenSeesPyInProcSolver(
            progress_dir=tmp_path / 'progress',
            progress_case_id='viscous_c7600',
        ),
        bridge_model,
        load_case,
        damper_params,
    )

    context = solver._command_context()

    assert solver.case_fingerprint() == without.case_fingerprint()
    assert context['progress_case_id'] == 'viscous_c7600'
    assert context['progress_filename'] == 'viscous_c7600.json'


def test_progress_dir_accepts_str(tmp_path, bridge_model, load_case, damper_params) -> None:
    solver = _configured(
        OpenSeesPyInProcSolver(progress_dir=str(tmp_path / 'progress')),
        bridge_model,
        load_case,
        damper_params,
    )

    assert solver.progress_dir == tmp_path / 'progress'
    assert solver._command_context()['progress_path'] == (tmp_path / 'progress').as_posix()


def test_ansys_registers_read_only_output_probe_without_changing_fingerprint(
    tmp_path, bridge_model, load_case, damper_params
) -> None:
    progress_dir = tmp_path / 'progress'
    without = _configured(AnsysSolver(), bridge_model, load_case, damper_params)
    with_progress = _configured(
        AnsysSolver(progress_dir=progress_dir), bridge_model, load_case, damper_params
    )
    assert without.case_fingerprint() == with_progress.case_fingerprint()

    case_dir = tmp_path / 'solver' / with_progress.build_case_id(
        bridge_model, load_case, damper_params
    )
    case_dir.mkdir(parents=True)
    with_progress._prepare_execution_files(case_dir, with_progress._command_context())
    (case_dir / 'ansys.out').write_text('TIME=5.0\n', encoding='utf-8')

    refresh_ansys_output_probes(progress_dir)

    entries = read_all_progress(progress_dir)
    assert entries[0]['percent'] == 50
    assert 'progress' not in repr(with_progress.design_metadata()).lower()
    assert 'case_step_progress' in with_progress.solver_capability()['supported_features']
