"""MAPDL execution helpers.

The production solver currently uses MAPDL batch execution through
``CommandRunner``.  The legacy PyMAPDL session manager remains available, but
imports ``ansys.mapdl.core`` lazily so fast tests do not require ANSYS.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterable

from pyansys_bridge.core.workspace import CaseWorkspace


@dataclass(frozen=True)
class MapdlBatchConfig:
    executable: str = "MAPDL.exe"
    args: tuple[str, ...] = ()
    nproc: int | None = None
    memory_mb: int | None = None

    @classmethod
    def from_options(
        cls,
        *,
        executable: str = "MAPDL.exe",
        args: Iterable[str] | None = None,
        nproc: int | None = None,
        memory_mb: int | None = None,
    ) -> "MapdlBatchConfig":
        return cls(executable=executable, args=tuple(args or ()), nproc=nproc, memory_mb=memory_mb)


class MapdlBatchManager:
    """Build isolated MAPDL batch commands for one case workspace."""

    def __init__(self, config: MapdlBatchConfig | None = None) -> None:
        self.config = config or MapdlBatchConfig()

    def build_batch_command(self, workspace: CaseWorkspace) -> list[str]:
        command_stream_path = workspace.command_stream_path.resolve()
        execution_dir = workspace.case_dir.resolve()
        command = [
            self.config.executable,
            *self.config.args,
            "-b",
            "-i",
            str(command_stream_path),
            "-o",
            str(execution_dir / "ansys.out"),
            "-j",
            workspace.job_name,
            "-dir",
            str(execution_dir),
        ]
        if self.config.nproc is not None:
            command.extend(["-np", str(self.config.nproc)])
        if self.config.memory_mb is not None:
            command.extend(["-m", str(self.config.memory_mb)])
        return command

    def execution_env(self, workspace: CaseWorkspace) -> dict[str, str]:
        return workspace.ansys_env()

    def execution_log_path(self, workspace: CaseWorkspace):
        return workspace.execution_log_path

    def cleanup_zombie_processes(self) -> int:
        """Reserved process-cleanup hook.

        The batch runner does not own process discovery yet; returning zero
        makes that explicit without killing unrelated user MAPDL sessions.
        """

        return 0


class MAPDLManager:
    """Legacy PyMAPDL session manager kept for compatibility."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(MAPDLManager, cls).__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        if getattr(self, "_initialized", False):
            return
        self.config: dict[str, Any] = {}
        self.logger: list[str] = []
        self.is_connected = False
        self._mapdl = None
        self._initialized = True

    def launch(self, config: dict[str, Any] | None = None):
        launch_mapdl = _load_launch_mapdl()
        self.config = dict(config or {})
        launch_options = {
            "nproc": self.config.get("nproc", 1),
            "ram": self.config.get("ram", 4096),
            "override": True,
            "mode": "gui" if self.config.get("gui") else "grpc",
        }
        if self.config.get("working_dir"):
            launch_options["run_location"] = self.config["working_dir"]
        if self.config.get("jobname"):
            launch_options["jobname"] = self.config["jobname"]
        if self.config.get("ansys_path"):
            launch_options["ansys_path"] = self.config["ansys_path"]

        try:
            self._mapdl = launch_mapdl(**launch_options)
            self.is_connected = True
            return self._mapdl
        except Exception as exc:  # pragma: no cover - requires local ANSYS.
            self.is_connected = False
            raise RuntimeError(f"Unable to launch MAPDL: {exc}") from exc

    def connect(self, config: dict[str, Any] | None = None, **kwargs):
        merged = dict(config or {})
        merged.update(kwargs)
        return self.launch(merged)

    def get_mapdl(self):
        if self._mapdl is None or not self.is_connected:
            return None
        return self._mapdl

    def check_status(self) -> bool:
        if self._mapdl is None:
            return False
        try:
            self._mapdl.run("/NOPR")
        except Exception:
            self.is_connected = False
            return False
        self.is_connected = True
        return True

    def restart(self):
        self.cleanup()
        return self.launch(self.config)

    def cleanup(self) -> None:
        if self._mapdl is None:
            self.is_connected = False
            return
        try:
            self._mapdl.exit()
        finally:
            self._mapdl = None
            self.is_connected = False

    def clear_database(self) -> None:
        if self._mapdl is not None:
            self._mapdl.clear()

    def save_database(self, filename: str | None = None) -> None:
        if self._mapdl is not None:
            if filename is None:
                self._mapdl.save()
            else:
                self._mapdl.save(filename)

    def resume_database(self, filename: str) -> None:
        if self._mapdl is not None:
            self._mapdl.resume(filename)

    def get_logs(self) -> list[str]:
        return list(self.logger)

    def print_logs(self) -> None:
        for log in self.logger:
            print(log)


@contextmanager
def managed_mapdl_session(config: dict[str, Any] | None = None):
    manager = MAPDLManager()
    try:
        yield manager.launch(config)
    finally:
        manager.cleanup()


def get_manager() -> MAPDLManager:
    return MAPDLManager()


def quick_launch(nproc: int = 1, ram: int = 4096):
    return MAPDLManager().launch({"nproc": nproc, "ram": ram})


def _load_launch_mapdl():
    try:
        from ansys.mapdl.core import launch_mapdl
    except ImportError as exc:  # pragma: no cover - depends on optional package.
        raise RuntimeError("ansys-mapdl-core is required for PyMAPDL sessions") from exc
    return launch_mapdl


__all__ = [
    "MAPDLManager",
    "MapdlBatchConfig",
    "MapdlBatchManager",
    "get_manager",
    "managed_mapdl_session",
    "quick_launch",
]
