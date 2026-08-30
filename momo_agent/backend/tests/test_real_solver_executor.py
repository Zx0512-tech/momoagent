from __future__ import annotations

from threading import Event

import pytest
import numpy as np

from app.services.real_execution import (
    ActiveLearningRequest,
    ConfigExecutionRequest,
    RealExecutionCancelled,
    RealExecutionError,
    RealExecutionTimeout,
    RealSolverExecutor,
    SolverExecutionRequest,
    SurrogateTrainingRequest,
    design_set_sha256,
    generate_two_factor_doe,
    train_surrogate,
    select_infill_designs,
)
from pyansys_bridge.models import BridgeModel, DamperParams, LoadCase


def _request(tmp_path, *, timeout_s: float | None = None) -> SolverExecutionRequest:
    return SolverExecutionRequest(
        solver='mock',
        bridge_model=BridgeModel(name='fixture', source_path='fixture.txt'),
        load_cases=(LoadCase(name='earthquake', load_type='earthquake', scale=1.0),),
        damper_params=(DamperParams(c=1000.0, alpha=0.5),),
        output_dir=tmp_path / 'results',
        timeout_s=timeout_s,
        run_id='run_1',
        job_id='job_1',
    )


def test_shared_executor_persists_manifest_catalog_and_usage(tmp_path) -> None:
    heartbeats: list[str] = []
    result = RealSolverExecutor().execute(_request(tmp_path), heartbeat=heartbeats.append)

    assert len(result.results) == 1
    assert result.results[0].status == 'completed'
    assert result.output_manifest['fileCount'] >= 1
    assert all(len(item['sha256']) == 64 for item in result.output_manifest['files'])
    assert result.result_catalog['caseCount'] == 1
    assert result.result_catalog['entries'][0]['verified'] is True
    assert result.result_catalog['entries'][0]['units']['displacement'] == 'm'
    assert heartbeats == [result.results[0].case_id]
    assert result.usage['runId'] == 'run_1'


def test_shared_executor_checks_cancellation_before_solver(tmp_path) -> None:
    cancel = Event()
    cancel.set()

    with pytest.raises(RealExecutionCancelled):
        RealSolverExecutor().execute(_request(tmp_path), cancel_event=cancel)


def test_shared_executor_fails_closed_for_expired_timeout(tmp_path) -> None:
    ticks = iter((0.0, 2.0))
    with pytest.raises(RealExecutionTimeout):
        RealSolverExecutor(clock=lambda: next(ticks)).execute(_request(tmp_path, timeout_s=1.0))


def test_config_executor_preserves_runner_payload_and_reports_lifecycle(tmp_path) -> None:
    calls: list[tuple[str, float | None]] = []
    heartbeats: list[str] = []

    class RunnerResult:
        def to_dict(self) -> dict[str, object]:
            return {'baseline_status': 'completed', 'value': 3}

    def runner(path, *, execution_timeout_s):
        calls.append((str(path), execution_timeout_s))
        return RunnerResult()

    result = RealSolverExecutor().execute_config(
        ConfigExecutionRequest(
            config_path=tmp_path / 'config.json',
            timeout_s=12.0,
            run_id='run_1',
            job_id='job_1',
        ),
        runner=runner,
        heartbeat=heartbeats.append,
    )

    assert result.payload == {'baseline_status': 'completed', 'value': 3}
    assert calls == [(str(tmp_path / 'config.json'), 12.0)]
    assert heartbeats == ['completed']
    assert result.usage['runId'] == 'run_1'
    assert result.usage['jobId'] == 'job_1'


def test_config_executor_checks_cancel_and_timeout_before_and_after_runner(tmp_path) -> None:
    cancel = Event()
    cancel.set()
    called = False

    def runner(path, *, execution_timeout_s):
        nonlocal called
        called = True
        return {'status': 'completed'}

    with pytest.raises(RealExecutionCancelled):
        RealSolverExecutor().execute_config(
            ConfigExecutionRequest(config_path=tmp_path / 'config.json', timeout_s=5.0),
            runner=runner,
            cancel_event=cancel,
        )
    assert called is False

    ticks = iter((0.0, 2.0))
    with pytest.raises(RealExecutionTimeout):
        RealSolverExecutor(clock=lambda: next(ticks)).execute_config(
            ConfigExecutionRequest(config_path=tmp_path / 'config.json', timeout_s=1.0),
            runner=lambda path, *, execution_timeout_s: {'status': 'completed'},
        )


def test_config_executor_rejects_failed_runner_payload(tmp_path) -> None:
    with pytest.raises(RealExecutionError, match='失败状态'):
        RealSolverExecutor().execute_config(
            ConfigExecutionRequest(config_path=tmp_path / 'config.json'),
            runner=lambda path, *, execution_timeout_s: {'status': 'failed'},
        )


def test_doe_generator_preserves_historical_five_and_is_seed_stable() -> None:
    bounds = {'c': (1000.0, 10000.0), 'alpha': (0.3, 1.0)}
    first = generate_two_factor_doe(bounds, count=15, seed=20260705)
    second = generate_two_factor_doe(bounds, count=15, seed=20260705)

    assert first[:5] == [
        {'c': 5500.0, 'alpha': 0.65},
        {'c': 1000.0, 'alpha': 0.3},
        {'c': 1000.0, 'alpha': 1.0},
        {'c': 10000.0, 'alpha': 0.3},
        {'c': 10000.0, 'alpha': 1.0},
    ]
    assert first == second
    assert len(first) == 15
    assert len(generate_two_factor_doe(bounds, count=5, seed=1)) == 5
    assert len(generate_two_factor_doe(bounds, count=24, seed=1)) == 24
    assert len(design_set_sha256(first)) == 64


def test_doe_generator_rejects_out_of_range_counts() -> None:
    bounds = {'c': (1000.0, 10000.0), 'alpha': (0.3, 1.0)}
    with pytest.raises(ValueError):
        generate_two_factor_doe(bounds, count=4, seed=1)
    with pytest.raises(ValueError):
        generate_two_factor_doe(bounds, count=25, seed=1)


def test_surrogate_training_uses_real_results_and_reloads_model(tmp_path) -> None:
    base = _request(tmp_path)
    request = SolverExecutionRequest(
        solver=base.solver,
        bridge_model=base.bridge_model,
        load_cases=base.load_cases,
        damper_params=tuple(
            DamperParams(c=1000.0 + index * 500.0, alpha=0.4 + index * 0.05)
            for index in range(8)
        ),
        output_dir=base.output_dir,
    )
    RealSolverExecutor().execute(request)

    trained = train_surrogate(SurrogateTrainingRequest(
        result_root=base.output_dir,
        target_name='max_displacement',
        output_dir=tmp_path / 'models',
        model_names=('gpr',),
        cv='holdout',
    ))

    assert trained.sample_count == 8
    assert trained.model_path.is_file()
    assert len(trained.model_sha256) == 64
    assert len(trained.dataset_sha256) == 64
    assert trained.reload_reproducible is True
    assert trained.cv_metrics['gpr']['rmse'] >= 0


def test_active_learning_respects_two_round_two_point_budget_and_deduplicates() -> None:
    candidates = np.array([
        [1.0, 0.1], [2.0, 0.2], [3.0, 0.3], [4.0, 0.4], [5.0, 0.5], [6.0, 0.6],
    ])
    result = select_infill_designs(ActiveLearningRequest(
        candidates=candidates,
        existing_designs=candidates[:2],
        batch_size=2,
        max_iterations=2,
    ), precision_satisfied=False)

    assert result.real_solve_count == 4
    assert result.iteration_count == 2
    assert result.budget_exhausted is True
    assert len({tuple(row) for row in result.designs}) == 4

    no_addition = select_infill_designs(ActiveLearningRequest(
        candidates=candidates,
        existing_designs=candidates[:2],
    ), precision_satisfied=True)
    assert no_addition.real_solve_count == 0
