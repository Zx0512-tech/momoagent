"""Postprocessing hook for solver run-mode outputs."""

from __future__ import annotations

import csv
from dataclasses import dataclass
import gc
import os
from pathlib import Path
import re
from shutil import copyfile
from threading import Lock
from typing import Protocol

from pyansys_bridge.core.ansys_damper import ANSYS_DAMPER_C_SCALE
from pyansys_bridge.core.command_runner import CommandExecutionResult, CommandRunner, SubprocessCommandRunner
from pyansys_bridge.core.result_summary import SUMMARY_FILENAME, TIMESERIES_FILENAME
from pyansys_bridge.models import BridgeModel, DamperParams, LoadCase


ANSYS_DPF_TIMESERIES_COLUMNS = {
    "time": "Time",
    "displacement": ("Node36_UX", "Node107_UX"),
    "acceleration": ("Node36_AX", "Node107_AX"),
    "damper_force": ("Elem2000_FX", "Elem2010_FX", "Elem3000_FX", "Elem3010_FX"),
    "damper_stroke": ("Node517_UX", "Node518_UX", "Node520_UX", "Node521_UX"),
}

ANSYS_DPF_OPTIONAL_TIMESERIES_COLUMNS = {
    "tower_base_shear": ("Tower_FX", "Tower_FY"),
    "tower_base_moment": ("Tower_MY", "Tower_MZ"),
}

DEFAULT_ANSYS_DAMPER_PAIRS = (
    ("north_tower_girder_1", 3001, 36, 517),
    ("north_tower_girder_2", 3002, 36, 518),
    ("south_tower_girder_1", 3003, 107, 520),
    ("south_tower_girder_2", 3004, 107, 521),
)
DEFAULT_STBRIDGE_TOWER_BASE_NODES = (
    494,
    495,
    511,
    512,
    525,
    528,
    530,
    532,
    534,
    536,
    538,
    540,
    542,
    544,
    547,
    550,
)

DEFAULT_STBRIDGE_TOWER_BASE_ELEMENT_NODE_MAP = (
    (835, 494),
    (836, 495),
    (837, 511),
    (838, 512),
)

DEFAULT_STBRIDGE_TOWER_BASE_ELEMENT_END_MAP = (
    (835, 492, 494),
    (836, 493, 495),
    (837, 509, 511),
    (838, 510, 512),
)

ANSYS_DPF_MULTI_CSV_SPECS = {
    "displacement": ("Girder_Disp", ("Node36_UX", "Node107_UX")),
    "damper_force": (
        "Damper_Force",
        ("Elem2000_FX", "Elem2010_FX", "Elem3000_FX", "Elem3010_FX"),
    ),
    "damper_stroke": (
        "Damper_Disp",
        ("Node517_UX", "Node518_UX", "Node520_UX", "Node521_UX"),
    ),
}

ANSYS_DPF_OPTIONAL_MULTI_CSV_SPECS = {
    "acceleration": ("Girder_Accel", ("Node36_AX", "Node107_AX")),
    "tower_base_shear": ("Tower_Force", ("Tower_FX", "Tower_FY")),
    "tower_base_moment": ("Tower_Force", ("Tower_MY", "Tower_MZ")),
}

OPENSEES_TIMESERIES_COLUMNS = {
    "time": "time",
    "displacement": "displacement",
    "acceleration": "acceleration",
    "damper_force": "damper_force",
    "damper_stroke": "damper_stroke",
}

OPENSEES_OPTIONAL_TIMESERIES_COLUMNS = {
    "absolute_displacement": "absolute_displacement",
    "displacement_increment": "displacement_increment",
    "tower_base_moment": "tower_base_moment",
    "tower_base_shear": "tower_base_shear",
}

_ANSYS_DPF_RST_POSTPROCESS_LOCK = Lock()


@dataclass(frozen=True)
class PostprocessContext:
    """Inputs available after a solver command finishes successfully."""

    solver: str
    case_dir: Path
    command_stream_path: Path
    bridge_model: BridgeModel
    load_case: LoadCase
    damper_params: DamperParams
    execution_result: CommandExecutionResult


class SolverPostprocessor(Protocol):
    """Convert raw solver outputs into summary.json or timeseries.csv."""

    def postprocess(self, context: PostprocessContext) -> None:
        """Write compact outputs under context.case_dir."""


@dataclass(frozen=True)
class CompositePostprocessor:
    """Run several postprocessors in order."""

    steps: tuple[SolverPostprocessor, ...]

    def postprocess(self, context: PostprocessContext) -> None:
        for step in self.steps:
            step.postprocess(context)


@dataclass(frozen=True)
class CommandPostprocessor:
    """Run an external command that generates solver summary artifacts."""

    command: tuple[str, ...]
    command_runner: CommandRunner | None = None
    timeout_s: float | None = None
    log_name: str = "postprocess.log"

    def postprocess(self, context: PostprocessContext) -> None:
        runner = self.command_runner or SubprocessCommandRunner()
        result = runner.run(
            _format_command(self.command, context),
            cwd=context.case_dir,
            timeout_s=self.timeout_s,
            log_path=context.case_dir / self.log_name,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Postprocess command failed with code {result.returncode}")


@dataclass(frozen=True)
class FilePostprocessor:
    """Copy existing solver output files into the standard summary contract."""

    summary_source: str | Path | None = None
    timeseries_source: str | Path | None = None

    def __post_init__(self) -> None:
        if self.summary_source is None and self.timeseries_source is None:
            raise ValueError("At least one output source must be configured")

    def postprocess(self, context: PostprocessContext) -> None:
        context.case_dir.mkdir(parents=True, exist_ok=True)
        if self.summary_source is not None:
            _copy_output(
                _resolve_source(self.summary_source, context.case_dir),
                context.case_dir / SUMMARY_FILENAME,
            )
        if self.timeseries_source is not None:
            _copy_output(
                _resolve_source(self.timeseries_source, context.case_dir),
                context.case_dir / TIMESERIES_FILENAME,
            )


def _resolve_source(source: str | Path, case_dir: Path) -> Path:
    path = Path(source)
    if not path.is_absolute():
        path = case_dir / path
    return path


def _copy_output(source: Path, target: Path) -> None:
    if not source.exists():
        raise FileNotFoundError(f"Postprocess output not found: {source}")
    if source.resolve() == target.resolve():
        return
    copyfile(source, target)


@dataclass(frozen=True)
class CsvTimeseriesPostprocessor:
    """Map solver-specific CSV columns into the standard timeseries contract."""

    source: str | Path
    columns: dict[str, str | tuple[str, ...]]
    optional_columns: dict[str, str | tuple[str, ...]] | None = None

    def postprocess(self, context: PostprocessContext) -> None:
        source = _resolve_source(self.source, context.case_dir)
        if not source.exists():
            raise FileNotFoundError(f"Postprocess output not found: {source}")
        rows = _read_csv_rows(source)
        if not rows:
            raise ValueError("CSV postprocess source must contain at least one data row")
        columns = {
            **self.columns,
            **_available_optional_columns(rows[0], self.optional_columns or {}),
        }
        standard_rows = [_standard_row(row, columns) for row in rows]
        _write_standard_timeseries(context.case_dir / TIMESERIES_FILENAME, standard_rows)


class OpenSeesCsvPostprocessor:
    """Map OpenSees CSV outputs into the standard timeseries contract."""

    def __init__(
        self,
        source: str | Path = TIMESERIES_FILENAME,
        columns: dict[str, str | tuple[str, ...]] | None = None,
        optional_columns: dict[str, str | tuple[str, ...]] | None = None,
    ) -> None:
        self.source = source
        self.columns = dict(columns or OPENSEES_TIMESERIES_COLUMNS)
        self.optional_columns = dict(
            OPENSEES_OPTIONAL_TIMESERIES_COLUMNS
            if optional_columns is None
            else optional_columns
        )

    def postprocess(self, context: PostprocessContext) -> None:
        CsvTimeseriesPostprocessor(
            source=self.source,
            columns=self.columns,
            optional_columns=self.optional_columns,
        ).postprocess(context)


@dataclass(frozen=True)
class AnsysDpfMultiCsvPostprocessor:
    """Merge existing DPF CSV exports into standard timeseries.csv."""

    job_name: str
    output_dir: str | Path | None = None

    def postprocess(self, context: PostprocessContext) -> None:
        root = context.case_dir if self.output_dir is None else _resolve_source(self.output_dir, context.case_dir)
        table_by_name = {
            name: _read_csv_rows(root / f"{self.job_name}_{suffix}.csv")
            for name, (suffix, _) in ANSYS_DPF_MULTI_CSV_SPECS.items()
        }
        for name, (suffix, _) in ANSYS_DPF_OPTIONAL_MULTI_CSV_SPECS.items():
            path = root / f"{self.job_name}_{suffix}.csv"
            if path.exists():
                table_by_name[name] = _read_csv_rows(path)
        displacement_rows = table_by_name["displacement"]
        standard_rows = []
        for index, row in enumerate(displacement_rows):
            standard_row = {"time": _time_value(row)}
            specs = {
                **ANSYS_DPF_MULTI_CSV_SPECS,
                **{
                    name: spec
                    for name, spec in ANSYS_DPF_OPTIONAL_MULTI_CSV_SPECS.items()
                    if name in table_by_name
                },
            }
            for target, (_, columns) in specs.items():
                if index >= len(table_by_name[target]):
                    raise ValueError("ANSYS DPF CSV files must have the same row count")
                source_row = table_by_name[target][index]
                if _time_value(source_row) != standard_row["time"]:
                    raise ValueError("ANSYS DPF CSV time columns must stay aligned")
                standard_row[target] = _dominant_value(
                    [_column_value(source_row, column) for column in columns]
                )
            standard_rows.append(standard_row)
        _check_same_row_count(table_by_name, len(displacement_rows))
        _write_standard_timeseries(context.case_dir / TIMESERIES_FILENAME, standard_rows)


@dataclass(frozen=True)
class AnsysDpfRstPostprocessor:
    """Extract compact standard channels from MAPDL RST and PRRSOL output."""

    response_nodes: tuple[int, ...] = (36, 107)
    response_component: int = 0
    cumulative_displacement_node: int = 107
    damper_pairs: tuple[tuple[str, int, int, int], ...] = DEFAULT_ANSYS_DAMPER_PAIRS
    damper_component: int = 0
    damper_c_scale: float = ANSYS_DAMPER_C_SCALE
    ansys_path: str | None = None
    tower_base_nodes: tuple[int, ...] = DEFAULT_STBRIDGE_TOWER_BASE_NODES
    tower_base_element_node_map: tuple[tuple[int, int], ...] = DEFAULT_STBRIDGE_TOWER_BASE_ELEMENT_NODE_MAP
    tower_base_element_end_map: tuple[tuple[int, int, int], ...] = DEFAULT_STBRIDGE_TOWER_BASE_ELEMENT_END_MAP

    def postprocess(self, context: PostprocessContext) -> None:
        # DPF 同时打开多个大 RST 会触发本机内存/服务资源争用；MAPDL 仍可并行，RST 读取串行执行。
        with _ANSYS_DPF_RST_POSTPROCESS_LOCK:
            try:
                self._postprocess_locked(context)
            finally:
                gc.collect()

    def _postprocess_locked(self, context: PostprocessContext) -> None:
        if self.ansys_path:
            os.environ.setdefault("ANSYS_DPF_PATH", str(self.ansys_path))
        from ansys.dpf import core as dpf

        model = dpf.Model(str(_mapdl_result_path(context)))
        times = [float(value) for value in model.metadata.time_freq_support.time_frequencies.data]
        start_index = _dynamic_timeseries_start_index(times, context.load_case)
        baseline_index = _dynamic_baseline_index(times, context.load_case)
        tower_base_rows = _tower_base_section_force_rows_from_dpf(
            dpf=dpf,
            model=model,
            times=times,
            element_node_map=self.tower_base_element_node_map,
        )
        tower_base_member_moment_rows = (
            _tower_base_member_moment_rows_from_dpf(
                dpf=dpf,
                model=model,
                times=times,
                element_end_map=self.tower_base_element_end_map,
            )
            if self.tower_base_element_end_map
            else []
        )
        if self.tower_base_element_end_map and not tower_base_member_moment_rows:
            raise ValueError("MAPDL RST BEAM4 SMISC member moments are required for tower-base moment")
        tower_base_resultants = (
            _tower_base_section_resultants(tower_base_rows, baseline_index=baseline_index)
            if tower_base_rows
            else _tower_base_reaction_resultants_from_dpf(
                dpf=dpf,
                model=model,
                tower_base_nodes=self.tower_base_nodes,
                baseline_index=baseline_index,
            )
        )
        if tower_base_member_moment_rows:
            tower_base_member_moments = _tower_base_member_moment_resultants(
                tower_base_member_moment_rows,
                baseline_index=baseline_index,
            )
            if len(tower_base_member_moments) != len(tower_base_resultants):
                raise ValueError("MAPDL RST tower-base member moments must align with tower-base resultants")
            for resultant, member_moment in zip(tower_base_resultants, tower_base_member_moments):
                resultant["tower_base_moment"] = member_moment
        displacement_fields = _result_fields_on_all_times(dpf, model, "displacement")
        displacement_by_response_node = _node_component_series(
            displacement_fields,
            self.response_nodes,
            self.response_component,
        )
        use_absolute_response_displacement = False
        baseline_response_displacements = {
            node: displacement_by_response_node[node][baseline_index]
            for node in self.response_nodes
        }
        cumulative_displacement = _fixed_response_node_cumulative_travel(
            displacement_by_response_node,
            response_node=self.cumulative_displacement_node,
            start_index=start_index,
            baseline_response_displacement=baseline_response_displacements[
                self.cumulative_displacement_node
            ],
        )
        acceleration_by_node = _response_acceleration_series(
            model=model,
            times=times,
            displacement_fields=displacement_fields,
            response_nodes=self.response_nodes,
            component=self.response_component,
        )
        damper_outputs = _extract_damper_outputs_from_dpf(
            model=model,
            times=times,
            displacement_fields=displacement_fields,
            damper_pairs=self.damper_pairs,
            component=self.damper_component,
            damper_c=float(context.damper_params.c) * float(self.damper_c_scale),
            alpha=float(context.damper_params.alpha),
        )
        damper_outputs = _trim_damper_outputs_for_dynamic_baseline(
            damper_outputs,
            start_index=start_index,
            baseline_index=baseline_index,
            damper_names=tuple(name for name, _element_id, _node_i, _node_j in self.damper_pairs),
        )
        active_damper_names = _active_damper_names_from_command_stream(
            context.command_stream_path,
            self.damper_pairs,
        )
        _zero_inactive_damper_forces(damper_outputs, active_damper_names)
        # 位移三列与加速度列取控制节点自身的真实时程，不做逐时间步的跨节点包络。
        # 三个位移列共用按增量峰值选出的同一个节点，否则同一时刻的三列可能来自不同
        # 节点。峰值标量对这次重排不变（max 可交换）。
        response_window = tuple(range(start_index, len(displacement_fields)))
        displacement_node = _controlling_response_node({
            node: [
                displacement_by_response_node[node][index] - baseline_response_displacements[node]
                for index in response_window
            ]
            for node in self.response_nodes
        })
        acceleration_node = _controlling_response_node({
            node: [acceleration_by_node[node][index] for index in response_window]
            for node in self.response_nodes
        })
        standard_rows = []
        for index in response_window:
            absolute_displacement = displacement_by_response_node[displacement_node][index]
            displacement_increment = (
                absolute_displacement - baseline_response_displacements[displacement_node]
            )
            standard_row = {
                "time": times[index] if index < len(times) else float(index),
                "displacement": (
                    absolute_displacement
                    if use_absolute_response_displacement
                    else displacement_increment
                ),
                "displacement_increment": displacement_increment,
                "absolute_displacement": absolute_displacement,
                "acceleration": acceleration_by_node[acceleration_node][index],
                "tower_base_shear": tower_base_resultants[index]["tower_base_shear"],
                "tower_base_moment": tower_base_resultants[index]["tower_base_moment"],
            }
            damper_index = index - start_index
            if damper_index < len(damper_outputs["relative_rows"]):
                relative_row = damper_outputs["relative_rows"][damper_index]
                force_values = [
                    float(relative_row[f"{name}_damper_force_x_N"])
                    for name, _element_id, _node_i, _node_j in self.damper_pairs
                ]
                stroke_values = [
                    float(relative_row[f"{name}_rel_disp_x_m"])
                    for name, _element_id, _node_i, _node_j in self.damper_pairs
                ]
                velocity_values = [
                    float(relative_row[f"{name}_rel_vel_x_m_per_s"])
                    for name, _element_id, _node_i, _node_j in self.damper_pairs
                ]
                standard_row["damper_force"] = _dominant_value(force_values)
                standard_row["damper_stroke"] = _dominant_value(stroke_values)
                standard_row["damper_relative_velocity"] = _dominant_value(velocity_values)
            standard_rows.append(standard_row)
        _write_generic_csv(context.case_dir / "ansys_damper_node_response.csv", damper_outputs["node_rows"])
        _write_generic_csv(context.case_dir / "tower_girder_relative_response.csv", damper_outputs["relative_rows"])
        _write_generic_csv(context.case_dir / "damper_force.csv", damper_outputs["force_rows"])
        if tower_base_rows:
            _write_generic_csv(context.case_dir / "tower_base_section_force.csv", tower_base_rows)
        if tower_base_member_moment_rows:
            _write_generic_csv(
                context.case_dir / "tower_base_member_moment.csv",
                tower_base_member_moment_rows,
            )
        standard_objectives = _standard_objectives_from_rows(standard_rows)
        standard_objectives["cumulative_displacement"] = cumulative_displacement
        damper_capacity_metrics = _compute_damper_capacity_metrics(
            relative_rows=damper_outputs["relative_rows"],
            damper_names=tuple(name for name, _element_id, _node_i, _node_j in self.damper_pairs),
            c_by_damper={
                name: (
                    float(context.damper_params.c) * float(self.damper_c_scale)
                    if name in active_damper_names
                    else 0.0
                )
                for name, _element_id, _node_i, _node_j in self.damper_pairs
            },
            alpha=float(context.damper_params.alpha),
        )
        standard_objectives["dissipated_energy"] = sum(
            float(values["E"])
            for values in damper_capacity_metrics.values()
        )
        _write_json(context.case_dir / "damper_capacity_metrics.json", damper_capacity_metrics)
        _write_standard_timeseries(context.case_dir / TIMESERIES_FILENAME, standard_rows)
        _write_json(
            context.case_dir / SUMMARY_FILENAME,
            {
                "status": "completed",
                "objectives": {
                    **standard_objectives,
                    **_damper_cost_objective_fields(damper_capacity_metrics),
                },
                "metadata": {
                    "damper_capacity_metrics": damper_capacity_metrics,
                    "damper_cost_metric_definition": "Fmax=max|C*|v|^alpha|, Smax=max|u_rel|, E=sum(C*|v|^(alpha+1)*dt)",
                    "cumulative_displacement_node": self.cumulative_displacement_node,
                    "cumulative_displacement_definition": "fixed_response_node_travel_from_preload_baseline",
                    "controlling_displacement_node": displacement_node,
                    "controlling_acceleration_node": acceleration_node,
                    "controlling_node_selection": "peak_per_node_then_max",
                    "response_displacement_reference": (
                        "absolute"
                        if use_absolute_response_displacement
                        else "baseline_removed"
                    ),
                },
            },
        )


@dataclass(frozen=True)
class AnsysDpfNodeResponsePostprocessor:
    """Model-agnostic node/element response extraction from MAPDL RST.

    Unlike :class:`AnsysDpfRstPostprocessor` this makes no assumption about
    STbridge topology (tower-base elements, damper pairs), so it works for
    user-uploaded FEM models. It extracts displacement/acceleration series
    for the requested nodes and, optionally, element nodal force series for
    the requested elements, and writes a standard ``timeseries.csv`` with
    per-target columns plus envelope ``displacement``/``acceleration``
    channels for the standard objectives.
    """

    response_nodes: tuple[int, ...]
    response_component: int = 0
    response_elements: tuple[int, ...] = ()
    ansys_path: str | None = None

    def postprocess(self, context: PostprocessContext) -> None:
        with _ANSYS_DPF_RST_POSTPROCESS_LOCK:
            try:
                self._postprocess_locked(context)
            finally:
                gc.collect()

    def _postprocess_locked(self, context: PostprocessContext) -> None:
        if not self.response_nodes:
            raise ValueError("ansys-dpf-nodes postprocessor requires at least one response node")
        if self.ansys_path:
            os.environ.setdefault("ANSYS_DPF_PATH", str(self.ansys_path))
        from ansys.dpf import core as dpf

        model = dpf.Model(str(_mapdl_result_path(context)))
        times = [float(value) for value in model.metadata.time_freq_support.time_frequencies.data]
        start_index = _dynamic_timeseries_start_index(times, context.load_case)
        baseline_index = _dynamic_baseline_index(times, context.load_case)
        displacement_fields = _result_fields_on_all_times(dpf, model, "displacement")
        displacement_by_node = _node_component_series(
            displacement_fields,
            self.response_nodes,
            self.response_component,
        )
        acceleration_by_node = _response_acceleration_series(
            model=model,
            times=times,
            displacement_fields=displacement_fields,
            response_nodes=self.response_nodes,
            component=self.response_component,
        )
        element_force_by_id = (
            self._element_force_series(dpf, model)
            if self.response_elements
            else {}
        )
        baseline_displacements = {
            node: displacement_by_node[node][baseline_index]
            for node in self.response_nodes
        }
        sample_count = min(
            len(displacement_fields),
            *(len(series) for series in displacement_by_node.values()),
            *(len(series) for series in acceleration_by_node.values()),
            *((len(series) for series in element_force_by_id.values()) or (len(displacement_fields),)),
        )
        # displacement/acceleration 两列取控制节点自身的真实时程，不做逐时间步的跨节点
        # 包络：包络曲线在任何一个真实节点上都没发生过，而累计位移在这种列上累加没有
        # 物理意义。峰值标量对这次重排不变（max 可交换）。
        response_window = tuple(range(start_index, sample_count))
        displacement_node = _controlling_response_node({
            node: [
                displacement_by_node[node][index] - baseline_displacements[node]
                for index in response_window
            ]
            for node in self.response_nodes
        })
        acceleration_node = _controlling_response_node({
            node: [acceleration_by_node[node][index] for index in response_window]
            for node in self.response_nodes
        })
        rows: list[dict[str, float]] = []
        for index in response_window:
            node_displacements = [
                displacement_by_node[node][index] - baseline_displacements[node]
                for node in self.response_nodes
            ]
            node_accelerations = [acceleration_by_node[node][index] for node in self.response_nodes]
            row: dict[str, float] = {
                "time": times[index] if index < len(times) else float(index),
                "displacement": displacement_by_node[displacement_node][index]
                - baseline_displacements[displacement_node],
                "acceleration": acceleration_by_node[acceleration_node][index],
            }
            for position, node in enumerate(self.response_nodes):
                row[f"node_{node}_displacement"] = node_displacements[position]
                row[f"node_{node}_acceleration"] = node_accelerations[position]
            for element_id in self.response_elements:
                row[f"element_{element_id}_force"] = element_force_by_id[element_id][index]
            rows.append(row)
        _write_standard_timeseries(context.case_dir / TIMESERIES_FILENAME, rows)
        objectives = _standard_objectives_from_rows(rows)
        for column in rows[0]:
            if column.startswith(("node_", "element_")):
                objectives[f"max_{column}"] = max(abs(float(row[column])) for row in rows)
        _write_json(
            context.case_dir / SUMMARY_FILENAME,
            {
                "status": "completed",
                "objectives": objectives,
                "metadata": {
                    "postprocessor": "ansys-dpf-nodes",
                    "response_nodes": list(self.response_nodes),
                    "response_component": self.response_component,
                    "response_elements": list(self.response_elements),
                    "response_displacement_reference": "baseline_removed",
                    "controlling_displacement_node": displacement_node,
                    "controlling_acceleration_node": acceleration_node,
                    "controlling_node_selection": "max_abs_peak_per_node",
                },
            },
        )

    def _element_force_series(self, dpf, model) -> dict[int, list[float]]:
        element_ids = [int(element_id) for element_id in self.response_elements]
        mesh = model.metadata.meshed_region
        element_info: dict[int, tuple[int, int]] = {}
        for element_id in element_ids:
            try:
                connectivity = mesh.elements.element_by_id(element_id).connectivity.ids.tolist()
            except Exception as exc:
                raise ValueError(f"MAPDL RST result is missing element {element_id}") from exc
            element_info[element_id] = (0, len(connectivity))
        time_scoping = dpf.time_freq_scoping_factory.scoping_on_all_time_freqs(model)
        elem_scoping = dpf.Scoping(ids=element_ids, location=dpf.locations.elemental)
        enf_op = model.results.element_nodal_forces()
        enf_op.inputs.mesh_scoping(elem_scoping)
        enf_op.inputs.time_scoping(time_scoping)
        fields_container = enf_op.outputs.fields_container()
        if len(fields_container) == 0:
            raise ValueError("MAPDL RST result does not expose element nodal forces")
        series: dict[int, list[float]] = {element_id: [] for element_id in element_ids}
        for field in fields_container:
            for element_id in element_ids:
                values = _dpf_element_nodal_force_moment(field, element_id, element_info[element_id])
                series[element_id].append(values[min(self.response_component, 2)])
        return series


def _compute_damper_capacity_metrics(*args, **kwargs):
    from pyansys_bridge.optimization.damper_cost import compute_damper_capacity_metrics

    return compute_damper_capacity_metrics(*args, **kwargs)


def _damper_cost_objective_fields(*args, **kwargs):
    from pyansys_bridge.optimization.damper_cost import damper_cost_objective_fields

    return damper_cost_objective_fields(*args, **kwargs)


def ansys_dpf_timeseries_postprocessor(source: str | Path = "ansys_dpf_export.csv") -> CsvTimeseriesPostprocessor:
    """Build the default ANSYS/DPF CSV-to-timeseries mapper."""

    return CsvTimeseriesPostprocessor(
        source=source,
        columns=dict(ANSYS_DPF_TIMESERIES_COLUMNS),
        optional_columns=dict(ANSYS_DPF_OPTIONAL_TIMESERIES_COLUMNS),
    )


def ansys_dpf_multi_csv_postprocessor(
    job_name: str,
    output_dir: str | Path | None = None,
) -> AnsysDpfMultiCsvPostprocessor:
    """Build the default merger for DPF exports written by analysis/dpf.py."""

    return AnsysDpfMultiCsvPostprocessor(job_name=job_name, output_dir=output_dir)


def ansys_dpf_node_response_postprocessor(
    response_nodes: tuple[int, ...] | list[int],
    response_component: int = 0,
    response_elements: tuple[int, ...] | list[int] = (),
    ansys_path: str | None = None,
) -> AnsysDpfNodeResponsePostprocessor:
    """Build the model-agnostic node/element response postprocessor."""

    normalized_nodes = tuple(int(node) for node in response_nodes)
    if not normalized_nodes:
        raise ValueError("ansys-dpf-nodes postprocessor requires at least one response node")
    return AnsysDpfNodeResponsePostprocessor(
        response_nodes=normalized_nodes,
        response_component=int(response_component),
        response_elements=tuple(int(element) for element in response_elements),
        ansys_path=ansys_path,
    )


def ansys_dpf_rst_postprocessor(
    response_nodes: tuple[int, ...] | list[int] = (36, 107),
    response_component: int = 0,
    cumulative_displacement_node: int = 107,
    damper_pairs: tuple[tuple[str, int, int, int], ...] | None = DEFAULT_ANSYS_DAMPER_PAIRS,
    damper_component: int = 0,
    damper_c_scale: float = ANSYS_DAMPER_C_SCALE,
    ansys_path: str | None = None,
) -> AnsysDpfRstPostprocessor:
    """Build the direct MAPDL RST postprocessor for run-mode acceptance."""

    normalized_response_nodes = tuple(int(node) for node in response_nodes)
    normalized_cumulative_node = int(cumulative_displacement_node)
    if normalized_cumulative_node not in normalized_response_nodes:
        raise ValueError("cumulative_displacement_node must be one of response_nodes")
    return AnsysDpfRstPostprocessor(
        response_nodes=normalized_response_nodes,
        response_component=int(response_component),
        cumulative_displacement_node=normalized_cumulative_node,
        damper_pairs=(
            DEFAULT_ANSYS_DAMPER_PAIRS
            if damper_pairs is None
            else tuple(
                (str(name), int(element_id), int(node_i), int(node_j))
                for name, element_id, node_i, node_j in damper_pairs
            )
        ),
        damper_component=int(damper_component),
        damper_c_scale=float(damper_c_scale),
        ansys_path=ansys_path,
    )


def opensees_timeseries_postprocessor(source: str | Path = "opensees_timeseries.csv") -> CsvTimeseriesPostprocessor:
    """Build the default OpenSeesPy CSV-to-timeseries mapper."""

    return CsvTimeseriesPostprocessor(
        source=source,
        columns=dict(OPENSEES_TIMESERIES_COLUMNS),
        optional_columns=dict(OPENSEES_OPTIONAL_TIMESERIES_COLUMNS),
    )


def opensees_csv_postprocessor(source: str | Path = TIMESERIES_FILENAME) -> OpenSeesCsvPostprocessor:
    """Build the default OpenSees CSV-to-timeseries mapper."""

    return OpenSeesCsvPostprocessor(source=source)


def _format_command(command: tuple[str, ...], context: PostprocessContext) -> list[str]:
    values = {
        "case_dir": str(context.case_dir),
        "command_stream": str(context.command_stream_path),
        "solver": context.solver,
        "load_name": context.load_case.name,
        "load_type": context.load_case.load_type,
        "damper_c": str(context.damper_params.c),
        "damper_alpha": str(context.damper_params.alpha),
    }
    return [part.format(**values) for part in command]


def _extract_damper_outputs_from_dpf(
    model,
    times: list[float],
    displacement_fields,
    damper_pairs: tuple[tuple[str, int, int, int], ...],
    component: int,
    damper_c: float,
    alpha: float,
) -> dict[str, list[dict[str, float | int]]]:
    node_ids = _unique_damper_nodes(damper_pairs)
    displacement_by_node = _node_component_series(displacement_fields, node_ids, component)
    velocity_by_node = _velocity_series_from_dpf(model, node_ids, component)
    velocity_source = "dpf"
    if velocity_by_node is None:
        velocity_by_node = {
            node_id: _finite_difference(times, displacement_by_node[node_id])
            for node_id in node_ids
        }
        velocity_source = "finite_difference"
    node_rows = _damper_node_rows(times, node_ids, displacement_by_node, velocity_by_node, velocity_source)
    relative_rows, force_rows = _damper_relative_and_force_rows(
        times=times,
        damper_pairs=damper_pairs,
        displacement_by_node=displacement_by_node,
        velocity_by_node=velocity_by_node,
        damper_c=damper_c,
        alpha=alpha,
    )
    return {
        "node_rows": node_rows,
        "relative_rows": relative_rows,
        "force_rows": force_rows,
    }


def _unique_damper_nodes(damper_pairs: tuple[tuple[str, int, int, int], ...]) -> tuple[int, ...]:
    node_ids = []
    for _name, _element_id, node_i, node_j in damper_pairs:
        for node_id in (node_i, node_j):
            if node_id not in node_ids:
                node_ids.append(node_id)
    return tuple(node_ids)


def _node_component_series(fields_container, node_ids: tuple[int, ...], component: int) -> dict[int, list[float]]:
    values = {node_id: [] for node_id in node_ids}
    for field in fields_container:
        for node_id in node_ids:
            try:
                values[node_id].append(_component_value(field.get_entity_data_by_id(node_id), component))
            except Exception as exc:
                raise ValueError(f"MAPDL RST result is missing node {node_id}") from exc
    return values


def _velocity_series_from_dpf(model, node_ids: tuple[int, ...], component: int) -> dict[int, list[float]] | None:
    try:
        from ansys.dpf import core as dpf

        velocity_fields = _result_fields_on_all_times(dpf, model, "velocity")
    except Exception:
        return None
    try:
        return _node_component_series(velocity_fields, node_ids, component)
    except ValueError:
        return None


def _response_acceleration_series(
    *,
    model,
    times: list[float],
    displacement_fields,
    response_nodes: tuple[int, ...],
    component: int,
) -> dict[int, list[float]]:
    acceleration_by_node = _acceleration_series_from_dpf(model, response_nodes, component)
    if acceleration_by_node is not None:
        return acceleration_by_node
    displacement_by_node = _node_component_series(displacement_fields, response_nodes, component)
    velocity_by_node = {
        node_id: _finite_difference(times, displacement_by_node[node_id])
        for node_id in response_nodes
    }
    return {
        node_id: _finite_difference(times, velocity_by_node[node_id])
        for node_id in response_nodes
    }


def _acceleration_series_from_dpf(model, node_ids: tuple[int, ...], component: int) -> dict[int, list[float]] | None:
    try:
        from ansys.dpf import core as dpf

        acceleration_fields = _result_fields_on_all_times(dpf, model, "acceleration")
    except Exception:
        return None
    try:
        return _node_component_series(acceleration_fields, node_ids, component)
    except ValueError:
        return None


def _uses_absolute_response_displacement(load_case) -> bool:
    """Standard displacement is the response relative to the preloaded baseline."""

    return False


def _fixed_response_node_cumulative_travel(
    displacement_by_response_node: dict[int, list[float]],
    *,
    response_node: int,
    start_index: int,
    baseline_response_displacement: float,
) -> float:
    previous = float(baseline_response_displacement)
    cumulative = 0.0
    for value in displacement_by_response_node[response_node][start_index:]:
        current = float(value)
        cumulative += abs(current - previous)
        previous = current
    return cumulative


def _controlling_response_node(series_by_node: dict[int, list[float]]) -> int:
    """先各候选节点在自己的时程上求峰值，再比峰值取最大者作为控制节点。

    逐时间步的跨节点包络（_dominant_value）得到的曲线在任何一个真实节点上都没
    发生过，路径依赖量（累计位移）在这种列上累加没有物理意义。峰值标量本身对这次
    重排不变（max 可交换），改动只影响曲线归属与累计位移。
    """
    peaks = {
        node: max(abs(float(value)) for value in values)
        for node, values in series_by_node.items()
        if values
    }
    if not peaks:
        raise ValueError("controlling response node requires at least one non-empty response series")
    largest = max(peaks.values())
    # 镜像对称节点的峰值只在末位有效数字上不同，直接取 max 会让选中的节点由浮点
    # 噪声决定。落在相对容差内的按节点号取定，保证可复现。
    tolerance = abs(largest) * 1.0e-9
    return min(node for node, peak in peaks.items() if largest - peak <= tolerance)


def _tower_base_reaction_resultants_from_dpf(
    *,
    dpf,
    model,
    tower_base_nodes: tuple[int, ...],
    baseline_index: int = 0,
) -> list[dict[str, float]]:
    reaction_fields = _result_fields_on_all_times(dpf, model, "reaction_force")
    coordinates = {
        int(node_id): tuple(float(value) for value in model.metadata.meshed_region.nodes.node_by_id(int(node_id)).coordinates)
        for node_id in tower_base_nodes
    }
    rows: list[dict[str, float]] = []
    for field in reaction_fields:
        row: dict[str, float] = {}
        for node_id in tower_base_nodes:
            force = _first_three_components(field.get_entity_data_by_id(int(node_id)))
            moment = _cross_product(coordinates[int(node_id)], force)
            for component, value in zip(("FX", "FY"), force[:2]):
                row[f"Node{int(node_id)}_{component}"] = float(value)
            for component, value in zip(("MY", "MZ"), moment[1:3]):
                row[f"Node{int(node_id)}_{component}"] = float(value)
        rows.append(row)
    if not rows:
        raise ValueError("MAPDL RST reaction_force result contains no time steps")
    return _tower_base_peak_node_resultants(rows, prefix="Node", baseline_index=baseline_index)


def _tower_base_section_force_rows_from_dpf(
    *,
    dpf,
    model,
    times: list[float],
    element_node_map: tuple[tuple[int, int], ...],
) -> list[dict[str, float]]:
    if not element_node_map:
        return []
    elem_ids = [int(element_id) for element_id, _node_id in element_node_map]
    time_scoping = dpf.time_freq_scoping_factory.scoping_on_all_time_freqs(model)
    elem_scoping = dpf.Scoping(ids=elem_ids, location=dpf.locations.elemental)
    enf_op = model.results.element_nodal_forces()
    enf_op.inputs.mesh_scoping(elem_scoping)
    enf_op.inputs.time_scoping(time_scoping)
    fields_container = enf_op.outputs.fields_container()
    if len(fields_container) == 0:
        return []

    elem_info = _dpf_element_local_node_info(model, element_node_map)
    rows = []
    components = ("FX", "FY", "FZ", "MX", "MY", "MZ")
    for field_index, field in enumerate(fields_container):
        label_space = fields_container.get_label_space(field_index)
        time_id = int(label_space.get("time", field_index + 1))
        row: dict[str, float] = {
            "time": float(times[time_id - 1]) if time_id - 1 < len(times) else float(time_id)
        }
        for element_id, _node_id in element_node_map:
            values = _dpf_element_nodal_force_moment(field, int(element_id), elem_info[int(element_id)])
            for component, value in zip(components, values):
                row[f"Elem{int(element_id)}_{component}"] = value
        rows.append(row)
    return rows


def _tower_base_member_moment_rows_from_dpf(
    *,
    dpf,
    model,
    times: list[float],
    element_end_map: tuple[tuple[int, int, int], ...],
) -> list[dict[str, float]]:
    if not element_end_map:
        return []
    elem_ids = [int(element_id) for element_id, _node_i, _node_j in element_end_map]
    time_scoping = dpf.time_freq_scoping_factory.scoping_on_all_time_freqs(model)
    elem_scoping = dpf.Scoping(ids=elem_ids, location=dpf.locations.elemental)
    smisc_op = dpf.operators.result.smisc(
        data_sources=model.metadata.data_sources,
        time_scoping=time_scoping,
        mesh_scoping=elem_scoping,
    )
    fields_container = smisc_op.outputs.fields_container()
    if len(fields_container) == 0:
        return []

    coordinates = {
        int(node_id): tuple(
            float(value)
            for value in model.metadata.meshed_region.nodes.node_by_id(int(node_id)).coordinates
        )
        for _element_id, node_i, node_j in element_end_map
        for node_id in (node_i, node_j)
    }
    local_axes = {
        int(element_id): _beam4_default_local_axes(
            node_i=coordinates[int(node_i)],
            node_j=coordinates[int(node_j)],
        )
        for element_id, node_i, node_j in element_end_map
    }
    rows = []
    for field_index, field in enumerate(fields_container):
        label_space = fields_container.get_label_space(field_index)
        time_id = int(label_space.get("time", field_index + 1))
        row: dict[str, float] = {
            "time": float(times[time_id - 1]) if time_id - 1 < len(times) else float(time_id)
        }
        for element_id, _node_i, _node_j in element_end_map:
            entity = field.get_entity_data_by_id(int(element_id))
            values = entity.reshape(-1).tolist() if hasattr(entity, "reshape") else list(entity)
            if len(values) < 12:
                raise ValueError(f"BEAM4 element {int(element_id)} SMISC output has fewer than 12 values")
            global_moment = _beam4_j_end_internal_moment_global(
                local_moment=tuple(float(values[index]) for index in (9, 10, 11)),
                local_axes=local_axes[int(element_id)],
            )
            for component, value in zip(("MX", "MY", "MZ"), global_moment):
                row[f"Elem{int(element_id)}_{component}"] = value
        rows.append(row)
    return rows


def _beam4_default_local_axes(
    *,
    node_i: tuple[float, float, float],
    node_j: tuple[float, float, float],
) -> tuple[tuple[float, float, float], ...]:
    delta = tuple(float(node_j[index]) - float(node_i[index]) for index in range(3))
    length = sum(value * value for value in delta) ** 0.5
    if length <= 0.0:
        raise ValueError("BEAM4 element length must be positive")
    local_x = tuple(value / length for value in delta)
    xy_length = (delta[0] * delta[0] + delta[1] * delta[1]) ** 0.5
    if xy_length <= 1.0e-4 * length:
        local_y = (0.0, 1.0, 0.0)
    else:
        local_y = (-delta[1] / xy_length, delta[0] / xy_length, 0.0)
    local_z = _cross_product(local_x, local_y)
    return local_x, local_y, local_z


def _beam4_j_end_internal_moment_global(
    *,
    local_moment: tuple[float, float, float],
    local_axes: tuple[tuple[float, float, float], ...],
) -> tuple[float, float, float]:
    # BEAM4 SMISC/MMOM 是 J 端构件作用；塔底截面内力采用相反号后转到全局坐标。
    return tuple(
        -sum(float(local_moment[axis]) * float(local_axes[axis][component]) for axis in range(3))
        for component in range(3)
    )


def _dpf_element_local_node_info(model, element_node_map: tuple[tuple[int, int], ...]) -> dict[int, tuple[int, int]]:
    info = {}
    mesh = model.metadata.meshed_region
    for element_id, node_id in element_node_map:
        try:
            connectivity = mesh.elements.element_by_id(int(element_id)).connectivity.ids.tolist()
            if int(node_id) in connectivity:
                info[int(element_id)] = (connectivity.index(int(node_id)), len(connectivity))
                continue
        except Exception:
            pass
        # BEAM4 底端在当前参考模型中对应 J 端；DPF mesh 不含该类单元时使用同一退化规则。
        info[int(element_id)] = (1, 2)
    return info


def _dpf_element_nodal_force_moment(field, element_id: int, element_info: tuple[int, int]) -> tuple[float, ...]:
    local_index, node_count = element_info
    entity = field.get_entity_data_by_id(element_id)
    entries_per_set = node_count * 2
    set_count = len(entity) // entries_per_set
    values = [0.0] * 6
    for set_index in range(set_count):
        force_index = set_index * entries_per_set + local_index * 2
        moment_index = force_index + 1
        if moment_index >= len(entity):
            continue
        force = entity[force_index]
        moment = entity[moment_index]
        for index in range(min(3, len(force))):
            values[index] += float(force[index])
        for index in range(min(3, len(moment))):
            values[3 + index] += float(moment[index])
    return tuple(values)


def _peak_absolute_column(
    rows: list[dict[str, float]],
    columns: list[str],
    baseline: dict[str, float],
) -> str | None:
    """Pick the column whose baseline-relative history has the largest absolute peak.

    塔底剪力/弯矩取"所有桥塔节点绝对值峰值最大的那个节点"的时程，而不是跨节点求和：
    双塔镜像对称时求和会精确抵消为 0，掩盖真实的单塔截面内力。
    """
    if not columns:
        return None
    peaks = {
        column: max(abs(float(row[column]) - float(baseline[column])) for row in rows)
        for column in columns
    }
    largest = max(peaks.values())
    # 镜像对称双塔的各塔脚峰值只在末位有效数字上不同，直接取 max 会让输出时程的
    # 正负号由浮点噪声决定。峰值落在相对容差内的按列名取定，保证结果可复现。
    tolerance = abs(largest) * 1.0e-9
    return min(column for column, peak in peaks.items() if largest - peak <= tolerance)


def _tower_base_peak_node_resultants(
    rows: list[dict[str, float]],
    *,
    prefix: str,
    baseline_index: int = 0,
) -> list[dict[str, float]]:
    """把逐节点/逐单元时程收敛成峰值最大节点处的塔底剪力与弯矩时程。"""
    if not rows:
        return []
    baseline_index = min(max(0, int(baseline_index)), len(rows) - 1)
    baseline = rows[baseline_index]

    def _columns(component: str) -> list[str]:
        return sorted(
            column
            for column in rows[0]
            if column.startswith(prefix) and column.endswith(f"_{component}")
        )

    shear_column = _peak_absolute_column(rows, _columns("FX") + _columns("FY"), baseline)
    moment_column = _peak_absolute_column(rows, _columns("MY") + _columns("MZ"), baseline)
    return [
        {
            "tower_base_shear": (
                float(row[shear_column]) - float(baseline[shear_column]) if shear_column else 0.0
            ),
            "tower_base_moment": (
                float(row[moment_column]) - float(baseline[moment_column]) if moment_column else 0.0
            ),
        }
        for row in rows
    ]


def _tower_base_section_resultants(
    rows: list[dict[str, float]],
    *,
    baseline_index: int = 0,
) -> list[dict[str, float]]:
    resultants = _tower_base_peak_node_resultants(
        rows,
        prefix="Elem",
        baseline_index=baseline_index,
    )
    if not rows:
        return resultants

    baseline_index = min(max(0, int(baseline_index)), len(rows) - 1)
    baseline = rows[baseline_index]
    shear_column_groups = [
        sorted(
            column
            for column in rows[0]
            if column.startswith("Elem") and column.endswith(f"_{component}")
        )
        for component in ("FX", "FY")
    ]
    for row, resultant in zip(rows, resultants):
        axis_sums = [
            sum(float(row[column]) - float(baseline[column]) for column in columns)
            for columns in shear_column_groups
            if columns
        ]
        resultant["tower_base_shear"] = _dominant_value(axis_sums) if axis_sums else 0.0
    return resultants


def _tower_base_member_moment_resultants(
    rows: list[dict[str, float]],
    *,
    baseline_index: int = 0,
) -> list[float]:
    return [
        resultant["tower_base_moment"]
        for resultant in _tower_base_peak_node_resultants(
            rows, prefix="Elem", baseline_index=baseline_index
        )
    ]


def _dynamic_timeseries_start_index(times: list[float], load_case) -> int:
    try:
        dt = float(getattr(load_case, "dt", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0
    if dt <= 0.0:
        return 0
    tolerance = max(abs(dt) * 1.0e-6, 1.0e-9)
    for index, time in enumerate(times):
        if float(time) >= dt - tolerance:
            return index
    return 0


def _dynamic_baseline_index(times: list[float], load_case) -> int:
    start_index = _dynamic_timeseries_start_index(times, load_case)
    return max(0, start_index - 1)


def _trim_damper_outputs_for_dynamic_baseline(
    damper_outputs: dict[str, list[dict[str, float | int]]],
    *,
    start_index: int,
    baseline_index: int | None = None,
    damper_names: tuple[str, ...],
) -> dict[str, list[dict[str, float | int]]]:
    baseline_index = start_index if baseline_index is None else baseline_index
    baseline_index = max(0, int(baseline_index))
    trimmed = {
        key: [dict(row) for row in rows[start_index:]]
        for key, rows in damper_outputs.items()
    }
    if not trimmed.get("relative_rows"):
        return trimmed
    baseline_row = damper_outputs["relative_rows"][min(baseline_index, len(damper_outputs["relative_rows"]) - 1)]
    for row in trimmed["relative_rows"]:
        for name in damper_names:
            field = f"{name}_rel_disp_x_m"
            if field in row:
                row[field] = float(row[field]) - float(baseline_row[field])
    if trimmed.get("node_rows"):
        baseline_node_row = damper_outputs["node_rows"][min(baseline_index, len(damper_outputs["node_rows"]) - 1)]
        for row in trimmed["node_rows"]:
            for key, value in list(row.items()):
                if key.endswith("_UX"):
                    row[key] = float(value) - float(baseline_node_row[key])
    if trimmed.get("force_rows"):
        trimmed["force_rows"] = [dict(row) for row in damper_outputs["force_rows"][start_index:]]
    return trimmed


def _first_three_components(data) -> tuple[float, float, float]:
    values = data.tolist() if hasattr(data, "tolist") else data
    while values and isinstance(values[0], list):
        values = values[0]
    result = [float(value) for value in values[:3]]
    while len(result) < 3:
        result.append(0.0)
    return result[0], result[1], result[2]


def _cross_product(left: tuple[float, float, float], right: tuple[float, float, float]) -> tuple[float, float, float]:
    return (
        left[1] * right[2] - left[2] * right[1],
        left[2] * right[0] - left[0] * right[2],
        left[0] * right[1] - left[1] * right[0],
    )


def _finite_difference(times: list[float], values: list[float]) -> list[float]:
    velocities = []
    previous_time = None
    previous_value = None
    for time, value in zip(times, values):
        if previous_time is None or time == previous_time:
            velocities.append(0.0)
        else:
            velocities.append((value - previous_value) / (time - previous_time))
        previous_time = time
        previous_value = value
    return velocities


def _damper_node_rows(
    times: list[float],
    node_ids: tuple[int, ...],
    displacement_by_node: dict[int, list[float]],
    velocity_by_node: dict[int, list[float]],
    velocity_source: str,
) -> list[dict[str, float | str]]:
    rows = []
    for index, time in enumerate(times):
        row: dict[str, float | str] = {"time": float(time), "velocity_source": velocity_source}
        for node_id in node_ids:
            row[f"Node{node_id}_UX"] = displacement_by_node[node_id][index]
            row[f"Node{node_id}_VX"] = velocity_by_node[node_id][index]
        rows.append(row)
    return rows


def _damper_relative_and_force_rows(
    times: list[float],
    damper_pairs: tuple[tuple[str, int, int, int], ...],
    displacement_by_node: dict[int, list[float]],
    velocity_by_node: dict[int, list[float]],
    damper_c: float,
    alpha: float,
) -> tuple[list[dict[str, float | int]], list[dict[str, float]]]:
    relative_rows = []
    force_rows = []
    for index, time in enumerate(times):
        relative_row: dict[str, float | int] = {"time": float(time)}
        force_row: dict[str, float] = {"time": float(time)}
        for name, element_id, node_i, node_j in damper_pairs:
            rel_disp = displacement_by_node[node_j][index] - displacement_by_node[node_i][index]
            rel_vel = velocity_by_node[node_j][index] - velocity_by_node[node_i][index]
            force = _viscous_damper_force(rel_vel, damper_c, alpha)
            relative_row[f"{name}_element_id"] = element_id
            relative_row[f"{name}_node_i"] = node_i
            relative_row[f"{name}_node_j"] = node_j
            relative_row[f"{name}_rel_disp_x_m"] = rel_disp
            relative_row[f"{name}_rel_vel_x_m_per_s"] = rel_vel
            relative_row[f"{name}_damper_force_x_N"] = force
            force_row[f"{name}_force_x_N"] = force
        relative_rows.append(relative_row)
        force_rows.append(force_row)
    return relative_rows, force_rows


def _active_damper_names_from_command_stream(
    command_stream_path: Path,
    damper_pairs: tuple[tuple[str, int, int, int], ...],
) -> set[str]:
    try:
        text = command_stream_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {name for name, _element_id, _node_i, _node_j in damper_pairs}
    active = set()
    for name, element_id, _node_i, _node_j in damper_pairs:
        if re.search(rf"^\s*EN\s*,\s*{element_id}\b", text, flags=re.IGNORECASE | re.MULTILINE):
            active.add(name)
    return active


def _zero_inactive_damper_forces(
    damper_outputs: dict[str, list[dict[str, float | int]]],
    active_damper_names: set[str],
) -> None:
    for row in damper_outputs["relative_rows"]:
        for key in list(row):
            if key.endswith("_damper_force_x_N"):
                name = key.removesuffix("_damper_force_x_N")
                if name not in active_damper_names:
                    row[key] = 0.0
    for row in damper_outputs["force_rows"]:
        for key in list(row):
            if key.endswith("_force_x_N"):
                name = key.removesuffix("_force_x_N")
                if name not in active_damper_names:
                    row[key] = 0.0


def _viscous_damper_force(relative_velocity: float, damper_c: float, alpha: float) -> float:
    if relative_velocity == 0.0:
        return 0.0
    return damper_c * (1.0 if relative_velocity > 0.0 else -1.0) * (abs(relative_velocity) ** alpha)


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"Postprocess output not found: {path}")
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError("CSV postprocess source must contain a header row")
        return list(reader)


def _time_value(row: dict[str, str]) -> float:
    if "Time" in row:
        return float(row["Time"])
    if "time" in row:
        return float(row["time"])
    raise ValueError("CSV postprocess source must contain a Time/time column")


def _column_value(row: dict[str, str], column: str) -> float:
    if column not in row:
        raise ValueError(f"CSV postprocess source is missing column: {column}")
    return float(row[column])


def _check_same_row_count(tables: dict[str, list[dict[str, str]]], expected: int) -> None:
    for name, rows in tables.items():
        if len(rows) != expected:
            raise ValueError(f"ANSYS DPF CSV file {name!r} has {len(rows)} rows; expected {expected}")


def _standard_row(row: dict[str, str], columns: dict[str, str | tuple[str, ...]]) -> dict[str, float]:
    if "time" not in columns:
        raise ValueError("CSV column mapping must include 'time'")
    standard = {}
    for target, sources in columns.items():
        source_names = (sources,) if isinstance(sources, str) else tuple(sources)
        if not source_names:
            raise ValueError(f"CSV mapping for {target!r} must include at least one source column")
        values = []
        for source in source_names:
            if source not in row:
                raise ValueError(f"CSV postprocess source is missing column: {source}")
            values.append(float(row[source]))
        standard[target] = _dominant_value(values)
    return standard


def _available_optional_columns(
    row: dict[str, str],
    columns: dict[str, str | tuple[str, ...]],
) -> dict[str, str | tuple[str, ...]]:
    return {
        target: sources
        for target, sources in columns.items()
        if all(source in row for source in ((sources,) if isinstance(sources, str) else tuple(sources)))
    }


def _dominant_value(values: list[float]) -> float:
    return max(values, key=lambda value: abs(value))


def _mapdl_result_path(context: PostprocessContext) -> Path:
    job_name = _mapdl_job_name(context.execution_result.command)
    if job_name is not None:
        candidate = context.case_dir / f"{job_name}.rst"
        if candidate.exists():
            return candidate
    results = sorted(context.case_dir.glob("*.rst"))
    if len(results) == 1:
        return results[0]
    raise FileNotFoundError(f"Could not identify MAPDL RST result under {context.case_dir}")


def _mapdl_job_name(command: tuple[str, ...]) -> str | None:
    try:
        index = command.index("-j")
    except ValueError:
        return None
    if index + 1 >= len(command):
        return None
    return command[index + 1]


def _read_prrsol_total_values(path: Path) -> dict[str, float]:
    text = path.read_text(encoding="utf-8", errors="replace")
    match = re.search(r"TOTAL VALUES\s+VALUE\s+(.+)", text, flags=re.DOTALL)
    if match is None:
        raise ValueError(f"MAPDL output does not contain PRRSOL TOTAL VALUES: {path}")
    values = [
        float(value)
        for value in re.findall(
            r"[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:E[-+]?\d+)?",
            match.group(1).splitlines()[0],
        )
    ]
    if len(values) < 6:
        raise ValueError(f"MAPDL PRRSOL TOTAL VALUES did not contain 6 components: {path}")
    return dict(zip(("FX", "FY", "FZ", "MX", "MY", "MZ"), values[:6]))


def _component_value(data, component: int) -> float:
    values = data.tolist() if hasattr(data, "tolist") else data
    while values and isinstance(values[0], list):
        values = values[0]
    return float(values[component])


def _result_fields_on_all_times(dpf, model, result_name: str):
    result = getattr(model.results, result_name)()
    result.inputs.time_scoping(dpf.time_freq_scoping_factory.scoping_on_all_time_freqs(model))
    return result.outputs.fields_container()


def _write_standard_timeseries(path: Path, rows: list[dict[str, float]]) -> None:
    if not rows:
        raise ValueError("CSV postprocess source must contain at least one data row")
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0])
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _write_generic_csv(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        raise ValueError("CSV output must contain at least one data row")
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0])
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, payload: object) -> None:
    import json

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


def _standard_objectives_from_rows(rows: list[dict[str, float]]) -> dict[str, float]:
    from pyansys_bridge.core.result_summary import objectives_from_timeseries

    timeseries = {
        key: [float(row[key]) for row in rows]
        for key in rows[0]
    }
    return objectives_from_timeseries(timeseries)
