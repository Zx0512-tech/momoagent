"""Opt-in MAPDL batch connection pool helpers."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import Enum
from typing import Sequence

from pyansys_bridge.config.settings import Config
from pyansys_bridge.core.command_runner import CommandExecutionResult, CommandRunner
from pyansys_bridge.core.mapdl_manager import MapdlBatchManager
from pyansys_bridge.core.workspace import CaseWorkspace


class MapdlFailureClass(str, Enum):
    MODEL_FATAL = "MODEL_FATAL"
    RESOURCE_TRANSIENT = "RESOURCE_TRANSIENT"
    CONVERGENCE = "CONVERGENCE"


@dataclass(frozen=True)
class MapdlPoolConfig:
    enabled: bool = False
    max_workers: int = 1
    license_limit: int | None = None
    retry_limit: int = 0
    timeout_s: float | None = None
    cleanup_zombies: bool = True

    @classmethod
    def from_project_config(
        cls,
        *,
        enabled: bool = False,
        license_limit: int | None = None,
        retry_limit: int = 0,
        config: Config | None = None,
    ) -> "MapdlPoolConfig":
        project_config = config or Config()
        return cls(
            enabled=enabled,
            max_workers=int(project_config.get("mapdl.max_workers", 1)),
            license_limit=license_limit,
            retry_limit=retry_limit,
            timeout_s=project_config.get("mapdl.timeout_s"),
            cleanup_zombies=bool(project_config.get("mapdl.cleanup_zombies", True)),
        )

    @property
    def effective_workers(self) -> int:
        configured = max(1, int(self.max_workers))
        if self.license_limit is None:
            return configured
        return max(1, min(configured, int(self.license_limit)))


@dataclass(frozen=True)
class MapdlFailureDecision:
    failure_class: MapdlFailureClass
    retryable: bool
    reason: str


@dataclass(frozen=True)
class MapdlPoolCaseResult:
    workspace: CaseWorkspace
    status: str
    attempts: int
    execution_result: CommandExecutionResult
    failure_class: MapdlFailureClass | None = None


class MapdlPool:
    """Run multiple MAPDL batch workspaces with explicit opt-in parallelism."""

    def __init__(
        self,
        config: MapdlPoolConfig | None = None,
        manager: MapdlBatchManager | None = None,
    ) -> None:
        self.config = config or MapdlPoolConfig()
        self.manager = manager or MapdlBatchManager()

    def run_batch(
        self,
        workspaces: Sequence[CaseWorkspace],
        *,
        runner: CommandRunner,
        timeout_s: float | None = None,
    ) -> list[MapdlPoolCaseResult]:
        if not self.config.enabled:
            raise RuntimeError("MapdlPool is disabled by default; set enabled=True to run batch cases")
        if self.config.cleanup_zombies:
            self.manager.cleanup_zombie_processes()
        indexed_workspaces = list(enumerate(workspaces))
        if not indexed_workspaces:
            return []
        effective_timeout_s = self.config.timeout_s if timeout_s is None else timeout_s

        results: list[MapdlPoolCaseResult | None] = [None] * len(indexed_workspaces)
        with ThreadPoolExecutor(max_workers=self.config.effective_workers) as executor:
            futures = [
                executor.submit(self._run_one, workspace, runner=runner, timeout_s=effective_timeout_s)
                for _, workspace in indexed_workspaces
            ]
            for (index, _), future in zip(indexed_workspaces, futures):
                results[index] = future.result()
        return [result for result in results if result is not None]

    def _run_one(
        self,
        workspace: CaseWorkspace,
        *,
        runner: CommandRunner,
        timeout_s: float | None,
    ) -> MapdlPoolCaseResult:
        attempts = 0
        last_result: CommandExecutionResult | None = None
        last_decision: MapdlFailureDecision | None = None
        while attempts <= self.config.retry_limit:
            attempts += 1
            workspace.prepare()
            result = runner.run(
                self.manager.build_batch_command(workspace),
                cwd=workspace.case_dir,
                timeout_s=timeout_s,
                log_path=self.manager.execution_log_path(workspace),
                env=self.manager.execution_env(workspace),
            )
            last_result = result
            if result.returncode == 0:
                return MapdlPoolCaseResult(
                    workspace=workspace,
                    status="completed",
                    attempts=attempts,
                    execution_result=result,
                )
            decision = classify_mapdl_failure(result)
            last_decision = decision
            if not decision.retryable:
                break

        assert last_result is not None
        assert last_decision is not None
        return MapdlPoolCaseResult(
            workspace=workspace,
            status="failed",
            attempts=attempts,
            execution_result=last_result,
            failure_class=last_decision.failure_class,
        )


def classify_mapdl_failure(result: CommandExecutionResult) -> MapdlFailureDecision:
    text = f"{result.stdout}\n{result.stderr}".lower()
    if result.returncode == 124 or any(
        marker in text
        for marker in (
            "license",
            "licence",
            "temporarily unavailable",
            "connection refused",
            "timed out",
            "timeout",
        )
    ):
        return MapdlFailureDecision(
            failure_class=MapdlFailureClass.RESOURCE_TRANSIENT,
            retryable=True,
            reason="resource transient failure",
        )
    if "converge" in text or "convergence" in text:
        return MapdlFailureDecision(
            failure_class=MapdlFailureClass.CONVERGENCE,
            retryable=True,
            reason="solver convergence failure",
        )
    return MapdlFailureDecision(
        failure_class=MapdlFailureClass.MODEL_FATAL,
        retryable=False,
        reason="model fatal failure",
    )


__all__ = [
    "MapdlFailureClass",
    "MapdlFailureDecision",
    "MapdlPool",
    "MapdlPoolCaseResult",
    "MapdlPoolConfig",
    "classify_mapdl_failure",
]
