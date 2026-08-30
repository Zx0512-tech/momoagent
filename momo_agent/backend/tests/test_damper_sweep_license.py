from __future__ import annotations

from concurrent.futures import Future
import threading
from pathlib import Path

import pytest

import app.services.platform_store as platform_store_module
from app.api.v1.schemas import Artifact, DamperParameterSweepRequest
from app.services.platform_store import PlatformStore


def _fake_artifact(name: str) -> Artifact:
    return Artifact(
        artifactId=f'art_{name}',
        kind='JSON_SUMMARY',
        name=name,
        path=f'output/test/{name}',
        mimeType='application/json',
        canPreview=True,
        downloadUrl=f'/api/v1/artifacts/art_{name}/download',
        sha256='0' * 64,
    )


def test_ansys_license_concurrency_cap_env_parsing(monkeypatch) -> None:
    monkeypatch.delenv('MOMO_ANSYS_MAX_CONCURRENT', raising=False)
    assert PlatformStore._ansys_license_concurrency_cap() is None
    monkeypatch.setenv('MOMO_ANSYS_MAX_CONCURRENT', '2')
    assert PlatformStore._ansys_license_concurrency_cap() == 2
    monkeypatch.setenv('MOMO_ANSYS_MAX_CONCURRENT', '0')
    assert PlatformStore._ansys_license_concurrency_cap() is None
    monkeypatch.setenv('MOMO_ANSYS_MAX_CONCURRENT', 'not-a-number')
    assert PlatformStore._ansys_license_concurrency_cap() is None


@pytest.mark.parametrize(
    ('message', 'expected'),
    [
        ('MAPDL exited: ANSYS LICENSE MANAGER ERROR: FlexNet Licensing error -15,570', True),
        ('cannot connect to ansyslmd server', True),
        ('lmgrd is not running', True),
        ('mesh generation failed at element 42', False),
        ('timeout waiting for solver output', False),
    ],
)
def test_ansys_license_error_classification(message: str, expected: bool) -> None:
    assert PlatformStore._is_ansys_license_error(RuntimeError(message)) is expected


def test_sweep_api_request_defaults_to_four_concurrent_cases() -> None:
    request = DamperParameterSweepRequest.model_validate({
        'runMode': 'REAL_DAMPER_PARAMETER_SWEEP',
        'solver': 'OPENSEESPY_INPROC',
        'caseSetId': 'sweep_default_concurrency',
        'cases': [
            {
                'caseId': 'viscous_c7600',
                'damperType': 'VISCOUS',
                'parameters': {'c': 7600.0, 'alpha': 0.8, 'vfloor': 1e-6},
            }
        ],
        'responseIds': ['max_girder_end_displacement'],
    })

    assert request.max_concurrent_cases == 4


def _sweep_store(tmp_path: Path, monkeypatch) -> tuple[PlatformStore, dict[str, dict]]:
    store = PlatformStore(state_path=tmp_path / 'state' / 'platform.sqlite3', recover_orphans=False)
    monkeypatch.setattr(
        platform_store_module,
        'EARTHQUAKE_WORKFLOW_OUTPUT_ROOT',
        tmp_path / 'real_workflows',
    )
    written: dict[str, dict] = {}
    monkeypatch.setattr(store, '_load_json_config', lambda _path: {'solver_kwargs': {}})
    monkeypatch.setattr(
        store,
        '_earthquake_baseline_config',
        lambda config, _config_dir, _output_dir, _solver, _load_kind='EARTHQUAKE': dict(config),
    )
    monkeypatch.setattr(store, '_apply_agent_standard_earthquake_load', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        store,
        '_write_json_config',
        lambda path, config: written.__setitem__(Path(path).name, config),
    )
    monkeypatch.setattr(
        store,
        '_register_json_file_artifact',
        lambda *, name, path, fallback_preview: _fake_artifact(name),
    )
    monkeypatch.setattr(store, '_register_inquiry_csv_artifacts', lambda _case_dir: [])
    monkeypatch.setattr(store, '_register_result_catalog', lambda _run_dir: _fake_artifact('result_catalog.json'))
    monkeypatch.setattr(
        store,
        '_register_real_output_manifest',
        lambda _run_dir: _fake_artifact('real_output_manifest.json'),
    )
    monkeypatch.setattr(store, '_begin_job_progress', lambda _job_id, total_cases: None)
    return store, written


def _sweep_params(case_count: int) -> dict:
    return {
        'solver': 'ANSYS',
        'maxConcurrentCases': 3,
        'selectedLayoutId': 'TWO_PER_TOWER',
        'selectedLayout': {'physicalCountPerTower': 2, 'nodePairs': [[36, 517], [36, 518]]},
        'cases': [
            {
                'caseId': f'case_{index}',
                'damperType': 'VISCOUS',
                'solverModule': 'damper_user300_viscous',
                'parameters': {'c': 1000.0 + index, 'alpha': 0.5, 'vfloor': 1.0e-4},
            }
            for index in range(case_count)
        ],
    }


def test_sweep_license_failure_retries_serially_and_records_evidence(tmp_path, monkeypatch) -> None:
    """并发抢不到许可证的 case 降级串行重试；座位数上限与重试名单进证据。"""
    monkeypatch.setenv('MOMO_ANSYS_MAX_CONCURRENT', '2')
    store, written = _sweep_store(tmp_path, monkeypatch)

    attempts: dict[str, int] = {}
    lock = threading.Lock()

    def fake_run(config_path, *, execution_timeout_s):
        del execution_timeout_s
        case_id = Path(config_path).parent.name
        with lock:
            attempts[case_id] = attempts.get(case_id, 0) + 1
            attempt = attempts[case_id]
        if case_id == 'case_1' and attempt == 1:
            raise RuntimeError('MAPDL exited: ANSYS LICENSE MANAGER ERROR: FlexNet Licensing error -15,570')
        return {
            'status': 'completed',
            'objectives': {'max_displacement': 0.1},
            'metadata': {'execution_mode': 'run', 'is_verified_solver_output': True},
            'case_id': f'solver_{case_id}',
        }

    monkeypatch.setattr(store, '_run_real_damper_comparison_case', fake_run)

    store._generate_real_damper_parameter_sweep_artifacts(_sweep_params(3), job_id=None)

    assert attempts == {'case_0': 1, 'case_1': 2, 'case_2': 1}
    summary = written['real_damper_parameter_sweep_summary.json']
    parallelism = summary['parallelism']
    assert parallelism['requestedMaxConcurrentCases'] == 3
    assert parallelism['effectiveMaxConcurrentCases'] == 2
    assert parallelism['ansysLicenseConcurrencyCap'] == 2
    assert parallelism['licenseRetriedCaseIds'] == ['case_1']
    assert [case['caseId'] for case in summary['caseResults']] == ['case_0', 'case_1', 'case_2']
    assert summary['allVerifiedExecution'] is True
    assert {
        config['solver_kwargs']['damper_c_scale']
        for config in written.values()
        if config.get('damper_params')
    } == {1000.0}


def test_sweep_non_license_failure_still_fails_batch(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv('MOMO_ANSYS_MAX_CONCURRENT', raising=False)
    store, _written = _sweep_store(tmp_path, monkeypatch)

    def fake_run(config_path, *, execution_timeout_s):
        del execution_timeout_s
        if Path(config_path).parent.name == 'case_1':
            raise RuntimeError('mesh generation failed at element 42')
        return {
            'status': 'completed',
            'objectives': {'max_displacement': 0.1},
            'metadata': {'execution_mode': 'run', 'is_verified_solver_output': True},
            'case_id': 'solver_ok',
        }

    monkeypatch.setattr(store, '_run_real_damper_comparison_case', fake_run)

    with pytest.raises(RuntimeError, match='mesh generation failed'):
        store._generate_real_damper_parameter_sweep_artifacts(_sweep_params(3), job_id=None)


def test_sweep_serial_license_failure_is_not_retried(tmp_path, monkeypatch) -> None:
    """串行执行（maxConcurrentCases=1）没有座位竞争，许可证错误按普通失败处理。"""
    monkeypatch.delenv('MOMO_ANSYS_MAX_CONCURRENT', raising=False)
    store, _written = _sweep_store(tmp_path, monkeypatch)

    def fake_run(config_path, *, execution_timeout_s):
        del config_path, execution_timeout_s
        raise RuntimeError('ANSYS LICENSE MANAGER ERROR')

    monkeypatch.setattr(store, '_run_real_damper_comparison_case', fake_run)
    params = _sweep_params(2)
    params['maxConcurrentCases'] = 1

    with pytest.raises(RuntimeError, match='LICENSE'):
        store._generate_real_damper_parameter_sweep_artifacts(params, job_id=None)


def test_openseespy_sweep_uses_process_pool_and_case_progress_ids(tmp_path, monkeypatch) -> None:
    """OpenSeesPy 的 domain 必须隔离在每个 case 的子进程中。"""
    store, written = _sweep_store(tmp_path, monkeypatch)

    class InlineProcessPool:
        max_workers: int | None = None

        def __init__(self, *, max_workers: int) -> None:
            type(self).max_workers = max_workers

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback) -> bool:
            return False

        def submit(self, fn, *args, **kwargs) -> Future:
            future = Future()
            try:
                future.set_result(fn(*args, **kwargs))
            except BaseException as exc:
                future.set_exception(exc)
            return future

    def fake_process_case(config_path, execution_timeout_s):
        del execution_timeout_s
        case_id = Path(config_path).parent.name
        return {
            'status': 'completed',
            'objectives': {'max_displacement': 0.1},
            'metadata': {'execution_mode': 'run', 'is_verified_solver_output': True},
            'case_id': f'solver_{case_id}',
            'executionUsage': {'caseId': case_id},
        }

    def fake_thread_case(config_path, *, execution_timeout_s):
        return fake_process_case(config_path, execution_timeout_s)

    monkeypatch.setattr(platform_store_module, 'ProcessPoolExecutor', InlineProcessPool, raising=False)
    monkeypatch.setattr(
        platform_store_module,
        '_execute_damper_parameter_sweep_case',
        fake_process_case,
        raising=False,
    )
    monkeypatch.setattr(store, '_run_real_damper_comparison_case', fake_thread_case)
    params = _sweep_params(4)
    params['solver'] = 'OPENSEESPY_INPROC'
    params['maxConcurrentCases'] = 4
    for case in params['cases']:
        case['solverModule'] = 'damper_viscous'

    store._generate_real_damper_parameter_sweep_artifacts(params, job_id=None)

    summary = written['real_damper_parameter_sweep_summary.json']
    assert InlineProcessPool.max_workers == 4
    assert summary['parallelism']['effectiveMaxConcurrentCases'] == 4
    assert summary['parallelism']['mode'] == 'process_pool'
    assert {
        config['solver_kwargs']['damper_c_scale']
        for config in written.values()
        if config.get('damper_params')
    } == {1000.0}
    assert any(
        config.get('solver_kwargs', {}).get('progress_case_id') == 'case_3'
        for config in written.values()
    )
