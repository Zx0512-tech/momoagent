"""Batch analyzer."""

from __future__ import annotations

from pathlib import Path

from pyansys_bridge.core.solver_factory import SolverFactory
from pyansys_bridge.models import BridgeModel, DamperParams, LoadCase

from .case_runner import CaseRunner
from .result_store import ResultStore


class BatchAnalyzer:
    """Run multiple damper/load combinations through a solver adapter."""

    def __init__(
        self,
        solver: str = "mock",
        output_dir: str | Path = "output/batch_results",
        solver_kwargs: dict | None = None,
        cache_dirs: list[str | Path] | tuple[str | Path, ...] | None = None,
    ) -> None:
        self.solver_name = solver
        self.store = ResultStore(output_dir)
        self.fallback_stores = tuple(ResultStore(path) for path in (cache_dirs or ()))
        self.solver_kwargs = dict(solver_kwargs or {})
        if self.solver_name.lower() in {"ansys", "opensees", "openseespy_inproc"} and "output_dir" not in self.solver_kwargs:
            self.solver_kwargs["output_dir"] = self.store.root / "_command_streams"

    def run_combinations(
        self,
        bridge_model: BridgeModel,
        damper_params: list[DamperParams],
        load_cases: list[LoadCase],
    ):
        results = []
        for params in damper_params:
            for load_case in load_cases:
                solver = SolverFactory.create(self.solver_name, **self.solver_kwargs)
                runner = CaseRunner(solver, self.store, list(self.fallback_stores))
                results.append(runner.run(bridge_model, load_case, params))
        return results
