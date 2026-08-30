"""Deterministic solver used for tests and dry-run pipelines."""

from __future__ import annotations

from pyansys_bridge.core.result_summary import objectives_from_timeseries
from pyansys_bridge.core.solver_interface import SolverInterface
from pyansys_bridge.models import BridgeModel, DamperParams, LoadCase


MOCK_OBJECTIVE_CONTRACT = "mock_objectives_v2"


class MockSolver(SolverInterface):
    """Small deterministic solver that mimics the unified interface."""

    solver_name = "mock"

    def prepare_model(self, bridge_model: BridgeModel) -> None:
        self.bridge_model = bridge_model

    def set_damper_params(self, params: DamperParams) -> None:
        self.damper_params = params

    def apply_load_case(self, load_case: LoadCase) -> None:
        self.load_case = load_case

    def case_fingerprint(self) -> str:
        return MOCK_OBJECTIVE_CONTRACT

    def solve(self) -> None:
        if self.bridge_model is None or self.damper_params is None or self.load_case is None:
            raise RuntimeError("Model, load case, and damper parameters must be set before solve")

    def extract_timeseries(self) -> dict[str, list[float]]:
        assert self.damper_params is not None
        assert self.load_case is not None
        scale = self.load_case.scale
        c_factor = 1.0 / (self.damper_params.c / 1.0e5)
        alpha_factor = 1.0 + abs(self.damper_params.alpha)
        return {
            "time": [0.0, 0.5, 1.0],
            "displacement": [0.0, 0.01 * scale * c_factor, 0.005 * scale * c_factor],
            "acceleration": [0.0, 0.10 * scale * alpha_factor, -0.04 * scale * alpha_factor],
            "tower_base_moment": [0.0, 120.0 * scale * c_factor, -80.0 * scale * c_factor],
            "tower_base_shear": [0.0, 18.0 * scale * c_factor, -12.0 * scale * c_factor],
            "damper_force": [0.0, self.damper_params.c * 0.001 * scale, -self.damper_params.c * 0.0005 * scale],
            "damper_stroke": [0.0, 0.002 * scale * c_factor, 0.001 * scale * c_factor],
        }

    def extract_objectives(self) -> dict[str, float]:
        ts = self.extract_timeseries()
        return objectives_from_timeseries(ts)
