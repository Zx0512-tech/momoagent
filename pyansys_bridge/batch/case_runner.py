"""Single case runner."""

from __future__ import annotations

from pyansys_bridge.models import AnalysisResult, BridgeModel, DamperParams, LoadCase

from .result_store import ResultStore


class CaseRunner:
    """Run one analysis case with caching."""

    def __init__(self, solver, store: ResultStore, fallback_stores: list[ResultStore] | None = None) -> None:
        self.solver = solver
        self.store = store
        self.fallback_stores = tuple(fallback_stores or ())

    def run(self, model: BridgeModel, load_case: LoadCase, params: DamperParams) -> AnalysisResult:
        self.solver.prepare_model(model)
        self.solver.set_damper_params(params)
        self.solver.apply_load_case(load_case)
        case_id = self.solver.build_case_id(model, load_case, params)
        if self.store.has_completed(case_id):
            result = self.store.load(case_id)
            if _refresh_cached_metadata(result, load_case, self.solver.design_metadata()):
                self.store.save(result)
            return result
        for fallback in self.fallback_stores:
            if fallback.has_completed(case_id):
                result = fallback.load(case_id)
                _refresh_cached_metadata(result, load_case, self.solver.design_metadata())
                self.store.save(result)
                return result
        result = self.solver.run_analysis(model, load_case, params)
        self.store.save(result)
        return result


def _refresh_cached_metadata(
    result: AnalysisResult,
    load_case: LoadCase,
    solver_design: dict[str, object],
) -> bool:
    changed = False
    load_case_payload = load_case.to_dict()
    if result.metadata.get("load_case") != load_case_payload:
        result.metadata["load_case"] = load_case_payload
        changed = True
    if solver_design:
        existing_design = dict(result.metadata.get("solver_design", {}))
        merged_design = {**existing_design, **solver_design}
        if result.metadata.get("solver_design") != merged_design:
            result.metadata["solver_design"] = merged_design
            changed = True
    return changed
