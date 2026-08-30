"""Load case combination helpers."""

from __future__ import annotations

from typing import Any

from pyansys_bridge.models import LoadCase


def combine_load_cases(
    name: str,
    load_cases: list[LoadCase],
    scale: float = 1.0,
    metadata: dict[str, Any] | None = None,
) -> LoadCase:
    """Create a named combination without altering the source load cases."""

    if not load_cases:
        raise ValueError("At least one load case is required")
    dts = {case.dt for case in load_cases if case.dt is not None}
    if len(dts) > 1:
        raise ValueError("Combined load cases must use a unified dt")
    duration = max((case.duration or 0.0) for case in load_cases)
    return LoadCase(
        name=name,
        load_type="combination",
        dt=next(iter(dts)) if dts else None,
        duration=duration,
        scale=scale,
        components=tuple(case.name for case in load_cases),
        metadata={
            **dict(metadata or {}),
            "component_hashes": [case.fingerprint() for case in load_cases],
            "component_types": [case.load_type for case in load_cases],
            "component_load_cases": [case.to_dict() for case in load_cases],
        },
    )
