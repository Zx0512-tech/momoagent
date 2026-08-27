"""STbridge OpenSeesPy model builder without import-time analysis side effects."""

from __future__ import annotations

import json
import math
from pathlib import Path
import sys
from typing import Any


MODEL_SCRIPT = Path(__file__).with_name("stbridge_opensees_modal_model.py")
GRAVITY_START_MARKER = "ops.constraints('Transformation')"
GRAVITY_STATE_MARKER = "ops.loadConst('-time', 0.0)"
SECTION_MARKERS = {
    "constraints": "ops.fix(",
    "materials": "ops.uniaxialMaterial(",
    "elements": "ops.geomTransf(",
}
DEFAULT_REFERENCE_FREQUENCIES_HZ = [
    0.05530750551942655,
    0.10040736400449973,
    0.18571922362932938,
    0.22587850675860627,
    0.2985259681015457,
    0.3229275392524449,
    0.37473462678490876,
    0.38708968945802447,
    0.39316339469550815,
    0.4221885435709374,
]
STANDARD_MODEL_STAGE_SEQUENCE = (
    "nodes",
    "materials",
    "elements",
    "constraints",
    "loads",
)


def standard_model_stage_entrypoints() -> dict[str, str]:
    """Return the standard STbridge OpenSeesPy model stage entrypoint names."""

    return {
        "nodes": "build_nodes",
        "materials": "build_materials",
        "elements": "build_elements",
        "constraints": "build_constraints",
        "loads": "apply_gravity_loads",
    }


def build_model() -> dict[str, Any]:
    """Build the STbridge OpenSees model and return the execution namespace."""

    namespace = build_structure()
    return apply_gravity(namespace)


def build_structure() -> dict[str, Any]:
    """Build nodes, materials, elements, masses, and boundary constraints."""

    ops = _require_ops()
    namespace = _model_namespace()
    build_nodes(namespace)
    build_materials(namespace)
    build_elements(namespace)
    build_constraints(namespace)
    if not ops.getNodeTags():
        raise RuntimeError("STbridge OpenSees builder produced no nodes")
    namespace["gravity_preloaded"] = False
    return namespace


def build_nodes(namespace: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build STbridge model nodes."""

    namespace = _ensure_namespace(namespace)
    exec(compile(_node_source(), str(MODEL_SCRIPT), "exec"), namespace)
    return namespace


def build_constraints(namespace: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build fixed supports and equal-DOF constraints."""

    namespace = _ensure_namespace(namespace)
    exec(compile(_constraint_source(), str(MODEL_SCRIPT), "exec"), namespace)
    return namespace


def build_materials(namespace: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build uniaxial materials used by cables and truss members."""

    namespace = _ensure_namespace(namespace)
    exec(compile(_material_source(), str(MODEL_SCRIPT), "exec"), namespace)
    return namespace


def build_elements(namespace: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build beam, truss, and cable elements."""

    namespace = _ensure_namespace(namespace)
    exec(compile(_element_source(), str(MODEL_SCRIPT), "exec"), namespace)
    return namespace


def apply_gravity(namespace: dict[str, Any] | None = None) -> dict[str, Any]:
    """Apply and freeze the gravity state for modal and transient analyses."""

    ops = _require_ops()
    namespace = _ensure_namespace(namespace)
    if namespace.get("gravity_preloaded"):
        return namespace
    if not ops.getNodeTags():
        raise RuntimeError("STbridge gravity preload requires build_structure() first")
    apply_gravity_loads(namespace)
    return commit_gravity_state(namespace)


def apply_gravity_loads(namespace: dict[str, Any] | None = None) -> dict[str, Any]:
    """Apply nodal gravity loads without committing the gravity state."""

    namespace = _ensure_namespace(namespace)
    exec(compile(_gravity_load_source(), str(MODEL_SCRIPT), "exec"), namespace)
    namespace["gravity_loads_applied"] = True
    return namespace


def commit_gravity_state(namespace: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run static preload analysis and freeze the gravity state."""

    namespace = _ensure_namespace(namespace)
    exec(compile(_gravity_analysis_source(), str(MODEL_SCRIPT), "exec"), namespace)
    namespace["gravity_preloaded"] = True
    return namespace


def build_command_stream_model() -> dict[str, Any]:
    """Command-stream alias used by SolverFactory OpenSees backends."""

    return build_model()


def run_modal(
    num_modes: int = 20,
    reference_frequencies_hz: list[float] | None = None,
) -> dict[str, Any]:
    """Run modal analysis after building the model when needed."""

    ops = _require_ops()
    if not ops.getNodeTags():
        build_model()
    try:
        eigen_values = list(ops.eigen(int(num_modes)))
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
        "eigenvalues": eigen_values,
        "frequencies": frequencies,
        "reference": references,
        "errors_pct": errors_pct,
        "all_within_5pct": bool(errors_pct) and all(error <= 5.0 for error in errors_pct[:10]),
    }


def _gravity_model_source() -> str:
    return _structure_model_source() + _gravity_state_source()


def _structure_model_source() -> str:
    return _node_source() + _constraint_source() + _material_source() + _element_source()


def _gravity_state_source() -> str:
    return _gravity_load_source() + _gravity_analysis_source()


def _node_source() -> str:
    return _section_source(None, SECTION_MARKERS["constraints"])


def _constraint_source() -> str:
    return _section_source(SECTION_MARKERS["constraints"], SECTION_MARKERS["materials"])


def _material_source() -> str:
    return _section_source(SECTION_MARKERS["materials"], SECTION_MARKERS["elements"])


def _element_source() -> str:
    return _section_source(SECTION_MARKERS["elements"], GRAVITY_START_MARKER)


def _gravity_load_source() -> str:
    return _section_source(GRAVITY_START_MARKER, "ops.test(")


def _gravity_analysis_source() -> str:
    _prelude, body = _model_source_parts()
    lines: list[str] = []
    saw_gravity_state = False
    for body_line in body:
        if not lines:
            if not body_line.strip().startswith("ops.test("):
                continue
        normalized = _normalize_model_body_line(body_line)
        if normalized is not None:
            lines.append(normalized)
        if body_line.strip() == GRAVITY_STATE_MARKER:
            saw_gravity_state = True
            lines.append("gravity_preloaded = True")
            break
    if not saw_gravity_state:
        raise RuntimeError(f"Gravity state marker not found in {MODEL_SCRIPT}")
    return "\n".join(lines) + "\n"


def _section_source(start_marker: str | None, end_marker: str) -> str:
    prelude, body = _model_source_parts()
    lines: list[str] = prelude.copy() if start_marker is None else []
    in_section = start_marker is None
    saw_start = start_marker is None
    saw_end = False
    for body_line in body:
        stripped = body_line.strip()
        if not in_section:
            if _matches_marker(stripped, start_marker):
                in_section = True
                saw_start = True
            else:
                continue
        if in_section and _matches_marker(stripped, end_marker):
            saw_end = True
            break
        normalized = _normalize_model_body_line(body_line)
        if normalized is not None:
            lines.append(normalized)
    if not saw_start:
        raise RuntimeError(f"Section start marker not found in {MODEL_SCRIPT}: {start_marker}")
    if not saw_end:
        raise RuntimeError(f"Section end marker not found in {MODEL_SCRIPT}: {end_marker}")
    return "\n".join(lines) + "\n"


def _matches_marker(line: str, marker: str | None) -> bool:
    if marker is None:
        return False
    if marker.endswith("("):
        return line.startswith(marker)
    return line == marker


def _model_source_parts() -> tuple[list[str], list[str]]:
    text = MODEL_SCRIPT.read_text(encoding="utf-8")
    prelude: list[str] = []
    body: list[str] = []
    in_main = False
    for line in text.splitlines():
        if line.startswith("def main()"):
            in_main = True
            continue
        if not in_main:
            # builder 已注入 ops，避免读取模型时再次触发 OpenSeesPy 平台探测差异
            if line.strip() == "import openseespy.opensees as ops":
                continue
            prelude.append(line)
            continue
        if not line.startswith("    "):
            continue
        body.append(line[4:])
    return prelude, body


def _normalize_model_body_line(body_line: str) -> str | None:
    if body_line.startswith("print('gravity_static_status="):
        return None
    if body_line.strip() in {"ops.system('BandGeneral')", 'ops.system("BandGeneral")'}:
        return "ops.system(opensees_system, *opensees_system_args)"
    if body_line == "static_ok = ops.analyze(10)":
        return "\n".join(
            [
                body_line,
                "if static_ok != 0:",
                "    raise RuntimeError('STbridge gravity static preload failed')",
            ]
        )
    return body_line


def _model_namespace() -> dict[str, Any]:
    ops = _require_ops()
    return {
        "ops": ops,
        "json": json,
        "math": math,
        "sys": sys,
        "opensees_system": globals().get("opensees_system", "UmfPack"),
        "opensees_system_args": tuple(globals().get("opensees_system_args", ())),
        "__file__": str(MODEL_SCRIPT),
        "__name__": "__stbridge_opensees_model_builder__",
    }


def _ensure_namespace(namespace: dict[str, Any] | None = None) -> dict[str, Any]:
    ops = _require_ops()
    if namespace is None:
        return _model_namespace()
    namespace.setdefault("ops", ops)
    namespace.setdefault("json", json)
    namespace.setdefault("math", math)
    namespace.setdefault("sys", sys)
    namespace.setdefault("opensees_system", globals().get("opensees_system", "UmfPack"))
    namespace.setdefault("opensees_system_args", tuple(globals().get("opensees_system_args", ())))
    namespace.setdefault("__file__", str(MODEL_SCRIPT))
    namespace.setdefault("__name__", "__stbridge_opensees_model_builder__")
    return namespace


def _frequencies_from_eigenvalues(eigen_values: list[float]) -> list[float]:
    return [math.sqrt(value) / (2.0 * math.pi) for value in eigen_values if value > 1.0e-10]


def _require_ops():
    try:
        import openseespy.opensees as ops
    except Exception as exc:
        raise RuntimeError(f"OpenSeesPy import failed: {exc}") from exc
    return ops


if __name__ == "__main__":
    result = run_modal()
    print(json.dumps(result, ensure_ascii=False))
