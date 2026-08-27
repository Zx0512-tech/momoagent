"""Command execution helpers for optional real solver runs."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Protocol


@dataclass(frozen=True)
class CommandExecutionResult:
    """Captured external command execution result."""

    command: tuple[str, ...]
    cwd: str
    returncode: int
    stdout: str = ""
    stderr: str = ""
    log_path: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "command": list(self.command),
            "cwd": self.cwd,
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "log_path": self.log_path,
        }


class CommandRunner(Protocol):
    """Interface used by solver adapters to run external commands."""

    def run(
        self,
        command: list[str],
        cwd: str | Path,
        timeout_s: float | None = None,
        log_path: str | Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> CommandExecutionResult:
        """Run a command and return its captured result."""


class SubprocessCommandRunner:
    """Run commands through subprocess with captured output."""

    def run(
        self,
        command: list[str],
        cwd: str | Path,
        timeout_s: float | None = None,
        log_path: str | Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> CommandExecutionResult:
        process = subprocess.Popen(
            command,
            cwd=str(cwd),
            env=None if env is None else {**os.environ, **env},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            stdout, stderr = process.communicate(timeout=timeout_s)
            returncode = process.returncode
        except subprocess.TimeoutExpired as exc:
            _terminate_process_tree(process)
            try:
                stdout, stderr = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate()
            returncode = 124
            timeout_stdout = _process_output_text(exc.stdout)
            timeout_stderr = _process_output_text(exc.stderr)
            if timeout_stdout and timeout_stdout not in stdout:
                stdout = f"{timeout_stdout}{stdout}"
            if timeout_stderr and timeout_stderr not in stderr:
                stderr = f"{timeout_stderr}{stderr}"
            timeout_message = f"Command timed out after {timeout_s} seconds."
            stderr = f"{stderr}\n{timeout_message}" if stderr else timeout_message
        log_value = None
        if log_path is not None:
            target = Path(log_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                stdout + ("\nSTDERR:\n" + stderr if stderr else ""),
                encoding="utf-8",
            )
            log_value = str(target)
        return CommandExecutionResult(
            command=tuple(command),
            cwd=str(cwd),
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
            log_path=log_value,
        )


def _process_output_text(output: str | bytes | None) -> str:
    if output is None:
        return ""
    if isinstance(output, bytes):
        return output.decode(errors="replace")
    return output


def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            text=True,
            check=False,
        )
        if process.poll() is None:
            process.kill()
    else:
        process.kill()
