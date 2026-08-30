"""Surrogate dataset container."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import json

import numpy as np


@dataclass(frozen=True)
class SurrogateDataset:
    """Training data for surrogate models."""

    x: np.ndarray
    y: np.ndarray
    feature_names: tuple[str, ...] = ("c", "alpha")
    target_name: str = "response"

    def __post_init__(self) -> None:
        if self.x.ndim != 2:
            raise ValueError("x must be a 2D array")
        if self.y.ndim != 1:
            raise ValueError("y must be a 1D array")
        if self.x.shape[0] != self.y.shape[0]:
            raise ValueError("x and y must contain the same number of samples")
        if self.x.shape[1] != len(self.feature_names):
            raise ValueError("feature_names must match the x column count")


def dataset_from_result_store(
    result_root: str | Path,
    target_name: str,
    feature_names: tuple[str, ...] = ("c", "alpha"),
    status: str = "completed",
    load_case_name: str | None = None,
    case_ids: set[str] | frozenset[str] | tuple[str, ...] | list[str] | None = None,
) -> SurrogateDataset:
    """Build a surrogate dataset from persisted batch result summaries."""

    allowed_case_ids = None if case_ids is None else {str(case_id) for case_id in case_ids}
    rows = []
    targets = []
    for payload in _iter_result_summaries(result_root):
        case_id = str(payload.get("case_id", "<unknown>"))
        if allowed_case_ids is not None and case_id not in allowed_case_ids:
            continue
        if payload.get("status") != status:
            continue
        metadata = payload.get("metadata", {})
        if load_case_name is not None and metadata.get("load_case", {}).get("name") != load_case_name:
            continue
        damper_params = metadata.get("damper_params", {})
        solver_design = metadata.get("solver_design", {})
        objectives = payload.get("objectives", {})
        if target_name not in objectives:
            continue
        try:
            row = [_feature_value(name, damper_params, solver_design) for name in feature_names]
        except KeyError as exc:
            raise ValueError(f"Missing feature in result summary: {exc.args[0]}") from exc
        for name, value in zip(feature_names, row):
            if not np.isfinite(value):
                raise ValueError(f"Non-finite feature {name} in result summary: {case_id}")
        rows.append(row)
        target_value = float(objectives[target_name])
        if not np.isfinite(target_value):
            raise ValueError(f"Non-finite target objective {target_name} in result summary: {case_id}")
        targets.append(target_value)

    if not rows:
        raise ValueError(f"No completed result summaries contain target objective: {target_name}")

    return SurrogateDataset(
        x=np.asarray(rows, dtype=float),
        y=np.asarray(targets, dtype=float),
        feature_names=feature_names,
        target_name=target_name,
    )


def _iter_result_summaries(result_root: str | Path):
    root = Path(result_root)
    for summary_path in sorted(root.glob("*/summary.json")):
        with summary_path.open("r", encoding="utf-8") as handle:
            payload: dict[str, Any] = json.load(handle)
        yield payload


def _feature_value(name: str, damper_params: dict[str, Any], solver_design: dict[str, Any]) -> float:
    if name in damper_params:
        return float(damper_params[name])
    if name in solver_design:
        return float(solver_design[name])
    raise KeyError(name)
