"""Dry-run solver adapter that writes assembled command streams."""

from __future__ import annotations

from hashlib import sha256
from inspect import signature
from pathlib import Path

from pyansys_bridge.core.ansys_command_stream import write_apdl_command_stream
from pyansys_bridge.core.command_runner import CommandExecutionResult, CommandRunner, SubprocessCommandRunner
from pyansys_bridge.core.command_stream import (
    CommandModule,
    CommandStreamAssembler,
    default_command_modules,
    roles_for_load_case,
    template_command_modules,
)
from pyansys_bridge.core.mock_solver import MockSolver
from pyansys_bridge.core.postprocessor import (
    AnsysDpfMultiCsvPostprocessor,
    AnsysDpfRstPostprocessor,
    CommandPostprocessor,
    CompositePostprocessor,
    CsvTimeseriesPostprocessor,
    FilePostprocessor,
    OpenSeesCsvPostprocessor,
    PostprocessContext,
    SolverPostprocessor,
)
from pyansys_bridge.core.result_extractor import apply_run_mode_solver_outputs
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


class CommandStreamDryRunSolver(MockSolver):
    """Shared dry-run adapter for solver-specific command stream generation."""

    file_extension = ".txt"

    def __init__(
        self,
        output_dir: str | Path = "output/command_streams",
        modules: list[CommandModule] | None = None,
        include_modal: bool = False,
        damper_module: str = "damper_viscous",
        physical_count_per_tower: int = 1,
        damper_placements: tuple[DamperPlacement, ...] | None = None,
        omit_dampers: bool = False,
        damper_calibration: dict[str, object] | None = None,
        execution_mode: str = "dry_run",
        command_runner: CommandRunner | None = None,
        postprocessor: SolverPostprocessor | None = None,
        execution_timeout_s: float | None = None,
        cleanup: str = "never",
        cleanup_artifact_suffixes_on_success: list[str] | tuple[str, ...] | None = None,
    ) -> None:
        super().__init__()
        if execution_mode not in {"dry_run", "run"}:
            raise ValueError("execution_mode must be 'dry_run' or 'run'")
        self.output_dir = Path(output_dir)
        self.modules = _command_modules_with_required_defaults(modules) if modules is not None else default_command_modules()
        self.include_modal = include_modal
        self.damper_module = damper_module
        self.omit_dampers = bool(omit_dampers)
        self.placements = (
            tuple(damper_placements)
            if damper_placements is not None
            else tower_girder_layout(physical_count_per_tower)
        )
        self.physical_count_per_tower = common_physical_count(self.placements)
        self.damper_calibration = dict(damper_calibration or {})
        self.execution_mode = execution_mode
        self.command_runner = command_runner or SubprocessCommandRunner()
        self.postprocessor = postprocessor
        self.execution_timeout_s = execution_timeout_s
        self.command_stream_path: Path | None = None
        self.command_stream_roles: tuple[str, ...] = ()
        self.execution_result: CommandExecutionResult | None = None
        self.case_dir: Path | None = None
        self.workspace: CaseWorkspace | None = None
        self.cleanup_policy = cleanup
        self.cleanup_artifact_suffixes_on_success = tuple(
            suffix if str(suffix).startswith(".") else f".{suffix}"
            for suffix in (cleanup_artifact_suffixes_on_success or ())
        )

    def solve(self) -> None:
        super().solve()
        assert self.bridge_model is not None
        assert self.load_case is not None
        assert self.damper_params is not None

        self.command_stream_roles = roles_for_load_case(
            self.load_case,
            include_modal=self.include_modal,
            solver=self.solver_name,
        )
        context = self._command_context()
        stream = CommandStreamAssembler(self.modules).assemble(
            self.solver_name,
            self.command_stream_roles,
            context=context,
            module_names={"damper": self.damper_module},
        )
        case_id = self.build_case_id(self.bridge_model, self.load_case, self.damper_params)
        workspace = CaseWorkspace(
            root=self.output_dir,
            case_id=case_id,
            solver_name=self.solver_name,
            file_extension=self.file_extension,
            cleanup=self.cleanup_policy,
        )
        self.workspace = workspace
        case_dir = workspace.prepare()
        self.case_dir = case_dir
        if self.solver_name == "ansys" or self.file_extension.lower() == ".apdl":
            self.command_stream_path = write_apdl_command_stream(workspace, stream)
        else:
            self.command_stream_path = workspace.write_command_stream(stream)
        self.execution_result = None

        if self.execution_mode == "run":
            self._prepare_execution_files(case_dir, context)
            command = self._build_execution_command(case_dir)
            run_kwargs = {
                "cwd": self._execution_cwd(case_dir),
                "timeout_s": self.execution_timeout_s,
                "log_path": self._execution_log_path(case_dir),
            }
            execution_env = self._execution_env(case_dir)
            if execution_env is not None and _runner_accepts_env(self.command_runner):
                run_kwargs["env"] = execution_env
            self.execution_result = self.command_runner.run(command, **run_kwargs)
            if self.execution_result.returncode != 0:
                raise RuntimeError(
                    f"{self.solver_name} command failed with code {self.execution_result.returncode}"
                )
            if self.postprocessor is not None:
                self.postprocessor.postprocess(
                    PostprocessContext(
                        solver=self.solver_name,
                        case_dir=case_dir,
                        command_stream_path=self.command_stream_path,
                        bridge_model=self.bridge_model,
                        load_case=self.load_case,
                        damper_params=self.damper_params,
                        execution_result=self.execution_result,
                    )
                )

    def run_analysis(
        self,
        bridge_model: BridgeModel,
        load_case: LoadCase,
        damper_params: DamperParams,
    ):
        result = super().run_analysis(bridge_model, load_case, damper_params)
        result.metadata["is_verified_solver_output"] = False
        if self.command_stream_path is not None:
            result.metadata.update(
                {
                    "command_stream": {
                        "path": str(self.command_stream_path),
                        "sha256": _file_sha256(self.command_stream_path),
                        "roles": list(self.command_stream_roles),
                        "damper_module": self.damper_module,
                        "dry_run": self.execution_mode == "dry_run",
                    }
                }
            )
            postprocessor_contract = _postprocessor_contract(self.postprocessor)
            if postprocessor_contract is not None:
                result.metadata["command_stream"]["postprocessor_contract"] = postprocessor_contract
            if self.execution_result is not None:
                result.metadata["command_execution"] = self.execution_result.to_dict()
            if (
                self.execution_mode == "run"
                and self.case_dir is not None
                and result.status == "completed"
            ):
                apply_run_mode_solver_outputs(result, self.case_dir, self.solver_name)
            result.metadata["is_verified_solver_output"] = _is_run_mode_solver_output(result)
            if (
                result.metadata["is_verified_solver_output"]
                and self.case_dir is not None
                and self.cleanup_artifact_suffixes_on_success
            ):
                result.metadata["artifact_cleanup"] = _remove_case_artifacts(
                    self.case_dir,
                    self.cleanup_artifact_suffixes_on_success,
                )
        return result

    def _command_context(self) -> dict[str, object]:
        assert self.bridge_model is not None
        assert self.load_case is not None
        assert self.damper_params is not None
        context = self.load_case.command_context()
        context.update(
            {
                "model_path": self._model_path_for_stream(),
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
                "damper_module": self.damper_module,
                "omit_dampers": self.omit_dampers,
                "damper_commands": (
                    self._undamped_damper_commands()
                    if self.omit_dampers
                    else self._damper_commands(
                        split_total_damper_params(self.damper_params, self.placements)
                    )
                ),
            }
        )
        return context

    def case_fingerprint(self) -> str:
        payload = repr(self._cache_metadata())
        return sha256(payload.encode("utf-8")).hexdigest()[:12]

    def design_metadata(self) -> dict[str, object]:
        metadata = {
            "damper_module": self.damper_module,
            "omit_dampers": self.omit_dampers,
            "include_modal": self.include_modal,
            "gravity": _gravity_contract(self.bridge_model),
            "load_metadata": dict(self.load_case.metadata) if self.load_case is not None else {},
            "physical_count_per_tower": self.physical_count_per_tower,
            "damper_placements": [_placement_contract(placement) for placement in self.placements],
            "execution_mode": self.execution_mode,
        }
        if self.damper_calibration:
            metadata["damper_calibration"] = dict(self.damper_calibration)
        return metadata

    def _cache_metadata(self) -> dict[str, object]:
        metadata = _cache_fingerprint_payload(dict(self.design_metadata()))
        metadata.pop("damper_calibration", None)
        metadata["module_contract"] = _module_contract(self.modules, self.solver_name)
        postprocessor_contract = _postprocessor_contract(self.postprocessor)
        if postprocessor_contract is not None:
            metadata["postprocessor_contract"] = postprocessor_contract
        return metadata

    def _model_path_for_stream(self) -> str:
        assert self.bridge_model is not None
        return self.bridge_model.source_path

    def _damper_commands(self, dampers: tuple[RealizableDamper, ...]) -> str:
        raise NotImplementedError

    def _undamped_damper_commands(self) -> str:
        raise NotImplementedError

    def _build_execution_command(self, case_dir: Path) -> list[str]:
        raise NotImplementedError

    def _execution_cwd(self, case_dir: Path) -> Path:
        return case_dir

    def _execution_log_path(self, case_dir: Path) -> Path:
        return case_dir / f"{self.solver_name}_execution.log"

    def _execution_env(self, case_dir: Path) -> dict[str, str] | None:
        return None

    def _prepare_execution_files(self, case_dir: Path, context: dict[str, object]) -> None:
        return None


def _postprocessor_contract(postprocessor: SolverPostprocessor | None) -> dict[str, object] | None:
    if postprocessor is None:
        return None
    if isinstance(postprocessor, CsvTimeseriesPostprocessor):
        contract = {
            "type": "csv_timeseries",
            "source": str(postprocessor.source),
            "columns": _columns_contract(postprocessor.columns),
        }
        if postprocessor.optional_columns:
            contract["optional_columns"] = _columns_contract(postprocessor.optional_columns)
        return contract
    if isinstance(postprocessor, AnsysDpfMultiCsvPostprocessor):
        return {
            "type": "ansys_dpf_multi_csv",
            "job_name": postprocessor.job_name,
            "output_dir": None if postprocessor.output_dir is None else str(postprocessor.output_dir),
        }
    if isinstance(postprocessor, OpenSeesCsvPostprocessor):
        contract = {
            "type": "opensees_csv",
            "source": str(postprocessor.source),
            "columns": _columns_contract(postprocessor.columns),
        }
        if postprocessor.optional_columns:
            contract["optional_columns"] = _columns_contract(postprocessor.optional_columns)
        return contract
    if isinstance(postprocessor, AnsysDpfRstPostprocessor):
        return {
            "type": "ansys_dpf_rst",
            "response_nodes": list(postprocessor.response_nodes),
            "response_component": postprocessor.response_component,
            "cumulative_displacement_node": postprocessor.cumulative_displacement_node,
            "damper_pairs": [list(pair) for pair in postprocessor.damper_pairs],
            "damper_component": postprocessor.damper_component,
            "damper_c_scale": postprocessor.damper_c_scale,
            "damper_force_policy": "physical_elements_only",
        }
    if isinstance(postprocessor, CommandPostprocessor):
        return {
            "type": "command",
            "command": list(postprocessor.command),
            "timeout_s": postprocessor.timeout_s,
            "log_name": postprocessor.log_name,
        }
    if isinstance(postprocessor, CompositePostprocessor):
        return {
            "type": "composite",
            "steps": [_postprocessor_contract(step) for step in postprocessor.steps],
        }
    if isinstance(postprocessor, FilePostprocessor):
        return {
            "type": "file",
            "summary_source": None
            if postprocessor.summary_source is None
            else str(postprocessor.summary_source),
            "timeseries_source": None
            if postprocessor.timeseries_source is None
            else str(postprocessor.timeseries_source),
        }
    return {
        "type": f"{postprocessor.__class__.__module__}.{postprocessor.__class__.__qualname__}",
    }


def _remove_case_artifacts(case_dir: Path, suffixes: tuple[str, ...]) -> dict[str, object]:
    normalized = {suffix.lower() for suffix in suffixes}
    removed_names = []
    removed_bytes = 0
    for path in sorted(case_dir.iterdir()):
        if not path.is_file() or path.suffix.lower() not in normalized:
            continue
        removed_names.append(path.name)
        removed_bytes += path.stat().st_size
        path.unlink()
    return {
        "policy": "verified_success",
        "suffixes": sorted(normalized),
        "removed_names": removed_names,
        "removed_bytes": removed_bytes,
    }


def _cache_fingerprint_payload(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: _cache_fingerprint_payload(item)
            for key, item in value.items()
            if key != "load_calibration"
        }
    if isinstance(value, list):
        return [_cache_fingerprint_payload(item) for item in value]
    return value


def _command_modules_with_required_defaults(modules: list[CommandModule]) -> list[CommandModule]:
    existing = {(module.solver.lower(), module.role) for module in modules}
    additions = [
        module
        for module in template_command_modules()
        if module.role == "gravity" and (module.solver.lower(), module.role) not in existing
    ]
    return [*modules, *additions]


def _runner_accepts_env(command_runner: CommandRunner) -> bool:
    try:
        parameters = signature(command_runner.run).parameters
    except (TypeError, ValueError):
        return True
    return "env" in parameters or any(
        parameter.kind == parameter.VAR_KEYWORD for parameter in parameters.values()
    )


def _columns_contract(columns: dict[str, str | tuple[str, ...]]) -> dict[str, str | list[str]]:
    return {
        target: list(source) if isinstance(source, tuple) else source
        for target, source in sorted(columns.items())
    }


def _placement_contract(placement: DamperPlacement) -> dict[str, object]:
    return {
        "name": placement.name,
        "node_i": placement.node_i,
        "node_j": placement.node_j,
        "direction": placement.direction,
        "physical_count": placement.physical_count,
    }


def _gravity_contract(model: BridgeModel | None) -> dict[str, object]:
    metadata = {} if model is None else model.metadata
    return {
        "acceleration": [
            float(metadata.get("gravity_accel_x", 0.0)),
            float(metadata.get("gravity_accel_y", -9.81)),
            float(metadata.get("gravity_accel_z", 0.0)),
        ],
        "time": float(metadata.get("gravity_time", 1.0)),
        "substeps": metadata.get("gravity_substeps", 2),
        "state_reused_by": ["modal", "transient"],
    }


def _module_contract(modules: list[CommandModule], solver_name: str) -> list[dict[str, str]]:
    normalized_solver = solver_name.lower()
    return [
        {
            "solver": module.solver,
            "name": module.name,
            "role": module.role,
            "content": module.content,
        }
        for module in sorted(modules, key=lambda item: (item.role, item.name, item.content))
        if module.solver.lower() == normalized_solver
    ]


def _file_sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _is_run_mode_solver_output(result) -> bool:
    if result.status != "completed":
        return False
    command_stream = result.metadata.get("command_stream", {})
    execution = result.metadata.get("command_execution", {})
    solver_summary = result.metadata.get("solver_summary", {})
    return (
        command_stream.get("dry_run") is False
        and _artifact_hash_matches(command_stream)
        and execution.get("returncode") == 0
        and _execution_references_command_stream(execution, command_stream)
        and _artifact_hash_matches(solver_summary)
        and _solver_summary_lists_objectives(solver_summary, result.objectives)
    )


def _artifact_hash_matches(metadata: dict[str, object]) -> bool:
    path = metadata.get("path")
    expected = metadata.get("sha256")
    if not path or not expected:
        return False
    source = Path(str(path))
    return source.is_file() and sha256(source.read_bytes()).hexdigest() == expected


def _execution_references_command_stream(execution: dict[str, object], command_stream: dict[str, object]) -> bool:
    stream_path = command_stream.get("path")
    command = execution.get("command", ())
    if not stream_path or not isinstance(command, (list, tuple)):
        return False
    resolved_stream = Path(str(stream_path)).resolve()
    return any(Path(str(part)).resolve() == resolved_stream for part in command)


def _solver_summary_lists_objectives(solver_summary: dict[str, object], objectives: dict[str, float]) -> bool:
    objective_names = solver_summary.get("objective_names", ())
    if not objective_names:
        return False
    return set(objectives).issubset({str(name) for name in objective_names})
