from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class WorkflowPlaceholderResponse:
    project_name: str
    summary: dict[str, Any] = field(default_factory=lambda: {"status": "placeholder"})
    spectra: list[Any] = field(default_factory=list)
    histories: list[Any] = field(default_factory=list)
    exports: list[Any] = field(default_factory=list)


def build_placeholder_response(project_name: str) -> WorkflowPlaceholderResponse:
    return WorkflowPlaceholderResponse(project_name=project_name)
