"""ANSYS solver adapter."""

from __future__ import annotations

from pathlib import Path

from pyansys_bridge.core.ansys_damper import (
    ANSYS_DAMPER_C_SCALE,
    ansys_damper_commands,
    ansys_damper_elements_contract,
    ansys_damper_params_contract,
    ansys_userrc_source,
    uses_userrc_regularization,
    uses_user300,
)
from pyansys_bridge.core.ansys_load_targets import (
    build_operation_load_targets,
    operation_load_target_metadata,
)
from pyansys_bridge.core.ansys_load_rendering import (
    ansys_load_table_commands,
    ansys_solver_execution_commands,
    legacy_vread_rows,
    read_scalar_record_values,
)
from pyansys_bridge.core.command_stream_solver import CommandStreamDryRunSolver
from pyansys_bridge.core.mapdl_manager import MapdlBatchConfig, MapdlBatchManager
from pyansys_bridge.core.progress_sink import register_ansys_output_probe
from pyansys_bridge.core.solver_interface import (
    COMMON_PRODUCTION_SOLVER_FEATURES,
    COMMON_SOLVER_WORKFLOW_ROLES,
)
from pyansys_bridge.core.workspace import CaseWorkspace
from pyansys_bridge.models import BridgeModel, DamperParams, LoadCase, RealizableDamper


class AnsysSolver(CommandStreamDryRunSolver):
    """ANSYS adapter.

    The current implementation writes an APDL command stream and then returns
    deterministic dry-run responses. Long-running MAPDL execution stays behind
    this boundary until the command stream contracts are stable.
    """

    solver_name = "ansys"
    file_extension = ".apdl"

    def __init__(
        self,
        mapdl_executable: str = "MAPDL.exe",
        nproc: int | None = None,
        mapdl_args: list[str] | tuple[str, ...] | None = None,
        mapdl_memory_mb: int | None = None,
        damper_c_scale: float = ANSYS_DAMPER_C_SCALE,
        interpolate_time_history_tables: bool = False,
        progress_dir: str | Path | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.mapdl_executable = mapdl_executable
        self.nproc = nproc
        self.mapdl_args = tuple(mapdl_args or ())
        self.mapdl_memory_mb = mapdl_memory_mb
        self.damper_c_scale = float(damper_c_scale)
        self.interpolate_time_history_tables = bool(interpolate_time_history_tables)
        # 只读 ansys.out 探针属于观测配置，不进入指纹和工程归档元数据。
        self.progress_dir = None if progress_dir is None else Path(progress_dir)
        self.mapdl_manager = MapdlBatchManager(
            MapdlBatchConfig.from_options(
                executable=self.mapdl_executable,
                args=self.mapdl_args,
                nproc=self.nproc,
                memory_mb=self.mapdl_memory_mb,
            )
        )

    def build_case_id(self, model: BridgeModel, load_case: LoadCase, params: DamperParams) -> str:
        previous_model = self.bridge_model
        previous_load = self.load_case
        previous_params = self.damper_params
        self.bridge_model = model
        self.load_case = load_case
        self.damper_params = params
        try:
            return super().build_case_id(model, load_case, params)
        finally:
            self.bridge_model = previous_model
            self.load_case = previous_load
            self.damper_params = previous_params

    def _damper_commands(self, dampers: tuple[RealizableDamper, ...]) -> str:
        return ansys_damper_commands(self.damper_module, dampers, c_scale=self.damper_c_scale)

    def _undamped_damper_commands(self) -> str:
        return "\n".join(
            [
                "/PREP7",
                "! Undamped baseline: tower-girder dampers are intentionally omitted",
            ]
        )

    def _command_context(self) -> dict[str, object]:
        context = super()._command_context()
        if not self.omit_dampers:
            context["damper_commands"] = self._damper_commands(self._total_damper_units())
        context.update(self._operation_load_targets())
        context["interpolate_time_history_tables"] = self.interpolate_time_history_tables
        context.update(ansys_load_table_commands(context))
        context["ansys_solver_execution_commands"] = ansys_solver_execution_commands(context)
        return context

    def design_metadata(self) -> dict[str, object]:
        metadata = super().design_metadata()
        metadata["damper_placements"] = [
            {
                "name": placement.name,
                "node_i": placement.node_i,
                "node_j": placement.node_j,
                "direction": placement.direction,
                "physical_count": placement.physical_count,
            }
            for placement in self.placements
        ]
        if self.damper_params is not None and not self.omit_dampers:
            realized_dampers = self._total_damper_units()
            metadata["damper_params"] = ansys_damper_params_contract(
                self.damper_params,
                c_scale=self.damper_c_scale,
                physical_count_per_tower=self.physical_count_per_tower,
                realizable_damper_count=len(realized_dampers),
                damper_module=self.damper_module,
            )
            metadata["damper_elements"] = ansys_damper_elements_contract(
                realized_dampers,
                c_scale=self.damper_c_scale,
                damper_module=self.damper_module,
            )
        metadata.update(self._operation_load_target_metadata())
        if self.nproc is not None:
            metadata["nproc"] = self.nproc
        if self.mapdl_args:
            metadata["mapdl_args"] = list(self.mapdl_args)
        if self.mapdl_memory_mb is not None:
            metadata["mapdl_memory_mb"] = self.mapdl_memory_mb
        metadata["interpolate_time_history_tables"] = self.interpolate_time_history_tables
        return metadata

    def solver_capability(self) -> dict[str, object]:
        return {
            "type": "ansys",
            "primary_role": "full_workflow_solver",
            "recommended_for": list(COMMON_SOLVER_WORKFLOW_ROLES),
            "workflow_roles": list(COMMON_SOLVER_WORKFLOW_ROLES),
            "validation_peer": "openseespy_inproc",
            "supported_features": [
                *COMMON_PRODUCTION_SOLVER_FEATURES,
                "apdl_command_stream",
                "mapdl_batch_run",
                "ansys_dpf_postprocess",
                "case_step_progress",
                "ansys_output_time_probe",
            ],
            "not_implemented": [],
            "backend": "mapdl",
        }

    def solve(self) -> None:
        super().solve()
        if (
            self.command_stream_path is not None
            and self.damper_params is not None
            and not self.omit_dampers
            and uses_userrc_regularization(self.damper_module, self.damper_params)
        ):
            (self.command_stream_path.parent / "userrc_regularized_viscous.f").write_text(
                ansys_userrc_source(),
                encoding="utf-8",
            )

    def _total_damper_units(self) -> tuple[RealizableDamper, ...]:
        assert self.damper_params is not None
        return tuple(
            RealizableDamper(
                placement_name=placement.name,
                unit_index=1,
                node_i=placement.node_i,
                node_j=placement.node_j,
                direction=placement.direction,
                params=DamperParams(
                    c=self.damper_params.c,
                    alpha=self.damper_params.alpha,
                    stiffness=self.damper_params.stiffness,
                    regularization_velocity=self.damper_params.regularization_velocity,
                ),
            )
            for placement in self.placements
        )

    def _operation_load_targets(self) -> dict[str, object]:
        model_metadata = {} if self.bridge_model is None else self.bridge_model.metadata
        load_context = None if self.load_case is None else self.load_case.command_context()
        return build_operation_load_targets(model_metadata, load_context)

    def _operation_load_target_metadata(self) -> dict[str, object]:
        targets = self._operation_load_targets()
        load_context = None if self.load_case is None else self.load_case.command_context()
        return operation_load_target_metadata(targets, load_context)

    def _build_execution_command(self, case_dir: Path) -> list[str]:
        if self.command_stream_path is None:
            raise RuntimeError("Command stream must be written before building the MAPDL command")
        return self.mapdl_manager.build_batch_command(self._workspace_for_case(case_dir))

    def _execution_log_path(self, case_dir: Path) -> Path:
        return self.mapdl_manager.execution_log_path(self._workspace_for_case(case_dir))

    def _execution_env(self, case_dir: Path) -> dict[str, str]:
        env = self.mapdl_manager.execution_env(self._workspace_for_case(case_dir))
        if uses_user300(self.damper_module):
            user_dir = Path(__file__).resolve().parents[2] / "ansys" / "build_userelem"
            env["ANS_USER_PATH"] = str(user_dir)
            env["ANS_USER_PATH_242"] = str(user_dir)
        return env

    def _prepare_execution_files(self, case_dir: Path, context: dict[str, object]) -> None:
        super()._prepare_execution_files(case_dir, context)
        if self.progress_dir is not None:
            register_ansys_output_probe(
                self.progress_dir,
                case_dir.name,
                output_path=case_dir / "ansys.out",
                dt=context.get("load_dt") or 0.0,
                duration=context.get("load_duration") or 0.0,
            )
        if "earthquake_name" not in context or context.get("earthquake_path") is None:
            return
        source_path = context.get("earthquake_path")
        values = read_scalar_record_values(Path(str(source_path)))
        rows = legacy_vread_rows(context, len(values))
        target = case_dir / "ACCE.txt"
        target.write_text(
            "\n".join(f"{values[min(index, len(values) - 1)]:.9f}" for index in range(rows)) + "\n",
            encoding="utf-8",
        )

    def _workspace_for_case(self, case_dir: Path) -> CaseWorkspace:
        if self.workspace is not None and self.workspace.case_dir == case_dir:
            return self.workspace
        return CaseWorkspace(
            root=case_dir.parent,
            case_id=case_dir.name,
            solver_name=self.solver_name,
            file_extension=self.file_extension,
            cleanup=self.cleanup_policy,
        )


def run_case(
    bridge_model: BridgeModel,
    load_case: LoadCase,
    damper_params: DamperParams,
    **solver_kwargs,
):
    """Run one ANSYS case through the compatibility facade."""

    return AnsysSolver(**solver_kwargs).run_analysis(bridge_model, load_case, damper_params)
