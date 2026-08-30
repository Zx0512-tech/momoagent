"""Matrix-aligned STbridge OpenSeesPy reference model builder.

This builder wraps the user-provided monolithic reference script without
executing modal analysis at import time.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
import sys
from typing import Any


REFERENCE_MODEL_SCRIPT = Path(r"D:\pyansys\stbridge_opensees.py")
ANALYSIS_STATE = "linear_no_initial_stress"
DEFAULT_REFERENCE_FREQUENCIES_HZ = [0.06, 0.10, 0.18, 0.23, 0.29, 0.32, 0.39, 0.42, 0.43, 0.47]


def build_model(source_path: str | Path | None = None) -> dict[str, Any]:
    """Build the reference model and stop before modal eigen extraction."""

    namespace = _model_namespace(source_path)
    source = _model_source_before_eigen(namespace["__source_path__"])
    exec(compile(source, str(namespace["__source_path__"]), "exec"), namespace)
    ops = namespace["ops"]
    if not ops.getNodeTags():
        raise RuntimeError("STbridge reference builder produced no nodes")
    namespace["analysis_state"] = ANALYSIS_STATE
    return namespace


def run_modal(
    num_modes: int = 20,
    reference_frequencies_hz: list[float] | None = None,
    source_path: str | Path | None = None,
    eigen_solver: str = "fullGenLapack",
) -> dict[str, Any]:
    """Run modal analysis for the reference model."""

    ops = _require_ops()
    if not ops.getNodeTags():
        build_model(source_path)
    try:
        if eigen_solver == "default":
            eigen_values = list(ops.eigen(int(num_modes)))
        else:
            eigen_values = list(ops.eigen(eigen_solver, int(num_modes)))
    except Exception:
        eigen_values = list(ops.eigen("-fullGenLapack", int(num_modes)))
    frequencies = _frequencies_from_eigenvalues(eigen_values)
    references = list(reference_frequencies_hz or DEFAULT_REFERENCE_FREQUENCIES_HZ)
    errors_pct = [
        abs(frequency - reference) / reference * 100.0
        for frequency, reference in zip(frequencies, references)
        if reference > 0.0
    ]
    return {
        "analysis_state": ANALYSIS_STATE,
        "eigen_solver": eigen_solver,
        "eigenvalues": eigen_values,
        "frequencies": frequencies,
        "reference": references,
        "errors_pct": errors_pct,
        "all_within_5pct": bool(errors_pct) and all(error <= 5.0 for error in errors_pct[:10]),
    }


def _model_source_before_eigen(source_path: str | Path | None = None) -> str:
    source = Path(source_path or REFERENCE_MODEL_SCRIPT)
    lines = source.read_text(encoding="utf-8").splitlines()
    stop_line = _first_eigen_line(source)
    selected: list[str] = []
    for line_number, line in enumerate(lines, start=1):
        if line_number >= stop_line:
            break
        stripped = line.strip()
        # builder 已注入 ops，避免加载参考脚本时触发平台相关 OpenSeesPy 导入。
        if stripped == "import openseespy.opensees as ops":
            continue
        selected.append(line)
    return "\n".join(selected) + "\n"


def _first_eigen_line(source: Path) -> int:
    import ast

    tree = ast.parse(source.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _is_ops_call(node, "eigen"):
            return node.lineno
    raise RuntimeError(f"ops.eigen call not found in {source}")


def _is_ops_call(node: Any, name: str) -> bool:
    import ast

    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == name
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "ops"
    )


def _model_namespace(source_path: str | Path | None = None) -> dict[str, Any]:
    ops = _require_ops()
    resolved_source = Path(source_path or REFERENCE_MODEL_SCRIPT)
    return {
        "ops": ops,
        "json": json,
        "math": math,
        "sys": sys,
        "__file__": str(resolved_source),
        "__name__": "__stbridge_opensees_reference_builder__",
        "__source_path__": resolved_source,
    }


def _frequencies_from_eigenvalues(eigen_values: list[float]) -> list[float]:
    return [math.sqrt(value) / (2.0 * math.pi) for value in eigen_values if value > 1.0e-10]


def _require_ops():
    try:
        import platform

        if not platform.machine():
            platform.machine = lambda: "AMD64"  # type: ignore[assignment]
        import openseespy.opensees as ops
    except Exception as exc:
        raise RuntimeError(f"OpenSeesPy import failed: {exc}") from exc
    return ops


if __name__ == "__main__":
    result = run_modal()
    print(json.dumps(result, ensure_ascii=False))
