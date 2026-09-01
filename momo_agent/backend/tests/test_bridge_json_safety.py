from __future__ import annotations

import math

import numpy as np
import pytest

from pyansys_bridge.models.load_case import TimeHistoryLoad
from pyansys_bridge.optimization.decision_explainability import evaluate_decision_robustness
from pyansys_bridge.optimization.workflow import SurrogateOptimizationResult


class _NonFiniteSurrogate:
    def predict(self, designs: np.ndarray) -> np.ndarray:
        return np.full(designs.shape[0], math.nan)


def test_time_history_csv_rejects_non_finite_header_metadata(tmp_path) -> None:
    load = TimeHistoryLoad.from_series(
        kind="earthquake",
        samples=[0.0, 1.0],
        dt=0.1,
        metadata={"scale": math.nan},
    )

    target = tmp_path / "load.csv"
    with pytest.raises(ValueError, match="Out of range float values"):
        load.write_csv(target)

    assert not target.exists()


def test_decision_report_rejects_non_finite_predictions(tmp_path) -> None:
    result = SurrogateOptimizationResult(
        parameter_names=("damping",),
        objective_names=("drift",),
        candidates=np.array([[0.1]]),
        objectives=np.array([[1.0]]),
        pareto_designs=np.array([[0.1]]),
        pareto_objectives=np.array([[1.0]]),
        decision_weights=np.array([1.0]),
        best_design=np.array([0.1]),
        best_objectives=np.array([1.0]),
    )

    with pytest.raises(ValueError, match="Out of range float values"):
        evaluate_decision_robustness(
            result,
            [_NonFiniteSurrogate()],
            output_dir=tmp_path,
        )

    assert not (tmp_path / "decision_explainability.json").exists()
