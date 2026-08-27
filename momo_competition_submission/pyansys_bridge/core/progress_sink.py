"""Case 级求解进度的跨进程汇报通道。

DOE 并行使用 ``ProcessPoolExecutor``，各 case 运行在独立子进程中，彼此没有
共享内存，也不应该知道平台 SQLite 的位置。因此进度以"每个 case 一个 JSON
文件"的形式落盘：子进程只管写自己那一个文件，读取方扫描目录汇总。

写入使用先写临时文件再 ``os.replace`` 的原子替换，保证读取方永远看不到
半写状态，无需加锁。
"""

from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path
from typing import Any


PROGRESS_FILE_SUFFIX = ".json"
BATCH_SUMMARY_FILENAME = "_batch" + PROGRESS_FILE_SUFFIX
_BATCH_COMPONENT_PREFIX = "_batch."
ANSYS_PROBE_SUFFIX = ".ansys-probe.json"
_UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9._-]+")
_ANSYS_TIME_RE = re.compile(
    r"\bTIME\s*=\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[EeDd][+-]?\d+)?)",
    re.IGNORECASE,
)
_ANSYS_OUTPUT_TAIL_BYTES = 256 * 1024


def safe_progress_filename(case_id: str) -> str:
    """把 case_id 转成安全的文件名。

    case_id 含 ``c=7600``、``alpha=0.8`` 这类字符，不能直接做文件名。
    """

    normalized = _UNSAFE_FILENAME_CHARS.sub("_", str(case_id)).strip("_")
    return (normalized or "case") + PROGRESS_FILE_SUFFIX


def write_case_progress(
    progress_dir: str | Path,
    case_id: str,
    *,
    step: int,
    total_steps: int,
    phase: str = "transient",
) -> None:
    """原子写入单个 case 的进度快照。

    进度写入永远不能让求解失败，因此这里吞掉所有 IO 异常。
    """

    directory = Path(progress_dir)
    payload = {
        "caseId": str(case_id),
        "step": int(step),
        "totalSteps": int(total_steps),
        "percent": _percent(step, total_steps),
        "phase": str(phase),
    }
    _atomic_write_json(directory, safe_progress_filename(case_id), payload)


def write_batch_progress(
    progress_dir: str | Path,
    *,
    completed: int,
    total: int,
) -> None:
    """原子写入批量算例的完成计数（"完成 8/15"）。

    单个 case 的百分比来自求解子进程，而完成计数只有派发方知道，因此单独
    写一个 summary 文件，与 per-case 文件共用同一个目录和读取方。
    """

    _atomic_write_json(
        Path(progress_dir),
        BATCH_SUMMARY_FILENAME,
        {
            "completedCases": int(completed),
            "totalCases": int(total),
            "percent": _percent(completed, total),
        },
    )


def write_batch_component_progress(
    progress_dir: str | Path,
    *,
    component: str,
    completed: int,
    total: int,
) -> None:
    """原子写入并行阶段自己的完成计数。

    基线和 DOE 可以同时运行，不能让两个执行器覆盖同一个 ``_batch.json``。
    组件文件由读取方求和，因此各执行器只更新自己的计数。
    """

    normalized = _safe_batch_component(component)
    if normalized is None:
        return
    _atomic_write_json(
        Path(progress_dir),
        f"{_BATCH_COMPONENT_PREFIX}{normalized}{PROGRESS_FILE_SUFFIX}",
        {
            "component": normalized,
            "completedCases": int(completed),
            "totalCases": int(total),
            "percent": _percent(completed, total),
        },
    )


def register_ansys_output_probe(
    progress_dir: str | Path,
    case_id: str,
    *,
    output_path: str | Path,
    dt: object,
    duration: object,
) -> None:
    """登记只读 MAPDL 输出探针，不修改 APDL 命令流或求解结果。"""

    try:
        numeric_dt = float(dt)
        numeric_duration = float(duration)
    except (TypeError, ValueError):
        return
    if (
        not math.isfinite(numeric_dt)
        or not math.isfinite(numeric_duration)
        or numeric_dt <= 0.0
        or numeric_duration <= 0.0
    ):
        return
    total_steps = max(1, int(round(numeric_duration / numeric_dt)))
    filename = Path(safe_progress_filename(case_id)).stem + ANSYS_PROBE_SUFFIX
    _atomic_write_json(
        Path(progress_dir),
        filename,
        {
            "probeType": "ANSYS_OUTPUT_TIME",
            "caseId": str(case_id),
            "outputPath": str(Path(output_path).resolve()),
            "dt": numeric_dt,
            "duration": numeric_duration,
            "totalSteps": total_steps,
        },
    )


def refresh_ansys_output_probes(progress_dir: str | Path) -> None:
    """读取 ``ansys.out`` 尾部最新 TIME，并原子投影为通用 case 进度。"""

    directory = Path(progress_dir)
    if not directory.is_dir():
        return
    for path in directory.glob("*" + ANSYS_PROBE_SUFFIX):
        try:
            probe = json.loads(path.read_text(encoding="utf-8"))
            case_id = str(probe["caseId"])
            dt = float(probe["dt"])
            duration = float(probe["duration"])
            total_steps = int(probe["totalSteps"])
            latest_time = _latest_ansys_time(Path(str(probe["outputPath"])))
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
            continue
        if (
            latest_time is None
            or not math.isfinite(latest_time)
            or dt <= 0.0
            or duration <= 0.0
            or total_steps <= 0
        ):
            continue
        step = max(0, min(total_steps, int(round(latest_time / dt))))
        write_case_progress(
            directory,
            case_id,
            step=step,
            total_steps=total_steps,
            phase="transient",
        )


def read_batch_progress(progress_dir: str | Path) -> dict[str, Any] | None:
    """读取批量完成计数，缺失或损坏时返回 ``None``。

    存在组件计数时返回其聚合值；旧的单文件格式继续兼容。
    """

    directory = Path(progress_dir)
    components = _read_batch_components(directory)
    if components:
        completed = sum(item["completedCases"] for item in components)
        total = sum(item["totalCases"] for item in components)
        return {
            "completedCases": completed,
            "totalCases": total,
            "percent": _percent(completed, total),
            "components": [item["component"] for item in components],
        }
    path = directory / BATCH_SUMMARY_FILENAME
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def read_all_progress(progress_dir: str | Path) -> list[dict[str, Any]]:
    """扫描目录，返回全部 case 进度，按 caseId 排序。

    目录不存在、单个文件损坏都视为"暂无该项进度"，不抛异常。
    """

    directory = Path(progress_dir)
    if not directory.is_dir():
        return []

    entries: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*" + PROGRESS_FILE_SUFFIX)):
        if path.name == BATCH_SUMMARY_FILENAME or path.name.endswith(ANSYS_PROBE_SUFFIX):
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict) and payload.get("caseId"):
            entries.append(payload)
    entries.sort(key=lambda item: str(item.get("caseId", "")))
    return entries


def clear_progress(progress_dir: str | Path) -> None:
    """清空进度目录，供一轮新的批量求解开始前调用。"""

    directory = Path(progress_dir)
    if not directory.is_dir():
        return
    for path in directory.glob("*"):
        try:
            path.unlink()
        except OSError:
            continue


def _atomic_write_json(directory: Path, filename: str, payload: dict[str, Any]) -> None:
    """先写 ``.tmp`` 再 ``os.replace``，读取方永远看不到半写状态。

    进度写入永远不能让求解失败，因此这里吞掉所有 IO 异常。
    """

    target = directory / filename
    temporary = target.with_suffix(target.suffix + ".tmp")
    try:
        directory.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(temporary, target)
    except (OSError, TypeError, ValueError):
        # 进度是观测信号，不是求解结果；写不进去也不能中断计算。
        return


def _safe_batch_component(component: str) -> str | None:
    normalized = str(component).strip()
    if not normalized or not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", normalized):
        return None
    return normalized


def _read_batch_components(directory: Path) -> list[dict[str, Any]]:
    if not directory.is_dir():
        return []
    components: list[dict[str, Any]] = []
    for path in sorted(directory.glob(f"{_BATCH_COMPONENT_PREFIX}*{PROGRESS_FILE_SUFFIX}")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            component = _safe_batch_component(payload.get("component"))
            completed = int(payload["completedCases"])
            total = int(payload["totalCases"])
        except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError):
            continue
        if component is None or completed < 0 or total <= 0:
            continue
        components.append({
            "component": component,
            "completedCases": min(completed, total),
            "totalCases": total,
        })
    return components


def _latest_ansys_time(output_path: Path) -> float | None:
    """从 MAPDL 文本输出尾部提取最后一个数值 TIME，忽略回显表达式。"""

    try:
        with output_path.open("rb") as handle:
            size = handle.seek(0, os.SEEK_END)
            handle.seek(max(0, size - _ANSYS_OUTPUT_TAIL_BYTES), os.SEEK_SET)
            text = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return None
    matches = _ANSYS_TIME_RE.findall(text)
    if not matches:
        return None
    try:
        return float(matches[-1].replace("D", "E").replace("d", "e"))
    except ValueError:
        return None


def _percent(step: int, total_steps: int) -> int:
    if total_steps <= 0:
        return 0
    ratio = int(step) / int(total_steps)
    return max(0, min(100, int(round(ratio * 100))))
