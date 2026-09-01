import codecs
import json
import logging
import os
import shutil
import subprocess
from importlib import metadata
from importlib.util import find_spec
from pathlib import Path

from app.core.logging_config import JsonFormatter
from app.services import platform_readiness
from app.services.platform_dispatcher import PlatformJobDispatcher
from app.services.platform_readiness import build_readiness_report
from app.services.platform_store import PlatformStore


def _stub_result_sdk(monkeypatch, *, available: bool) -> None:
    """固定 ANSYS 结果 SDK 探测结果，避免断言依赖本机是否装了 ansys-dpf-core。"""
    monkeypatch.setattr(
        platform_readiness,
        "_ansys_result_sdk_status",
        lambda: (available, "stubbed") if available else (False, "stubbed missing"),
    )


def test_readiness_requires_database_storage_disk_and_dispatcher(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv('MOMO_PLATFORM_MODE', 'LIVE')
    _stub_result_sdk(monkeypatch, available=True)
    store = PlatformStore(state_path=tmp_path / "state" / "platform.sqlite3")
    original_state = store.repository.load()
    dispatcher = PlatformJobDispatcher(store)
    ui_dist = tmp_path / "ui"
    ui_dist.mkdir()
    (ui_dist / "index.html").write_text("<html></html>", encoding="utf-8")
    ansys_executable = tmp_path / "MAPDL.exe"
    ansys_executable.write_bytes(b"binary")

    dispatcher.start()
    try:
        report = build_readiness_report(
            store,
            dispatcher,
            platform_ui_dist=ui_dist,
            ansys_executable=ansys_executable,
            require_platform_ui=True,
            require_ansys=True,
            min_free_bytes=1,
        )
    finally:
        dispatcher.stop()

    assert report["status"] == "READY"
    assert report["components"]["database"]["status"] == "PASS"
    assert report["components"]["artifact_storage"]["status"] == "PASS"
    assert report["components"]["disk_space"]["status"] == "PASS"
    assert report["components"]["dispatcher"]["status"] == "PASS"
    assert report["components"]["platform_mode"]["status"] == "PASS"
    assert report["components"]["platform_ui"]["status"] == "PASS"
    assert report["components"]["ansys_executable"]["status"] == "PASS"
    assert report["components"]["ansys_result_sdk"]["status"] == "PASS"
    assert report["components"]["llmConfigured"]["required"] is False
    assert report["components"]["llmConfigured"]["status"] in {"PASS", "FAIL"}
    assert store.repository.load() == original_state


def test_readiness_fails_when_required_database_check_fails(
    monkeypatch,
    tmp_path: Path,
) -> None:
    store = PlatformStore(state_path=tmp_path / "state" / "platform.sqlite3")
    dispatcher = PlatformJobDispatcher(store)
    monkeypatch.setattr(
        store.repository,
        "check_read_write",
        lambda: (_ for _ in ()).throw(OSError("database unavailable")),
    )

    report = build_readiness_report(
        store,
        dispatcher,
        platform_ui_dist=tmp_path / "missing-ui",
        require_platform_ui=False,
        require_ansys=False,
        min_free_bytes=1,
    )

    assert report["status"] == "NOT_READY"
    assert report["components"]["database"]["status"] == "FAIL"
    assert report["components"]["platform_ui"]["required"] is False


def test_required_ui_and_ansys_components_block_readiness(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv('MOMO_PLATFORM_MODE', 'LIVE')
    _stub_result_sdk(monkeypatch, available=True)
    store = PlatformStore(state_path=tmp_path / "state" / "platform.sqlite3")
    dispatcher = PlatformJobDispatcher(store)
    dispatcher.start()
    try:
        report = build_readiness_report(
            store,
            dispatcher,
            platform_ui_dist=tmp_path / "missing-ui",
            ansys_executable=tmp_path / "missing-MAPDL.exe",
            require_platform_ui=True,
            require_ansys=True,
            min_free_bytes=1,
        )
    finally:
        dispatcher.stop()

    assert report["status"] == "NOT_READY"
    assert report["blockingComponents"] == ["platform_ui", "ansys_executable"]


def test_missing_ansys_result_sdk_blocks_readiness(monkeypatch, tmp_path: Path) -> None:
    """缺 ansys-dpf-core 时求解能跑完但读不出 .rst，必须在就绪阶段拦住。"""
    monkeypatch.setenv('MOMO_PLATFORM_MODE', 'LIVE')
    _stub_result_sdk(monkeypatch, available=False)
    store = PlatformStore(state_path=tmp_path / "state" / "platform.sqlite3")
    dispatcher = PlatformJobDispatcher(store)
    ansys_executable = tmp_path / "MAPDL.exe"
    ansys_executable.write_bytes(b"binary")

    dispatcher.start()
    try:
        report = build_readiness_report(
            store,
            dispatcher,
            platform_ui_dist=tmp_path / "missing-ui",
            ansys_executable=ansys_executable,
            require_platform_ui=False,
            require_ansys=True,
            min_free_bytes=1,
        )
    finally:
        dispatcher.stop()

    assert report["status"] == "NOT_READY"
    assert report["blockingComponents"] == ["ansys_result_sdk"]
    assert report["components"]["ansys_executable"]["status"] == "PASS"
    assert report["components"]["ansys_result_sdk"]["required"] is True


def test_ansys_result_sdk_not_required_for_openseespy_readiness(monkeypatch, tmp_path: Path) -> None:
    """OpenSeesPy 在进程内求解，不读 .rst，缺 SDK 不该拦住它。"""
    monkeypatch.setenv('MOMO_PLATFORM_MODE', 'LIVE')
    _stub_result_sdk(monkeypatch, available=False)
    store = PlatformStore(state_path=tmp_path / "state" / "platform.sqlite3")
    dispatcher = PlatformJobDispatcher(store)

    dispatcher.start()
    try:
        report = build_readiness_report(
            store,
            dispatcher,
            platform_ui_dist=tmp_path / "missing-ui",
            require_platform_ui=False,
            require_ansys=False,
            min_free_bytes=1,
        )
    finally:
        dispatcher.stop()

    assert report["status"] == "READY"
    assert report["components"]["ansys_result_sdk"]["status"] == "FAIL"
    assert report["components"]["ansys_result_sdk"]["required"] is False


def test_ansys_result_sdk_probe_matches_installed_state() -> None:
    """探测结果必须与本机真实安装状态一致，不能钉死成 True（否则未装包的环境会假失败）。"""
    try:
        metadata.version("ansys-dpf-core")
        installed = find_spec("ansys.dpf.core") is not None
    except (metadata.PackageNotFoundError, ImportError, ValueError):
        installed = False

    available, detail = platform_readiness._ansys_result_sdk_status()

    assert available is installed
    assert "ansys-dpf-core" in detail


def test_mock_mode_blocks_production_ui_readiness(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv('MOMO_PLATFORM_MODE', 'MOCK')
    store = PlatformStore(state_path=tmp_path / "state" / "platform.sqlite3")
    dispatcher = PlatformJobDispatcher(store)
    ui_dist = tmp_path / "ui"
    ui_dist.mkdir()
    (ui_dist / "index.html").write_text("<html></html>", encoding="utf-8")

    dispatcher.start()
    try:
        report = build_readiness_report(
            store,
            dispatcher,
            platform_ui_dist=ui_dist,
            require_platform_ui=True,
            require_ansys=False,
            min_free_bytes=1,
        )
    finally:
        dispatcher.stop()

    assert report["status"] == "NOT_READY"
    assert report["components"]["platform_mode"] == {
        "status": "FAIL",
        "required": True,
        "detail": "MOMO_PLATFORM_MODE=MOCK; production UI requires LIVE",
    }
    assert "platform_mode" in report["blockingComponents"]


def test_production_start_loads_runtime_switches_and_defaults_live() -> None:
    source = (Path(__file__).resolve().parents[3] / "start.ps1").read_text(encoding="utf-8")

    assert "'MOMO_PLATFORM_MODE'" in source
    assert "'MOMO_AGENT_PERSISTENT_LOOP'" in source
    assert "$env:MOMO_PLATFORM_MODE = 'LIVE'" in source
    assert "Write-Warning" in source


def test_windows_launchers_parse_in_legacy_shells() -> None:
    submission_root = Path(__file__).resolve().parents[3]
    batch_path = submission_root / "momo.cmd"
    powershell_path = submission_root / "start.ps1"
    attributes_path = submission_root / ".gitattributes"

    batch_bytes = batch_path.read_bytes()
    assert batch_bytes.isascii(), "cmd.exe 启动器必须仅包含 ASCII 字节"
    assert b"\n" not in batch_bytes.replace(b"\r\n", b""), "cmd.exe 启动器必须使用 CRLF"
    assert powershell_path.read_bytes().startswith(codecs.BOM_UTF8), (
        "Windows PowerShell 5.1 需要 UTF-8 BOM 才能稳定解析中文"
    )
    attributes = attributes_path.read_text(encoding="utf-8")
    assert "/momo.cmd text eol=crlf" in attributes
    assert "/start.ps1 text eol=crlf" in attributes

    legacy_powershell = shutil.which("powershell.exe")
    if legacy_powershell is None:
        return
    parser_script = (
        "$tokens = $null; $errors = $null; "
        "[System.Management.Automation.Language.Parser]::ParseFile("
        "$env:MOMO_TEST_START_PS1, [ref]$tokens, [ref]$errors) | Out-Null; "
        "if ($errors.Count -gt 0) { $errors | ForEach-Object { $_.Message }; exit 1 }"
    )
    completed = subprocess.run(
        [legacy_powershell, "-NoProfile", "-Command", parser_script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        env={**os.environ, "MOMO_TEST_START_PS1": str(powershell_path)},
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_json_formatter_emits_machine_readable_operational_fields() -> None:
    record = logging.LogRecord(
        name="momo.api",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="request completed",
        args=(),
        exc_info=None,
    )
    record.event = "http_request_completed"
    record.request_id = "request-123"
    record.status_code = 200
    record.worker_pid = 101
    record.executor_pid = 202
    record.child_pids = [202, 303]
    record.termination_reason = "HEARTBEAT_TIMEOUT"
    record.exit_code = 1

    payload = json.loads(JsonFormatter().format(record))

    assert payload["level"] == "INFO"
    assert payload["logger"] == "momo.api"
    assert payload["event"] == "http_request_completed"
    assert payload["request_id"] == "request-123"
    assert payload["status_code"] == 200
    assert payload["worker_pid"] == 101
    assert payload["executor_pid"] == 202
    assert payload["child_pids"] == [202, 303]
    assert payload["termination_reason"] == "HEARTBEAT_TIMEOUT"
    assert payload["exit_code"] == 1
