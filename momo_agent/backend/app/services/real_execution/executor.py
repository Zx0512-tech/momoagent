"""共享的 solver/result 执行基座。

该模块只负责把已冻结的模型、荷载和阻尼参数交给现有
``pyansys_bridge.batch`` 执行器，并把结果整理成可审计的目录。它不负责
审批、工作流顺序或预算判断；这些仍由 Agent Harness/WorkflowGuard 负责。
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from threading import Event
from typing import Any, Callable, Iterable

from pyansys_bridge.batch.case_runner import CaseRunner
from pyansys_bridge.batch.result_store import ResultStore
from pyansys_bridge.core.solver_factory import SolverFactory
from pyansys_bridge.models import AnalysisResult, BridgeModel, DamperParams, LoadCase


class RealExecutionError(RuntimeError):
    """共享执行器的失败基类。"""


class RealExecutionCancelled(RealExecutionError):
    """执行在下一个安全边界被取消。"""


class RealExecutionTimeout(RealExecutionError):
    """执行超过冻结的 wall-clock 预算。"""


@dataclass(frozen=True)
class ConfigExecutionRequest:
    """已冻结的配置执行请求。

    配置执行器仍由既有 ANSYS/OpenSeesPy 适配器负责数值计算；共享执行器
    只统一入口处的取消、超时、返回值和心跳边界。
    """

    config_path: Path
    timeout_s: float | None = None
    run_id: str | None = None
    job_id: str | None = None

    def __post_init__(self) -> None:
        if self.timeout_s is not None and self.timeout_s <= 0:
            raise ValueError("timeout_s 必须大于 0")


@dataclass(frozen=True)
class ConfigExecutionResult:
    """配置执行的稳定返回契约。"""

    payload: dict[str, Any]
    usage: dict[str, Any]


@dataclass(frozen=True)
class SolverExecutionRequest:
    """一个可重放的批量求解请求。"""

    solver: str
    bridge_model: BridgeModel
    load_cases: tuple[LoadCase, ...]
    damper_params: tuple[DamperParams, ...]
    output_dir: Path
    solver_kwargs: dict[str, Any] = field(default_factory=dict)
    run_id: str | None = None
    job_id: str | None = None
    timeout_s: float | None = None

    def __post_init__(self) -> None:
        if not self.load_cases:
            raise ValueError("至少需要一个荷载工况")
        if not self.damper_params:
            raise ValueError("至少需要一组阻尼参数")
        if self.timeout_s is not None and self.timeout_s <= 0:
            raise ValueError("timeout_s 必须大于 0")


@dataclass(frozen=True)
class ResultCatalogEntry:
    """结果目录中的一个可追问响应。"""

    case_id: str
    solver: str
    objective_names: tuple[str, ...]
    timeseries_columns: tuple[str, ...]
    units: dict[str, str]
    source_sha256: str
    verified: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "caseId": self.case_id,
            "solver": self.solver,
            "objectiveNames": list(self.objective_names),
            "timeseriesColumns": list(self.timeseries_columns),
            "units": dict(self.units),
            "sourceSha256": self.source_sha256,
            "verified": self.verified,
        }


@dataclass(frozen=True)
class SolverExecutionResult:
    """共享执行器的稳定输出。"""

    results: tuple[AnalysisResult, ...]
    result_catalog: dict[str, Any]
    output_manifest: dict[str, Any]
    usage: dict[str, Any]


class RealSolverExecutor:
    """串行安全边界执行器，复用现有 solver adapter 和 ResultStore。"""

    def __init__(
        self,
        *,
        solver_factory: Callable[..., Any] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._solver_factory = solver_factory or SolverFactory.create
        self._clock = clock or time.monotonic

    def execute(
        self,
        request: SolverExecutionRequest,
        *,
        cancel_event: Event | None = None,
        heartbeat: Callable[[str], None] | None = None,
    ) -> SolverExecutionResult:
        """执行全部组合，并在每个 case 边界检查取消、超时和心跳。"""

        started = self._clock()
        request.output_dir.mkdir(parents=True, exist_ok=True)
        store = ResultStore(request.output_dir)
        results: list[AnalysisResult] = []

        for damper_params in request.damper_params:
            for load_case in request.load_cases:
                self._check_control(started, request.timeout_s, cancel_event)
                solver_kwargs = dict(request.solver_kwargs)
                if request.output_dir and str(request.solver).lower() in {
                    "ansys",
                    "opensees",
                    "openseespy_inproc",
                }:
                    solver_kwargs.setdefault("output_dir", request.output_dir / "_command_streams")
                solver = self._solver_factory(request.solver, **solver_kwargs)
                runner = CaseRunner(solver, store)
                result = runner.run(request.bridge_model, load_case, damper_params)
                self._validate_result(result)
                results.append(result)
                if heartbeat is not None:
                    heartbeat(result.case_id)

        finished = self._clock()
        manifest = _build_result_manifest(request.output_dir, results)
        catalog = _build_result_catalog(results, manifest)
        return SolverExecutionResult(
            results=tuple(results),
            result_catalog=catalog,
            output_manifest=manifest,
            usage={
                "caseCount": len(results),
                "elapsedSeconds": max(finished - started, 0.0),
                "solver": request.solver,
                "runId": request.run_id,
                "jobId": request.job_id,
            },
        )

    def execute_config(
        self,
        request: ConfigExecutionRequest,
        *,
        runner: Callable[..., Any],
        cancel_event: Event | None = None,
        heartbeat: Callable[[str], None] | None = None,
    ) -> ConfigExecutionResult:
        """通过既有配置 runner 执行一次真实流程。

        runner 必须接受 ``(config_path, execution_timeout_s=...)``，并返回
        dict 或带 ``to_dict`` 的结果对象。共享边界不修改业务 payload，
        只增加统一 usage 审计信息。
        """

        started = self._clock()
        self._check_control(started, request.timeout_s, cancel_event)
        result = runner(request.config_path, execution_timeout_s=request.timeout_s)
        self._check_control(started, request.timeout_s, cancel_event)
        payload = result.to_dict() if hasattr(result, "to_dict") else result
        if not isinstance(payload, dict):
            raise RealExecutionError("配置 runner 必须返回 dict 或实现 to_dict() 的结果")
        _validate_config_payload(payload)
        if heartbeat is not None:
            heartbeat("completed")
        finished = self._clock()
        return ConfigExecutionResult(
            payload=dict(payload),
            usage={
                "elapsedSeconds": max(finished - started, 0.0),
                "runId": request.run_id,
                "jobId": request.job_id,
                "configPath": str(request.config_path),
            },
        )

    def _check_control(
        self,
        started: float,
        timeout_s: float | None,
        cancel_event: Event | None,
    ) -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise RealExecutionCancelled("执行已按请求取消")
        if timeout_s is not None and self._clock() - started >= timeout_s:
            raise RealExecutionTimeout(f"执行超过 {timeout_s:g} 秒预算")

    @staticmethod
    def _validate_result(result: AnalysisResult) -> None:
        if result.status != "completed":
            raise RealExecutionError(f"求解 case {result.case_id} 未完成: {result.status}")
        for name, value in result.objectives.items():
            if not math.isfinite(float(value)):
                raise RealExecutionError(f"求解结果 {name} 不是有限数")
        for name, values in result.timeseries.items():
            if not values:
                raise RealExecutionError(f"结果列 {name} 为空")
            if not all(math.isfinite(float(value)) for value in values):
                raise RealExecutionError(f"结果列 {name} 包含非有限数")


def _build_result_manifest(output_dir: Path, results: Iterable[AnalysisResult]) -> dict[str, Any]:
    expected_case_ids = {result.case_id for result in results}
    files: list[dict[str, Any]] = []
    for path in sorted(output_dir.rglob('*'), key=lambda item: item.as_posix()):
        if not path.is_file() or path.name.startswith('.'):
            continue
        resolved = path.resolve()
        if not resolved.is_relative_to(output_dir.resolve()):
            raise RealExecutionError(f"结果制品越界: {path}")
        raw = path.read_bytes()
        files.append({
            "path": resolved.relative_to(output_dir.resolve()).as_posix(),
            "sizeBytes": len(raw),
            "sha256": sha256(raw).hexdigest(),
        })
    summary_case_ids = {
        path.split('/', 1)[0]
        for path in (item["path"] for item in files)
        if path.endswith('/summary.json')
    }
    missing = expected_case_ids - summary_case_ids
    if missing:
        raise RealExecutionError(f"结果制品缺失: {sorted(missing)}")
    return {
        "schemaVersion": "1.0",
        "rootDirectory": output_dir.name,
        "fileCount": len(files),
        "totalBytes": sum(item["sizeBytes"] for item in files),
        "files": files,
    }


def _validate_config_payload(payload: dict[str, Any]) -> None:
    """拒绝 runner 明确报告失败的 payload，避免写入伪成功轨迹。"""

    status_values: list[str] = []
    for key, value in payload.items():
        if key.lower().endswith("status") and isinstance(value, str):
            status_values.append(value.strip().lower())
    if any(status in {"failed", "failure", "error", "cancelled", "canceled", "timeout"} for status in status_values):
        raise RealExecutionError(f"配置 runner 返回失败状态: {status_values}")


def _build_result_catalog(
    results: Iterable[AnalysisResult],
    manifest: dict[str, Any],
) -> dict[str, Any]:
    hashes = {
        item["path"].split("/", 1)[0]: item["sha256"]
        for item in manifest["files"]
        if item["path"].endswith('/summary.json')
    }
    entries = []
    for result in results:
        entries.append(ResultCatalogEntry(
            case_id=result.case_id,
            solver=result.solver,
            objective_names=tuple(sorted(result.objectives)),
            timeseries_columns=tuple(sorted(result.timeseries)),
            units=_result_units(result),
            source_sha256=hashes[result.case_id],
            verified=True,
        ).to_dict())
    return {
        "schemaVersion": "1.0",
        "caseCount": len(entries),
        "entries": entries,
        "manifestFileCount": manifest["fileCount"],
    }


def _result_units(result: AnalysisResult) -> dict[str, str]:
    standard_units = {
        "time": "s",
        "displacement": "m",
        "max_displacement": "m",
        "max_girder_end_displacement": "m",
        "acceleration": "m/s²",
        "max_acceleration": "m/s²",
        "tower_base_moment": "N*m",
        "max_tower_base_moment": "N*m",
        "tower_base_shear": "N",
        "max_tower_base_shear": "N",
        "damper_force": "N",
        "max_damper_force": "N",
        "damper_stroke": "m",
        "max_damper_stroke": "m",
        "dissipated_energy": "J",
        "cumulative_displacement": "m",
    }
    raw_units = result.metadata.get("units") if isinstance(result.metadata, dict) else None
    units = {str(key): str(value) for key, value in (raw_units or {}).items()}
    for key in (*result.objectives.keys(), *result.timeseries.keys()):
        units.setdefault(key, standard_units.get(key, "1"))
    return units
