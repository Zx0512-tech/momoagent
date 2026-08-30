"""Unified solver interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from hashlib import sha256

from pyansys_bridge.models import AnalysisResult, BridgeModel, DamperParams, LoadCase

COMMON_SOLVER_WORKFLOW_ROLES = [
    "doe",
    "surrogate_initial_sampling",
    "active_learning",
    "high_fidelity_verification",
    "final_evaluation",
    "mutual_validation",
]

COMMON_PRODUCTION_SOLVER_FEATURES = [
    "gravity_initial_state",
    "modal_analysis",
    "earthquake_analysis",
    "wind_analysis",
    "traffic_analysis",
    "combined_load_cases",
    "viscous_damper",
    "friction_damper",
    "eddy_current_damper",
    "physical_damper_count",
    "baseline_workflow",
    "doe_sampling",
    "surrogate_initial_sampling",
    "active_learning_sampling",
    "high_fidelity_evaluation",
    "final_evaluation",
    "mutual_validation",
    "standard_summary_json",
    "standard_timeseries_csv",
]


def build_case_id(
    solver: str,
    model: BridgeModel,
    load_case: LoadCase,
    params: DamperParams,
    extra: str | None = None,
) -> str:
    parts = [solver, model.fingerprint(), load_case.fingerprint(), params.fingerprint()]
    if extra is not None:
        parts.append(extra)
    payload = "|".join(parts)
    return sha256(payload.encode("utf-8")).hexdigest()[:16]


class SolverInterface(ABC):
    """Abstract base class for ANSYS, OpenSees, and test solvers."""

    solver_name: str

    def __init__(self) -> None:
        self.bridge_model: BridgeModel | None = None
        self.damper_params: DamperParams | None = None
        self.load_case: LoadCase | None = None

    @abstractmethod
    def prepare_model(self, bridge_model: BridgeModel) -> None:
        """Prepare the solver-side model."""

    @abstractmethod
    def set_damper_params(self, params: DamperParams) -> None:
        """Apply damper parameters."""

    @abstractmethod
    def apply_load_case(self, load_case: LoadCase) -> None:
        """Apply a load case."""

    @abstractmethod
    def solve(self) -> None:
        """Run the solver."""

    @abstractmethod
    def extract_timeseries(self) -> dict[str, list[float]]:
        """Extract response time histories."""

    @abstractmethod
    def extract_objectives(self) -> dict[str, float]:
        """Extract scalar objectives."""

    def cleanup(self) -> None:
        """Release solver resources."""

    def case_fingerprint(self) -> str | None:
        """Return solver-configuration data that changes the physical case."""

        return None

    def design_metadata(self) -> dict[str, object]:
        """Return solver configuration to persist with design samples."""

        return {}

    def solver_capability(self) -> dict[str, object]:
        """Describe recommended solver use without changing execution behavior."""

        return {
            "type": self.solver_name,
            "primary_role": "generic",
            "recommended_for": [],
            "workflow_roles": list(COMMON_SOLVER_WORKFLOW_ROLES),
            "validation_peer": None,
            "supported_features": (),
            "not_implemented": (),
        }

    def require_features(self, features: list[str] | tuple[str, ...]) -> None:
        capability = self.solver_capability()
        supported = set(capability.get("supported_features", ()))
        missing = [feature for feature in features if feature not in supported]
        if missing:
            raise NotImplementedError(
                f"{self.solver_name} solver does not implement required feature(s): "
                + ", ".join(missing)
            )

    def build_case_id(self, model: BridgeModel, load_case: LoadCase, params: DamperParams) -> str:
        """Build a stable cache key for this solver and case."""

        return build_case_id(self.solver_name, model, load_case, params, extra=self.case_fingerprint())

    def run_analysis(
        self,
        bridge_model: BridgeModel,
        load_case: LoadCase,
        damper_params: DamperParams,
    ) -> AnalysisResult:
        self.prepare_model(bridge_model)
        self.set_damper_params(damper_params)
        self.apply_load_case(load_case)
        case_id = self.build_case_id(bridge_model, load_case, damper_params)
        result = AnalysisResult(case_id=case_id, solver=self.solver_name, status="running")
        try:
            self.solve()
            result.timeseries = self.extract_timeseries()
            result.objectives = self.extract_objectives()
            result.metadata = {
                "model": bridge_model.to_dict(),
                "load_case": load_case.to_dict(),
                "damper_params": damper_params.to_dict(),
            }
            design_metadata = self.design_metadata()
            if design_metadata:
                result.metadata["solver_design"] = design_metadata
            return result.finish("completed")
        except Exception as exc:
            result.metadata = {"error": str(exc)}
            return result.finish("failed")
        finally:
            self.cleanup()
