"""OpenSeesPy in-process solver backend."""

from __future__ import annotations

import importlib.util
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import sys
from typing import Any

from pyansys_bridge.core.command_stream import (
    CommandModule,
    CommandStreamAssembler,
    default_command_modules,
    roles_for_load_case,
)
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
from pyansys_bridge.core.progress_sink import safe_progress_filename
from pyansys_bridge.core.result_summary import (
    SUMMARY_FILENAME,
    TIMESERIES_FILENAME,
    dissipated_energy_from_relative_response,
    ensure_solver_summary,
)
from pyansys_bridge.core.solver_interface import (
    COMMON_PRODUCTION_SOLVER_FEATURES,
    COMMON_SOLVER_WORKFLOW_ROLES,
    SolverInterface,
)
from pyansys_bridge.core.workspace import CaseWorkspace
from pyansys_bridge.models import (
    BridgeModel,
    DamperParams,
    DamperPlacement,
    LoadCase,
    RealizableDamper,
    common_physical_count,
    split_total_damper_params,
    tower_girder_layout,
)


class OpenSeesPyInProcSolver(SolverInterface):
    """Execute assembled OpenSeesPy command streams in the current Python process."""

    solver_name = "openseespy_inproc"
    file_extension = ".py"

    def __init__(
        self,
        output_dir: str | Path = "output/openseespy_inproc",
        modules: list[CommandModule] | None = None,
        include_modal: bool = False,
        damper_module: str = "damper_viscous",
        physical_count_per_tower: int = 1,
        damper_placements: tuple[DamperPlacement, ...] | None = None,
        omit_dampers: bool = False,
        cleanup: str = "never",
        model_path: str | None = None,
        execution_mode: str = "run",
        execution_timeout_s: float | None = None,
        postprocessor: object | None = None,
        damper_calibration: dict[str, object] | None = None,
        response_nodes: tuple[int, ...] | list[int] = (36, 107),
        response_dof: int = 1,
        response_elements: tuple[int, ...] | list[int] = (),
        response_element_component: int = 0,
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
        progress_dir: str | Path | None = None,
        progress_case_id: str | None = None,
    ) -> None:
        super().__init__()
        if not response_nodes:
            raise ValueError("response_nodes must contain at least one OpenSees node")
        if response_dof < 1 or response_dof > 6:
            raise ValueError("response_dof must be in the OpenSees range 1..6")
        if response_element_component < 0 or response_element_component > 2:
            raise ValueError("response_element_component must be 0(X)/1(Y)/2(Z)")
        if not tower_base_nodes:
            raise ValueError("tower_base_nodes must contain at least one OpenSees node")
        if execution_mode != "run":
            raise ValueError("openseespy_inproc only supports execution_mode='run'")
        self.output_dir = Path(output_dir)
        self.modules = modules if modules is not None else default_command_modules()
        self.include_modal = include_modal
        self.damper_module = damper_module
        self.omit_dampers = bool(omit_dampers)
        self.placements = (
            tuple(damper_placements)
            if damper_placements is not None
            else tower_girder_layout(physical_count_per_tower)
        )
        self.physical_count_per_tower = common_physical_count(self.placements)
        self.cleanup_policy = cleanup
        self.execution_mode = execution_mode
        self.execution_timeout_s = execution_timeout_s
        self.postprocessor = postprocessor
        self.damper_calibration = dict(damper_calibration or {})
        self.model_path = model_path or str(
            Path("bridge_models/stbridge_opensees/stbridge_opensees_modal_builder.py").resolve()
        )
        self.response_nodes = tuple(int(node) for node in response_nodes)
        self.response_dof = int(response_dof)
        self.response_elements = tuple(int(element) for element in response_elements)
        if any(element <= 0 for element in self.response_elements):
            raise ValueError("response_elements values must be positive OpenSees element tags")
        if len(set(self.response_elements)) != len(self.response_elements):
            # 重复单元号会写出同名 CSV 列，DictReader 会静默丢弃先出现的那一列。
            raise ValueError("response_elements must not contain duplicates")
        self.response_element_component = int(response_element_component)
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
        # 只用于观测上报，刻意不参与 case_fingerprint/design_metadata：
        # 否则开关进度会被当成不同算例，破坏缓存复用与归档比对。
        self.progress_dir = None if progress_dir is None else Path(progress_dir)
        self.progress_case_id = progress_case_id
        self._configured_operation_load_nodes = operation_load_nodes_from_model(None)
        self.command_stream_path: Path | None = None
        self.command_stream_roles: tuple[str, ...] = ()
        self.case_dir: Path | None = None
        self.workspace: CaseWorkspace | None = None
        self._timeseries: dict[str, list[float]] = {}
        self._objectives: dict[str, float] = {}
        self.transient_algorithm_fallback_counts: dict[str, int] = {}

    def prepare_model(self, bridge_model: BridgeModel) -> None:
        self.bridge_model = bridge_model
        self._configured_operation_load_nodes = operation_load_nodes_from_model(bridge_model)

    def set_damper_params(self, params: DamperParams) -> None:
        self.damper_params = params

    def apply_load_case(self, load_case: LoadCase) -> None:
        self.load_case = load_case

    def solve(self) -> None:
        if self.bridge_model is None or self.damper_params is None or self.load_case is None:
            raise RuntimeError("Model, load case, and damper parameters must be set before solve")

        ops = _import_openseespy()
        self.command_stream_roles = roles_for_load_case(
            self.load_case,
            include_modal=self.include_modal,
            solver=self.solver_name,
        )
        rendered_script = CommandStreamAssembler(self.modules).render_to_string(
            "opensees",
            self.command_stream_roles,
            context=self._command_context(),
            module_names={"damper": self.damper_module},
        )
        workspace = CaseWorkspace(
            root=self.output_dir,
            case_id=self.build_case_id(self.bridge_model, self.load_case, self.damper_params),
            solver_name=self.solver_name,
            file_extension=self.file_extension,
            cleanup=self.cleanup_policy,
        )
        self.workspace = workspace
        self.case_dir = workspace.prepare()
        self.command_stream_path = workspace.write_command_stream(rendered_script)
        _remove_stale_standard_outputs(self.case_dir)
        self._timeseries = {}
        self._objectives = {}
        self.transient_algorithm_fallback_counts = {}

        success = False
        ops.wipe()
        try:
            namespace = {
                "ops": ops,
                "__name__": "__openseespy_inproc__",
                "__file__": str(self.command_stream_path),
            }
            exec(compile(rendered_script, str(self.command_stream_path), "exec"), namespace)
            fallback_counts = namespace.get("transient_algorithm_fallback_counts", {})
            self.transient_algorithm_fallback_counts = {
                str(name): int(count)
                for name, count in dict(fallback_counts).items()
            }
            self._load_standard_outputs(self.case_dir)
            success = True
        finally:
            ops.wipe()
            workspace.cleanup_after(success=success)

    def extract_timeseries(self) -> dict[str, list[float]]:
        return dict(self._timeseries)

    def extract_objectives(self) -> dict[str, float]:
        return dict(self._objectives)

    def cleanup(self) -> None:
        try:
            ops = _import_openseespy()
        except RuntimeError:
            return
        ops.wipe()

    def case_fingerprint(self) -> str:
        payload = repr(
            {
                "backend": "openseespy_inproc",
                "model_path": self.model_path,
                "include_modal": self.include_modal,
                "damper_module": self.damper_module,
                "omit_dampers": self.omit_dampers,
                "placements": [
                    (placement.name, placement.node_i, placement.node_j, placement.direction, placement.physical_count)
                    for placement in self.placements
                ],
                "response_nodes": self.response_nodes,
                "response_dof": self.response_dof,
                "response_elements": self.response_elements,
                "response_element_component": self.response_element_component,
                "tower_base_nodes": self.tower_base_nodes,
                "tower_base_element_node_map": self.tower_base_element_node_map,
                "tower_base_shear_inertia_element_mass_densities": self.tower_base_shear_inertia_element_mass_densities,
                "tower_base_shear_dofs": self.tower_base_shear_dofs,
                "tower_base_moment_dofs": self.tower_base_moment_dofs,
                "diagnostic_nodes": self.diagnostic_nodes,
                "diagnostic_pairs": self.diagnostic_pairs,
                "damping_ratio": self.damping_ratio,
                "opensees_system": self.opensees_system,
                "opensees_system_args": self.opensees_system_args,
                "rayleigh_frequency_a_hz": self.rayleigh_frequency_a_hz,
                "rayleigh_frequency_b_hz": self.rayleigh_frequency_b_hz,
                "damper_c_scale": self.damper_c_scale,
                "damper_implementation_contract": OPENSEES_DAMPER_IMPLEMENTATION_CONTRACT,
            }
        )
        return sha256(payload.encode("utf-8")).hexdigest()[:12]

    def design_metadata(self) -> dict[str, object]:
        metadata = {
            "backend": "openseespy_inproc",
            "model_path": self.model_path,
            "damper_module": self.damper_module,
            "omit_dampers": self.omit_dampers,
            "include_modal": self.include_modal,
            "execution_mode": self.execution_mode,
            "physical_count_per_tower": self.physical_count_per_tower,
            "execution_timeout_s": self.execution_timeout_s,
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
            **{
                key: list(value)
                for key, value in self._configured_operation_load_nodes.items()
                if value is not None
            },
        }
        if self.response_elements:
            metadata["response_elements"] = list(self.response_elements)
            metadata["response_element_component"] = self.response_element_component
        if self.damper_calibration:
            metadata["damper_calibration"] = dict(self.damper_calibration)
        return metadata

    def solver_capability(self) -> dict[str, object]:
        return {
            "type": "openseespy_inproc",
            "backend": "openseespy_inproc",
            "primary_role": "full_workflow_solver",
            "recommended_for": list(COMMON_SOLVER_WORKFLOW_ROLES),
            "workflow_roles": list(COMMON_SOLVER_WORKFLOW_ROLES),
            "validation_peer": "ansys",
            "supported_features": [
                *COMMON_PRODUCTION_SOLVER_FEATURES,
                "openseespy_in_process_run",
                "openseespy_command_stream",
                "serial_case_execution",
                "multiprocessing_doe_pool",
                "case_step_progress",
            ],
            "not_implemented": [
                "thread_parallel_execution",
                "ansys_dpf_postprocess",
                "mapdl_batch_run",
                "apdl_command_stream",
            ],
        }

    def run_analysis(
        self,
        bridge_model: BridgeModel,
        load_case: LoadCase,
        damper_params: DamperParams,
    ):
        result = super().run_analysis(bridge_model, load_case, damper_params)
        result.metadata["is_verified_solver_output"] = result.status == "completed"
        if self.command_stream_path is not None and self.command_stream_path.exists():
            result.metadata["command_stream"] = {
                "path": str(self.command_stream_path),
                "sha256": _file_sha256(self.command_stream_path),
                "roles": list(self.command_stream_roles),
                "damper_module": self.damper_module,
                "dry_run": False,
            }
        if self.case_dir is not None and (self.case_dir / SUMMARY_FILENAME).exists():
            result.metadata["solver_summary"] = {
                "path": str(self.case_dir / SUMMARY_FILENAME),
                "sha256": _file_sha256(self.case_dir / SUMMARY_FILENAME),
                "objective_names": sorted(result.objectives),
            }
        if self.transient_algorithm_fallback_counts:
            result.metadata["transient_algorithm_fallback_counts"] = dict(
                self.transient_algorithm_fallback_counts
            )
        return result

    def _command_context(self) -> dict[str, object]:
        assert self.bridge_model is not None
        assert self.load_case is not None
        assert self.damper_params is not None
        context: dict[str, Any] = self.load_case.command_context()
        earthquake_dof, earthquake_factor = uniform_excitation_axis(self.load_case.direction)
        context.update(
            {
                "model_path": self.model_path,
                "modal_modes": self.bridge_model.metadata.get("modal_modes", 20),
                "gravity_accel_x": self.bridge_model.metadata.get("gravity_accel_x", 0.0),
                "gravity_accel_y": self.bridge_model.metadata.get("gravity_accel_y", -9.81),
                "gravity_accel_z": self.bridge_model.metadata.get("gravity_accel_z", 0.0),
                "gravity_time": self.bridge_model.metadata.get("gravity_time", 1.0),
                "gravity_substeps": self.bridge_model.metadata.get("gravity_substeps", 2),
                "damper_c": self.damper_params.c,
                "damper_alpha": self.damper_params.alpha,
                "damper_stiffness": self.damper_params.stiffness,
                "damper_type": self.damper_module.removeprefix("damper_"),
                "damper_commands": (
                    self._undamped_damper_commands()
                    if self.omit_dampers
                    else self._damper_commands(
                        split_total_damper_params(self.damper_params, self.placements)
                    )
                ),
                "opensees_load_nodes": repr(self._configured_operation_load_nodes),
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
        if self.response_elements:
            # 只在用户点名单元时注入：空元组的 repr 是 "()"，在 Jinja 里是真值，
            # 无条件注入会让默认渲染多出单元内力代码，破坏已归档证据的 sha256。
            context.update(
                {
                    "response_elements": repr(self.response_elements),
                    "response_element_component": self.response_element_component,
                }
            )
        if self.progress_dir is not None:
            case_id = self.progress_case_id or self.build_case_id(
                self.bridge_model,
                self.load_case,
                self.damper_params,
            )
            context.update(
                {
                    "progress_path": self.progress_dir.as_posix(),
                    "progress_case_id": case_id,
                    "progress_filename": safe_progress_filename(case_id),
                }
            )
        return context

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
            lines.extend(
                opensees_damper_block(
                    self.damper_module,
                    damper,
                    mat_tag,
                    ele_tag,
                    opensees_direction(damper.direction),
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

    def _load_standard_outputs(self, case_dir: Path) -> None:
        summary = ensure_solver_summary(case_dir)
        if summary is None:
            raise RuntimeError("OpenSeesPy in-process run completed without summary.json or timeseries.csv")
        objectives = summary.get("objectives")
        if not isinstance(objectives, dict) or not objectives:
            raise RuntimeError("OpenSeesPy in-process summary did not contain solver objectives")
        timeseries = summary.get("timeseries")
        if not isinstance(timeseries, dict):
            timeseries_path = case_dir / TIMESERIES_FILENAME
            if not timeseries_path.exists():
                raise RuntimeError("OpenSeesPy in-process summary did not contain solver timeseries")
            from pyansys_bridge.core.result_summary import load_timeseries_csv

            timeseries = load_timeseries_csv(timeseries_path)
        self._objectives = {key: float(value) for key, value in objectives.items()}
        self._timeseries = {
            key: [float(item) for item in values]
            for key, values in timeseries.items()
        }
        damper_energy: dict[str, Any] | None = None
        if self.command_stream_path is not None:
            damper_energy = dissipated_energy_from_relative_response(
                self.command_stream_path.with_name("tower_girder_relative_response.csv")
            )
            self._objectives["dissipated_energy"] = float(
                damper_energy["dissipated_energy"]
            )
        if not _finite_outputs(self._objectives, self._timeseries):
            raise RuntimeError("OpenSeesPy in-process output contains non-finite values")
        with (case_dir / SUMMARY_FILENAME).open("w", encoding="utf-8") as handle:
            json.dump(
                {
                    "status": "completed",
                    "objectives": self._objectives,
                    "timeseries": self._timeseries,
                    "metadata": ({"damperEnergy": damper_energy} if damper_energy else {}),
                },
                handle,
                ensure_ascii=False,
                indent=2,
            )


def _import_openseespy():
    _prepare_local_openseespy_runtime()
    try:
        import platform

        if not platform.machine():
            platform.machine = lambda: "AMD64"  # type: ignore[assignment]
        import openseespy.opensees as ops
    except Exception as exc:
        raise RuntimeError(f"OpenSeesPy is not available for in-process execution: {exc}") from exc
    return ops


def _prepare_local_openseespy_runtime() -> None:
    runtime_root = _local_openseespy_runtime_root()
    if runtime_root is None:
        return
    runtime_path = str(runtime_root)
    if runtime_path in sys.path:
        sys.path.remove(runtime_path)
    sys.path.insert(0, runtime_path)
    _load_runtime_sitecustomize(runtime_root)


def _local_openseespy_runtime_root() -> Path | None:
    configured_runtime = os.environ.get("OPENSEESPY_USER300_RUNTIME")
    if configured_runtime:
        configured_path = Path(configured_runtime).resolve()
        runtime_root = configured_path.parent if configured_path.name.lower() == "openseespy" else configured_path
    else:
        runtime_root = Path(__file__).resolve().parents[2] / "bridge_models" / "stbridge_opensees"
    if (runtime_root / "openseespy" / "__init__.py").exists():
        return runtime_root
    return None


def _load_runtime_sitecustomize(runtime_root: Path) -> None:
    sitecustomize_path = runtime_root / "sitecustomize.py"
    if not sitecustomize_path.exists():
        return
    module_name = "_stbridge_opensees_sitecustomize_" + sha256(
        str(sitecustomize_path).encode("utf-8")
    ).hexdigest()[:12]
    if module_name in sys.modules:
        return
    spec = importlib.util.spec_from_file_location(module_name, sitecustomize_path)
    if spec is None or spec.loader is None:
        return
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    # Windows 下必须在导入 opensees.pyd 前注册 DLL 搜索路径。
    spec.loader.exec_module(module)


def _finite_outputs(
    objectives: dict[str, float],
    timeseries: dict[str, list[float]],
) -> bool:
    return all(math.isfinite(float(value)) for value in objectives.values()) and all(
        math.isfinite(float(value))
        for values in timeseries.values()
        for value in values
    )


def _remove_stale_standard_outputs(case_dir: Path) -> None:
    for filename in (SUMMARY_FILENAME, TIMESERIES_FILENAME):
        path = case_dir / filename
        if path.exists():
            path.unlink()


def _file_sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()
