"""Unified load generation protocol."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from pyansys_bridge.models import TimeHistoryLoad


@dataclass(frozen=True)
class ValidationResult:
    """Validation result returned by load generators."""

    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def valid(self) -> bool:
        return not self.errors

    @property
    def error_message(self) -> str:
        return "; ".join(self.errors)

    @classmethod
    def ok(cls, warnings: tuple[str, ...] = ()) -> "ValidationResult":
        return cls(warnings=warnings)


class LoadGenerator(Protocol):
    """Adapter contract for earthquake, wind, and traffic load generation."""

    def validate_params(self, params: dict[str, Any]) -> ValidationResult:
        """Validate generator params before producing loads."""

    def generate(self, params: dict[str, Any]) -> list[TimeHistoryLoad]:
        """Generate one or more unified time-history loads."""

    def write_time_history_csv(self, params: dict[str, Any], path: str | Path) -> Path:
        """Generate and serialize the primary unified time-history load."""
