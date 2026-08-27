"""Bridge model metadata contract."""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class BridgeModel:
    """Metadata for a bridge model used by one or more solvers."""

    name: str
    source_path: str
    unit_system: str = "SI"
    critical_nodes: dict[str, list[int]] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("BridgeModel.name is required")
        if not self.source_path:
            raise ValueError("BridgeModel.source_path is required")

    def source_hash(self) -> str | None:
        path = Path(self.source_path)
        if not path.exists() or not path.is_file():
            return None
        digest = sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "source_path": self.source_path,
            "unit_system": self.unit_system,
            "critical_nodes": self.critical_nodes,
            "metadata": dict(self.metadata),
            "source_hash": self.source_hash(),
        }

    def fingerprint(self) -> str:
        payload = repr(self.to_dict()).encode("utf-8")
        return sha256(payload).hexdigest()[:12]
