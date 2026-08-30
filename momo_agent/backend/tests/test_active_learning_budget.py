from types import SimpleNamespace

import pyansys_bridge.batch as batch_module
import pyansys_bridge.optimization.doe_pipeline as doe_pipeline
from pyansys_bridge.optimization.doe_pipeline import DamperDOEObjectiveSpec


def _pipeline(*, accepted: bool, record_count: int = 2) -> SimpleNamespace:
    report = SimpleNamespace(verified_execution=True, accepted=accepted)
    return SimpleNamespace(
        optimization=object(),
        validation_reports={'earthquake:peak': report},
        validation_records=tuple(object() for _ in range(record_count)),
        review_records=(),
    )


def _run_adaptive_pipeline(monkeypatch, tmp_path, *, accepted: bool):
    calls: list[dict] = []
    monkeypatch.setattr(batch_module, 'run_damper_doe_batch', lambda *args, **kwargs: [])
    monkeypatch.setattr(doe_pipeline, '_apply_damper_cost_objectives', lambda *args: None)
    monkeypatch.setattr(
        doe_pipeline,
        '_save_validation_records_to_training_store',
        lambda root, records: tuple(f'case_{len(calls)}_{index}' for index, _ in enumerate(records)),
    )
    monkeypatch.setattr(doe_pipeline, 'realize_optimized_damper_plan', lambda *args, **kwargs: object())

    def optimize(*args, **kwargs):
        calls.append(kwargs)
        return _pipeline(accepted=accepted)

    monkeypatch.setattr(doe_pipeline, 'optimize_from_result_stores', optimize)
    result = doe_pipeline.optimize_from_damper_doe_batch(
        object(),
        [],
        [],
        objective_specs=[DamperDOEObjectiveSpec(scenario='earthquake', objective='peak')],
        bounds={'c': (1000.0, 10000.0), 'alpha': (0.3, 1.0)},
        output_dir=tmp_path,
        run_validation=True,
        max_validation_peak_relative_error=0.05,
        n_validation_points=2,
        max_validation_designs=2,
        max_active_learning_iterations=2,
    )
    return result, calls


def test_active_learning_does_not_add_points_when_validation_is_accepted(monkeypatch, tmp_path) -> None:
    result, calls = _run_adaptive_pipeline(monkeypatch, tmp_path, accepted=True)

    assert result.active_learning_iterations == 0
    assert result.active_learning_records == ()
    assert len(calls) == 2


def test_active_learning_adds_two_points_per_round_and_stops_after_four(monkeypatch, tmp_path) -> None:
    result, calls = _run_adaptive_pipeline(monkeypatch, tmp_path, accepted=False)

    assert result.active_learning_iterations == 2
    assert len(result.active_learning_records) == 4
    assert len(calls) == 6
