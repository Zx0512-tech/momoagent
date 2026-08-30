"""Unified solver result contract."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class AnalysisResult:
    """A solver-independent analysis result."""

    case_id: str
    solver: str
    status: str
    objectives: dict[str, float] = field(default_factory=dict)
    timeseries: dict[str, list[float]] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    started_at: str = field(default_factory=utc_now_iso)
    finished_at: str | None = None

    def finish(self, status: str = "completed") -> "AnalysisResult":
        self.status = status
        self.finished_at = utc_now_iso()
        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "solver": self.solver,
            "status": self.status,
            "objectives": dict(self.objectives),
            "timeseries": {key: list(value) for key, value in self.timeseries.items()},
            "metadata": dict(self.metadata),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }
