"""比赛提交包的离线自检入口。"""

from __future__ import annotations

import json
import hashlib
import os
import re
import sys
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "momo_agent" / "backend"
RUNTIME_PARENT = ROOT / "bridge_models" / "stbridge_opensees"
EVIDENCE = ROOT / "evidence" / "opensees_optimization_20260802"
MANIFEST = ROOT / "SUBMISSION_MANIFEST.json"


def require(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(f"缺少提交文件：{path.relative_to(ROOT)}")


def verify_submission_manifest(root: Path, manifest_path: Path) -> dict[str, object]:
    """只校验清单声明的提交文件，不推断磁盘上的提交边界。"""
    if not manifest_path.is_file():
        raise FileNotFoundError(f"缺少提交清单：{manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    files = manifest.get("files") if isinstance(manifest, dict) else None
    if not isinstance(files, list):
        raise RuntimeError("提交清单 files 必须是数组")
    file_count = manifest.get("fileCount")
    if isinstance(file_count, bool) or not isinstance(file_count, int) or file_count != len(files):
        raise RuntimeError("提交清单 fileCount 与 files 条目数不一致")

    declared_paths = [
        item.get("path")
        for item in files
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    ]
    if len(declared_paths) != len({path.casefold() for path in declared_paths}):
        raise RuntimeError("提交清单包含重复路径")

    root_resolved = root.resolve()
    for item in files:
        if not isinstance(item, dict):
            raise RuntimeError("提交清单文件条目必须是对象")
        raw_path = item.get("path")
        if not isinstance(raw_path, str) or not raw_path or "\\" in raw_path:
            raise RuntimeError("提交清单包含不规范路径")
        relative_path = PurePosixPath(raw_path)
        if (
            relative_path.is_absolute()
            or raw_path != relative_path.as_posix()
            or raw_path == "."
            or ".." in relative_path.parts
            or re.match(r"^[A-Za-z]:", raw_path)
        ):
            raise RuntimeError(f"提交清单包含越界或不规范路径：{raw_path}")
        path = (root_resolved / Path(*relative_path.parts)).resolve()
        try:
            path.relative_to(root_resolved)
        except ValueError as exc:
            raise RuntimeError(f"提交清单路径越界：{raw_path}") from exc
        if not path.is_file():
            raise FileNotFoundError(f"提交清单声明的文件不存在：{raw_path}")
        expected_digest = item.get("sha256")
        if not isinstance(expected_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_digest):
            raise RuntimeError(f"提交清单 SHA-256 不合法：{raw_path}")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != expected_digest:
            raise RuntimeError(f"文件校验失败：{raw_path}")
    return manifest


def main() -> None:
    if sys.version_info[:2] != (3, 13) or sys.maxsize <= 2**32:
        raise RuntimeError("内置 USER300 OpenSeesPy 运行时要求 Python 3.13 x64")

    required = [
        BACKEND / "app" / "main.py",
        ROOT / "platform-ui" / "dist" / "index.html",
        ROOT / "pyansys_bridge" / "optimization" / "config_runner.py",
        ROOT / "bridge_models" / "stbridge_opensees" / "stbridge_opensees_modal_builder.py",
        ROOT / "analysis_data" / "earthquake_inputs" / "earthquake_acceleration_record.txt",
        ROOT / "analysis_data" / "wind_inputs" / "wind_vertical_8nodes_10mps_3600s.csv",
        ROOT / "docs" / "examples" / "templates" / "openseespy_inproc_run_joint_baseline_workflow_template.json",
        EVIDENCE / "optimization_summary.json",
        EVIDENCE / "final_review" / "summary.json",
    ]
    for path in required:
        require(path)

    manifest = verify_submission_manifest(ROOT, MANIFEST)

    sys.path[:0] = [str(ROOT), str(BACKEND), str(RUNTIME_PARENT)]
    os.environ["OPENSEESPY_USER300_RUNTIME"] = str(RUNTIME_PARENT / "openseespy")

    import fastapi  # noqa: F401
    import pyansys_bridge  # noqa: F401
    from openseespy import opensees as ops

    ops.wipe()
    ops.model("basic", "-ndm", 1, "-ndf", 1)
    ops.uniaxialMaterial("User300Viscous", 1, 1_000_000.0, 0.8, 1.0e-5)
    ops.wipe()

    optimization = json.loads((EVIDENCE / "optimization_summary.json").read_text(encoding="utf-8"))
    review = optimization["review_records"][0]
    result = review["analysis_results"][0]["objectives"]
    if not review["accepted"] or not review["verified_execution"]:
        raise RuntimeError("归档推荐点没有通过真实 FEM 复核")

    print("MOMO competition package: PASS")
    print("OpenSeesPy USER300 runtime: PASS")
    print(f"Submission manifest: PASS ({manifest['fileCount']} files)")
    print(f"DOE records: {optimization['doe_record_count']}")
    print("Final design: c=7600 per damper, alpha=0.8, c_total_per_tower=15200")
    print(f"Displacement: {result['max_girder_end_displacement']:.6f} m")
    print(f"Base shear: {result['max_tower_base_shear'] / 1e6:.6f} MN")
    print(f"Base moment: {result['max_tower_base_moment'] / 1e9:.6f} GN*m")


if __name__ == "__main__":
    main()
