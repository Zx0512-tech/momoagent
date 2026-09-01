from __future__ import annotations

import json
import os
import shutil
from importlib import metadata
from importlib.util import find_spec
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.services.platform_dispatcher import PlatformJobDispatcher
from app.services.platform_store import PlatformStore, platform_execution_mode, utc_now


REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_PLATFORM_UI_DIST = REPO_ROOT / "platform-ui" / "dist"
TEMPLATE_ROOT = REPO_ROOT / "docs" / "examples" / "templates"
DEFAULT_ANSYS_CONFIG = TEMPLATE_ROOT / "ansys_run_earthquake_baseline_template.json"
# MAPDL 路径按已登记的 ANSYS 模板逐个探测，不只读地震模板：风工况的 ANSYS
# 就绪性此前是从地震模板推断出来的，地震模板一旦改名或移走，风工况会跟着
# 误判 NOT_READY。两个模板当前声明同一个可执行文件，所以这里只是解耦，
# 不改变本机结论。
ANSYS_READINESS_CONFIGS = (
    DEFAULT_ANSYS_CONFIG,
    TEMPLATE_ROOT / "ansys_run_wind_baseline_template.json",
)
DEFAULT_MIN_FREE_BYTES = 1024 * 1024 * 1024


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _component(status: str, *, required: bool, detail: str) -> dict[str, Any]:
    return {"status": status, "required": required, "detail": detail}


def _configured_ansys_executable() -> Path | None:
    """取第一个已登记 ANSYS 模板声明的 MAPDL 路径。

    按 ANSYS_READINESS_CONFIGS 顺序探测：任一已登记模板给出可执行文件即可，
    避免单一模板（此前是地震模板）成为所有工况就绪性的隐式来源。
    """
    for config_path in ANSYS_READINESS_CONFIGS:
        try:
            payload = json.loads(config_path.read_text(encoding="utf-8"))
            value = payload.get("solver_kwargs", {}).get("mapdl_executable")
        except (OSError, ValueError):
            continue
        if value:
            return Path(value).resolve()
    return None


def _openseespy_runtime_status() -> tuple[bool, str]:
    """检查 OpenSeesPy 进程内求解运行时是否可用。

    必须走求解器自己的解析路径（`_import_openseespy` 会先把内置 USER300 运行时
    插到 sys.path 最前面），不能只做 `find_spec("openseespy")`：裸探测会命中
    site-packages 里的普通 openseespy，而求解器加载的是内置运行时。worktree 只
    拷了源码没拷 `opensees*.pyd` 时，裸探测报可用、求解阶段才炸
    `No module named openseespy.opensees`。
    """
    try:
        from pyansys_bridge.core.openseespy_inproc_solver import _import_openseespy
    except Exception as exc:  # noqa: BLE001 - 探测不得让就绪检查本身抛出
        return False, f"openseespy in-process solver bridge unavailable: {exc}"
    try:
        version = str(_import_openseespy().version()).strip()
    except Exception as exc:  # noqa: BLE001 - RuntimeError/OSError/ImportError 都要归到 FAIL
        return False, f"openseespy in-process runtime unavailable: {exc}"
    return True, f"openseespy in-process runtime {version or 'available'}"


def _ansys_result_sdk_status() -> tuple[bool, str]:
    """检查 ANSYS 结果读取 SDK 是否可用。

    MAPDL.exe 只负责算出 .rst，后处理要靠 ansys-dpf-core 读它。少了这个包，
    作业会先跑完整段瞬态（分钟级）再在后处理阶段失败，所以放进就绪检查提前拦住。
    用 find_spec + 包元数据而不是真正 import：import 要约 0.8s，而这个函数在
    每次预检和 /readiness 上都会调用。
    """
    try:
        found = find_spec("ansys.dpf.core") is not None
    except (ImportError, ValueError):
        found = False
    if not found:
        return False, "ansys-dpf-core missing; MAPDL .rst results cannot be read"
    try:
        version = metadata.version("ansys-dpf-core")
    except metadata.PackageNotFoundError:
        return False, "ansys.dpf.core importable but ansys-dpf-core package metadata missing"
    return True, f"ansys-dpf-core {version} available"


def build_readiness_report(
    store: PlatformStore,
    dispatcher: PlatformJobDispatcher,
    *,
    platform_ui_dist: Path = DEFAULT_PLATFORM_UI_DIST,
    ansys_executable: Path | None = None,
    require_platform_ui: bool | None = None,
    require_ansys: bool | None = None,
    require_openseespy: bool | None = None,
    min_free_bytes: int = DEFAULT_MIN_FREE_BYTES,
) -> dict[str, Any]:
    require_platform_ui = _env_flag("MOMO_REQUIRE_PLATFORM_UI") if require_platform_ui is None else require_platform_ui
    require_ansys = _env_flag("MOMO_REQUIRE_ANSYS") if require_ansys is None else require_ansys
    require_openseespy = (
        _env_flag("MOMO_REQUIRE_OPENSEESPY") if require_openseespy is None else require_openseespy
    )
    components: dict[str, dict[str, Any]] = {}

    platform_mode = platform_execution_mode()
    live_mode = platform_mode == 'LIVE'
    components['platform_mode'] = _component(
        'PASS' if live_mode else 'FAIL',
        required=require_platform_ui,
        detail=(
            'MOMO_PLATFORM_MODE=LIVE'
            if live_mode
            else f'MOMO_PLATFORM_MODE={platform_mode}; production UI requires LIVE'
        ),
    )

    try:
        store.repository.check_read_write()
        components["database"] = _component("PASS", required=True, detail="SQLite read/write check passed")
    except Exception as exc:
        components["database"] = _component("FAIL", required=True, detail=f"SQLite check failed: {exc}")

    artifact_root = store.repository.artifact_root
    probe_path = artifact_root / f".readiness-{uuid4().hex}.tmp"
    try:
        artifact_root.mkdir(parents=True, exist_ok=True)
        probe_path.write_bytes(b"momo-readiness")
        if probe_path.read_bytes() != b"momo-readiness":
            raise OSError("artifact probe content mismatch")
        components["artifact_storage"] = _component("PASS", required=True, detail="Artifact storage is writable")
    except Exception as exc:
        components["artifact_storage"] = _component("FAIL", required=True, detail=f"Artifact storage check failed: {exc}")
    finally:
        probe_path.unlink(missing_ok=True)

    try:
        free_bytes = shutil.disk_usage(store.state_path.parent).free
        disk_status = "PASS" if free_bytes >= min_free_bytes else "FAIL"
        components["disk_space"] = _component(
            disk_status,
            required=True,
            detail=f"free_bytes={free_bytes}; required_bytes={min_free_bytes}",
        )
    except OSError as exc:
        components["disk_space"] = _component("FAIL", required=True, detail=f"Disk check failed: {exc}")

    components["dispatcher"] = _component(
        "PASS" if dispatcher.is_running else "FAIL",
        required=True,
        detail="Dispatcher thread is running" if dispatcher.is_running else "Dispatcher thread is not running",
    )

    ui_ready = (platform_ui_dist / "index.html").is_file()
    components["platform_ui"] = _component(
        "PASS" if ui_ready else "FAIL",
        required=require_platform_ui,
        detail="Production UI bundle found" if ui_ready else "Production UI bundle missing",
    )

    ansys_executable = ansys_executable or _configured_ansys_executable()
    ansys_ready = bool(ansys_executable and ansys_executable.is_file())
    components["ansys_executable"] = _component(
        "PASS" if ansys_ready else "FAIL",
        required=require_ansys,
        detail="MAPDL executable found" if ansys_ready else "MAPDL executable missing",
    )

    sdk_ready, sdk_detail = _ansys_result_sdk_status()
    components["ansys_result_sdk"] = _component(
        "PASS" if sdk_ready else "FAIL",
        required=require_ansys,
        detail=sdk_detail,
    )

    # 只在调用方声明用 OpenSeesPy 时才必需：ANSYS 路径不进程内求解，缺它不该拦。
    # 此前没有任何 OpenSeesPy 就绪检查，而风工况批量与优化都会路由给它，
    # 运行时缺 opensees*.pyd 只能在求解阶段才暴露。
    openseespy_ready, openseespy_detail = _openseespy_runtime_status()
    components["openseespy_runtime"] = _component(
        "PASS" if openseespy_ready else "FAIL",
        required=require_openseespy,
        detail=openseespy_detail,
    )

    llm_configured = bool(
        os.environ.get('MOMO_LLM_BASE_URL', '').strip()
        and os.environ.get('MOMO_LLM_MODEL', '').strip()
    )
    components["llmConfigured"] = _component(
        "PASS" if llm_configured else "FAIL",
        required=False,
        detail="LLM routing and intent configuration found" if llm_configured else "MOMO_LLM_BASE_URL / MOMO_LLM_MODEL missing",
    )

    blocking = [
        name
        for name, component in components.items()
        if component["required"] and component["status"] != "PASS"
    ]
    return {
        "status": "NOT_READY" if blocking else "READY",
        "now": utc_now(),
        "blockingComponents": blocking,
        "components": components,
    }
