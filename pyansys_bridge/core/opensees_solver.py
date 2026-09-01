"""OpenSees solver adapter."""

from __future__ import annotations

from pathlib import Path

from pyansys_bridge.core.command_stream_solver import CommandStreamDryRunSolver
from pyansys_bridge.core.opensees_common import (
    STBRIDGE_SUPPORT_NODES,
    STBRIDGE_TOWER_BASE_ELEMENT_NODE_MAP,
    STBRIDGE_TOWER_BASE_SHEAR_INERTIA_ELEMENT_MASS_DENSITIES,
    dof_tuple,
    element_mass_density_tuple,
    element_node_map_tuple,
    operation_load_nodes_from_model,
    opensees_damper_block,
    opensees_direction,
    opensees_system_config,
    OPENSEES_DAMPER_C_SCALE,
    OPENSEES_DAMPER_IMPLEMENTATION_CONTRACT,
    node_tuple,
    placement_dof_pairs,
    placement_pair_tuple,
    uniform_excitation_axis,
)
from pyansys_bridge.core.solver_interface import COMMON_SOLVER_WORKFLOW_ROLES
from pyansys_bridge.models import BridgeModel, DamperParams, LoadCase, RealizableDamper


class OpenSeesSolver(CommandStreamDryRunSolver):
    """OpenSees adapter with optional reference model metadata."""

    solver_name = "opensees"
    file_extension = ".py"

    def __init__(
        self,
        model_path: str | None = None,
        python_executable: str = "python",
        sim_workdir: str | None = None,
        use_sim: bool = False,
        response_nodes: tuple[int, ...] | list[int] = (36, 107),
        response_dof: int = 1,
        tower_base_nodes: tuple[int, ...] | list[int] = STBRIDGE_SUPPORT_NODES,
        tower_base_element_node_map: tuple[tuple[int, int], ...] | list[tuple[int, int]] = STBRIDGE_TOWER_BASE_ELEMENT_NODE_MAP,
        tower_base_shear_inertia_element_mass_densities: tuple[tuple[int, float], ...] | list[tuple[int, float]] = STBRIDGE_TOWER_BASE_SHEAR_INERTIA_ELEMENT_MASS_DENSITIES,
        tower_base_shear_dofs: tuple[int, ...] | list[int] = (1, 2),
        tower_base_moment_dofs: tuple[int, ...] | list[int] = (5, 6),
        diagnostic_nodes: tuple[int, ...] | list[int] = (),
        diagnostic_pairs: tuple[tuple[int, ...], ...] | list[tuple[int, ...]] = (),
        damping_ratio: float = 0.05,
        rayleigh_frequency_a_hz: float | None = None,
        rayleigh_frequency_b_hz: float | None = None,
        opensees_system: str = "UmfPack",
        opensees_system_args: tuple[str, ...] | list[str] | str = (),
        damper_c_scale: float = OPENSEES_DAMPER_C_SCALE,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        if not response_nodes:
            raise ValueError("response_nodes must contain at least one OpenSees node")
        if response_dof < 1 or response_dof > 6:
            raise ValueError("response_dof must be in the OpenSees range 1..6")
        if not tower_base_nodes:
            raise ValueError("tower_base_nodes must contain at least one OpenSees node")
        self.model_path = model_path or str(
            Path(
                "bridge_models/stbridge_opensees/baseline_verified/"
                "stbridge_baseline_openseespy_gravity_modal.py"
            ).resolve()
        )
        self.python_executable = python_executable
        self.sim_workdir = Path(sim_workdir) if sim_workdir is not None else None
        self.use_sim = use_sim
        self.response_nodes = tuple(int(node) for node in response_nodes)
        self.response_dof = response_dof
        self.tower_base_nodes = tuple(int(node) for node in tower_base_nodes)
        self.tower_base_element_node_map = element_node_map_tuple(
            tower_base_element_node_map,
            "tower_base_element_node_map",
        )
        self.tower_base_shear_inertia_element_mass_densities = element_mass_density_tuple(
            tower_base_shear_inertia_element_mass_densities,
            "tower_base_shear_inertia_element_mass_densities",
        )
        self.tower_base_shear_dofs = dof_tuple(tower_base_shear_dofs, "tower_base_shear_dofs")
        self.tower_base_moment_dofs = dof_tuple(tower_base_moment_dofs, "tower_base_moment_dofs")
        self.diagnostic_nodes = node_tuple(diagnostic_nodes, "diagnostic_nodes")
        self.diagnostic_pairs = placement_pair_tuple(diagnostic_pairs, "diagnostic_pairs")
        if damping_ratio < 0.0:
            raise ValueError("damping_ratio cannot be negative")
        self.damping_ratio = float(damping_ratio)
        self.rayleigh_frequency_a_hz = (
            None if rayleigh_frequency_a_hz is None else float(rayleigh_frequency_a_hz)
        )
        self.rayleigh_frequency_b_hz = (
            None if rayleigh_frequency_b_hz is None else float(rayleigh_frequency_b_hz)
        )
        self.opensees_system, self.opensees_system_args = opensees_system_config(
            opensees_system,
            opensees_system_args,
        )
        self.damper_c_scale = float(damper_c_scale)
        self._configured_operation_load_nodes = operation_load_nodes_from_model(None)

    def reference_model_exists(self) -> bool:
        return Path(self.model_path).exists()

    def build_case_id(self, model: BridgeModel, load_case: LoadCase, params: DamperParams) -> str:
        self._configured_operation_load_nodes = operation_load_nodes_from_model(model)
        return super().build_case_id(model, load_case, params)

    def prepare_model(self, bridge_model: BridgeModel) -> None:
        self._configured_operation_load_nodes = operation_load_nodes_from_model(bridge_model)
        super().prepare_model(bridge_model)

    def _model_path_for_stream(self) -> str:
        return self.model_path

    def _command_context(self) -> dict[str, object]:
        context = super()._command_context()
        earthquake_dof, earthquake_factor = uniform_excitation_axis(self.load_case.direction)
        context.update(
            {
                "opensees_load_nodes": repr(self._operation_load_nodes()),
                "earthquake_uniform_dof": earthquake_dof,
                "earthquake_uniform_factor": earthquake_factor,
                "response_nodes": repr(self.response_nodes),
                "response_dof": self.response_dof,
                "tower_base_nodes": repr(self.tower_base_nodes),
                "tower_base_element_node_map": repr(self.tower_base_element_node_map),
                "tower_base_shear_inertia_element_mass_densities": repr(
                    self.tower_base_shear_inertia_element_mass_densities
                ),
                "tower_base_shear_dofs": repr(self.tower_base_shear_dofs),
                "tower_base_moment_dofs": repr(self.tower_base_moment_dofs),
                "damper_placement_pairs": repr(placement_dof_pairs(self.placements)),
                "diagnostic_nodes": repr(self.diagnostic_nodes),
                "diagnostic_pairs": repr(self.diagnostic_pairs),
                "damping_ratio": self.bridge_model.metadata.get("damping_ratio", self.damping_ratio),
                "opensees_system": repr(self.opensees_system),
                "opensees_system_args": repr(self.opensees_system_args),
                "rayleigh_frequency_a_hz": self.bridge_model.metadata.get(
                    "rayleigh_frequency_a_hz",
                    self.rayleigh_frequency_a_hz,
                ),
                "rayleigh_frequency_b_hz": self.bridge_model.metadata.get(
                    "rayleigh_frequency_b_hz",
                    self.rayleigh_frequency_b_hz,
                ),
            }
        )
        return context

    def design_metadata(self) -> dict[str, object]:
        metadata = super().design_metadata()
        metadata.update(
            {
                "response_nodes": list(self.response_nodes),
                "response_dof": self.response_dof,
                "tower_base_nodes": list(self.tower_base_nodes),
                "tower_base_element_node_map": [list(pair) for pair in self.tower_base_element_node_map],
                "tower_base_shear_inertia_element_mass_densities": [
                    [element, mass_density]
                    for element, mass_density in self.tower_base_shear_inertia_element_mass_densities
                ],
                "tower_base_shear_dofs": list(self.tower_base_shear_dofs),
                "tower_base_moment_dofs": list(self.tower_base_moment_dofs),
                "diagnostic_nodes": list(self.diagnostic_nodes),
                "diagnostic_pairs": [list(pair) for pair in self.diagnostic_pairs],
                "damping_ratio": self.damping_ratio,
                "opensees_system": self.opensees_system,
                "opensees_system_args": list(self.opensees_system_args),
                "rayleigh_frequency_a_hz": self.rayleigh_frequency_a_hz,
                "rayleigh_frequency_b_hz": self.rayleigh_frequency_b_hz,
                "damper_c_scale": self.damper_c_scale,
                "damper_implementation_contract": OPENSEES_DAMPER_IMPLEMENTATION_CONTRACT,
            }
        )
        metadata.update(
            {
                key: list(value)
                for key, value in self._operation_load_nodes().items()
                if value is not None
            }
        )
        return metadata

    def solver_capability(self) -> dict[str, object]:
        return {
            "type": "opensees",
            "primary_role": "full_workflow_solver",
            "recommended_for": list(COMMON_SOLVER_WORKFLOW_ROLES),
            "workflow_roles": list(COMMON_SOLVER_WORKFLOW_ROLES),
            "validation_peer": "ansys",
            "supported_features": [
                "openseespy_command_stream",
                "openseespy_run",
                "doe_sampling",
                "surrogate_initial_sampling",
                "active_learning_sampling",
                "high_fidelity_evaluation",
                "final_evaluation",
                "mutual_validation",
            ],
            "not_implemented": [
                "ansys_dpf_postprocess",
                "mapdl_batch_run",
                "apdl_command_stream",
            ],
            "backend": "openseespy",
        }

    def _operation_load_nodes(self) -> dict[str, tuple[int, ...] | None]:
        return self._configured_operation_load_nodes

    def _damper_commands(self, dampers: tuple[RealizableDamper, ...]) -> str:
        lines = [
            "damper_elements = []",
            "",
            "def _global_axis_orient():",
            "    return (1.0, 0.0, 0.0, 0.0, 1.0, 0.0)",
            "",
        ]
        for index, damper in enumerate(dampers, start=1):
            mat_tag = 9000 + index
            ele_tag = 9100 + index
            direction = opensees_direction(damper.direction)
            lines.extend(
                opensees_damper_block(
                    self.damper_module,
                    damper,
                    mat_tag,
                    ele_tag,
                    direction,
                    self.damper_c_scale,
                )
            )
        return "\n".join(lines)

    def _undamped_damper_commands(self) -> str:
        return "\n".join(
            [
                "damper_elements = []",
                "# Undamped baseline: tower-girder dampers are intentionally omitted",
            ]
        )

    def _build_execution_command(self, case_dir: Path) -> list[str]:
        if self.command_stream_path is None:
            raise RuntimeError("Command stream must be written before building the OpenSees command")
        if self.use_sim:
            return [
                self.python_executable,
                "-m",
                "sim",
                "--json",
                "run",
                str(self.command_stream_path.resolve()),
                "--solver",
                "openseespy",
            ]
        return [self.python_executable, str(self.command_stream_path.resolve())]

    def _execution_cwd(self, case_dir: Path) -> Path:
        if self.use_sim and self.sim_workdir is not None:
            return self.sim_workdir
        return case_dir

    def _execution_log_path(self, case_dir: Path) -> Path:
        return case_dir / "opensees_execution.log"

    def _execution_env(self, case_dir: Path) -> dict[str, str] | None:
        if not self.use_sim:
            return None
        sim_home = case_dir / "_sim_home"
        temp_dir = case_dir / "_tmp"
        pycache_dir = case_dir / "_pycache"
        mpl_config_dir = case_dir / "_mpl_config"
        cache_dir = case_dir / "_cache"
        sim_home.mkdir(parents=True, exist_ok=True)
        temp_dir.mkdir(parents=True, exist_ok=True)
        pycache_dir.mkdir(parents=True, exist_ok=True)
        mpl_config_dir.mkdir(parents=True, exist_ok=True)
        cache_dir.mkdir(parents=True, exist_ok=True)
        return {
            "SIM_HOME": str(sim_home.resolve()),
            "TMP": str(temp_dir.resolve()),
            "TEMP": str(temp_dir.resolve()),
            "PYTHONPYCACHEPREFIX": str(pycache_dir.resolve()),
            "MPLCONFIGDIR": str(mpl_config_dir.resolve()),
            "XDG_CACHE_HOME": str(cache_dir.resolve()),
        }
