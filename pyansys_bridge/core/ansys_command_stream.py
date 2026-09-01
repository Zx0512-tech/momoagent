"""ANSYS APDL command-stream write boundary."""

from __future__ import annotations

from pathlib import Path

from pyansys_bridge.core.apdl_validator import validate_apdl
from pyansys_bridge.core.workspace import CaseWorkspace


def write_apdl_command_stream(workspace: CaseWorkspace, text: str) -> Path:
    validate_apdl(text)
    return workspace.write_command_stream(text)
