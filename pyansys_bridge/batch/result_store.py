"""Persistent result storage for batch runs."""

from __future__ import annotations

import csv
from hashlib import sha256
import json
import math
from pathlib import Path
import time
from uuid import uuid4

from pyansys_bridge.models import AnalysisResult

DERIVED_DAMPER_COST_OBJECTIVES = frozenset({"Cost_L", "Cost_R", "Cost_total"})


class ResultStore:
    """Store case summaries as JSON and maintain an index CSV."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def case_dir(self, case_id: str) -> Path:
        return self.root / case_id

    def has_completed(self, case_id: str) -> bool:
        summary_path = self.case_dir(case_id) / "summary.json"
        if not summary_path.exists():
            return False
        with summary_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        objectives = payload.get("objectives", {})
        return (
            payload.get("status") == "completed"
            and isinstance(objectives, dict)
            and bool(objectives)
            and _has_finite_objective_values(objectives)
            and _has_valid_run_mode_artifacts(payload)
        )

    def save(self, result: AnalysisResult) -> Path:
        target = self.case_dir(result.case_id)
        target.mkdir(parents=True, exist_ok=True)
        summary_path = target / "summary.json"
        _write_json_atomic(summary_path, result.to_dict())
        self._write_index()
        return summary_path

    def load(self, case_id: str) -> AnalysisResult:
        summary_path = self.case_dir(case_id) / "summary.json"
        with summary_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return AnalysisResult(
            case_id=payload["case_id"],
            solver=payload["solver"],
            status=payload["status"],
            objectives={key: float(value) for key, value in payload.get("objectives", {}).items()},
            timeseries={
                key: [float(item) for item in values]
                for key, values in payload.get("timeseries", {}).items()
            },
            metadata=payload.get("metadata", {}),
            started_at=payload.get("started_at"),
            finished_at=payload.get("finished_at"),
        )

    def _write_index(self) -> None:
        rows = []
        for summary in sorted(self.root.glob("*/summary.json")):
            with summary.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
            row = {
                "case_id": payload["case_id"],
                "solver": payload["solver"],
                "status": payload["status"],
            }
            row.update(payload.get("objectives", {}))
            rows.append(row)
        if not rows:
            return
        fieldnames = sorted({key for row in rows for key in row})
        index_path = self.root / "index.csv"
        temp_path = _temp_path_for(index_path)
        with temp_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        _replace_atomic(temp_path, index_path, required=False)


def _write_json_atomic(path: Path, payload: dict) -> None:
    temp_path = _temp_path_for(path)
    with temp_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    _replace_atomic(temp_path, path, required=True)


def _replace_atomic(temp_path: Path, target_path: Path, *, required: bool) -> None:
    for attempt in range(20):
        try:
            temp_path.replace(target_path)
            return
        except PermissionError:
            if attempt == 19:
                if required:
                    raise
                try:
                    temp_path.unlink()
                except FileNotFoundError:
                    pass
                return
            time.sleep(0.02)


def _temp_path_for(path: Path) -> Path:
    return path.with_name(f".tmp-{uuid4().hex[:12]}")


def _has_finite_objective_values(objectives: dict) -> bool:
    try:
        return all(math.isfinite(float(value)) for value in objectives.values())
    except (TypeError, ValueError):
        return False


def _has_valid_run_mode_artifacts(payload: dict) -> bool:
    metadata = payload.get("metadata", {})
    design = metadata.get("solver_design", {})
    if design.get("execution_mode") != "run":
        return True
    command_stream = metadata.get("command_stream", {})
    solver_summary = metadata.get("solver_summary", {})
    return (
        command_stream.get("dry_run") is False
        and _metadata_hash_matches(command_stream)
        and _metadata_hash_matches(solver_summary)
        and _solver_summary_lists_objectives(solver_summary, payload.get("objectives", {}))
    )


def _metadata_hash_matches(metadata: dict) -> bool:
    path = metadata.get("path")
    expected = metadata.get("sha256")
    if not path or not expected:
        return False
    artifact = Path(path)
    return artifact.is_file() and sha256(artifact.read_bytes()).hexdigest() == expected


def _solver_summary_lists_objectives(metadata: dict, objectives: dict) -> bool:
    objective_names = metadata.get("objective_names", ())
    if not objective_names:
        return False
    solver_objectives = set(objectives) - DERIVED_DAMPER_COST_OBJECTIVES
    return solver_objectives.issubset({str(name) for name in objective_names})
