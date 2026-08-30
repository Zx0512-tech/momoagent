"""Per-case solver workspace paths and cleanup policy."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil

CleanupPolicy = str


@dataclass(frozen=True)
class CaseWorkspace:
    root: str | Path
    case_id: str
    solver_name: str
    file_extension: str
    cleanup: CleanupPolicy = "never"

    @property
    def case_dir(self) -> Path:
        return Path(self.root) / self.case_id

    @property
    def command_stream_path(self) -> Path:
        return self.case_dir / f"{self.solver_name}_command{self.file_extension}"

    @property
    def execution_log_path(self) -> Path:
        if self.solver_name == "ansys":
            return self.case_dir / "ansys_execution.log"
        return self.case_dir / f"{self.solver_name}_execution.log"

    @property
    def job_name(self) -> str:
        prefix = "stbridge" if self.solver_name == "ansys" else self.solver_name
        return f"{prefix}_{self.case_id[:8]}"

    def prepare(self) -> Path:
        self.case_dir.mkdir(parents=True, exist_ok=True)
        return self.case_dir

    def write_command_stream(self, text: str) -> Path:
        self.prepare()
        content = text if text.endswith("\n") else f"{text}\n"
        self.command_stream_path.write_text(content, encoding="utf-8")
        return self.command_stream_path

    def ansys_env(self) -> dict[str, str]:
        ansys_env_dir = self.case_dir / "_ansys_env"
        temp_dir = ansys_env_dir / "tmp"
        appdata_dir = ansys_env_dir / "AppData" / "Roaming"
        local_appdata_dir = ansys_env_dir / "AppData" / "Local"
        for directory in (temp_dir, appdata_dir, local_appdata_dir):
            directory.mkdir(parents=True, exist_ok=True)
        return {
            "TMP": str(temp_dir.resolve()),
            "TEMP": str(temp_dir.resolve()),
            "APPDATA": str(appdata_dir.resolve()),
            "LOCALAPPDATA": str(local_appdata_dir.resolve()),
            "ANSYS_LOCK": "OFF",
        }

    def cleanup_after(self, *, success: bool) -> None:
        if not _should_cleanup(self.cleanup, success=success):
            return
        root = Path(self.root).resolve()
        target = self.case_dir.resolve()
        if target == root or not target.is_relative_to(root):
            raise ValueError(f"Refusing to cleanup workspace outside root: {target}")
        if target.exists():
            shutil.rmtree(target)


def _should_cleanup(policy: CleanupPolicy, *, success: bool) -> bool:
    normalized = str(policy).lower().replace("-", "_")
    if normalized in {"never", "false", "0", "none"}:
        return False
    if normalized in {"always", "true", "1"}:
        return True
    if normalized == "on_success":
        return success
    if normalized == "on_failure":
        return not success
    raise ValueError("cleanup must be one of: never, always, on_success, on_failure")
