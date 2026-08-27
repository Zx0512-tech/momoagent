"""Damper parameter contract."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256


@dataclass(frozen=True)
class DamperParams:
    """Parameters for a nonlinear viscous damper."""

    c: float
    alpha: float
    stiffness: float | None = None
    regularization_velocity: float | None = None

    def __post_init__(self) -> None:
        if self.c <= 0:
            raise ValueError("Damper coefficient c must be positive")
        if self.stiffness is not None and self.stiffness <= 0:
            raise ValueError("Damper stiffness must be positive when provided")
        if self.regularization_velocity is not None and self.regularization_velocity <= 0:
            raise ValueError("Damper regularization velocity must be positive when provided")

    def to_dict(self) -> dict[str, float | None]:
        payload = {"c": float(self.c), "alpha": float(self.alpha), "stiffness": self.stiffness}
        if self.regularization_velocity is not None:
            payload["regularization_velocity"] = float(self.regularization_velocity)
        return payload

    def fingerprint(self) -> str:
        payload = (
            f"c={self.c:.12g};alpha={self.alpha:.12g};stiffness={self.stiffness};"
            f"regularization_velocity={self.regularization_velocity}"
        )
        return sha256(payload.encode("utf-8")).hexdigest()[:12]
