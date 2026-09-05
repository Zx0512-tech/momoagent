from pathlib import Path

import pytest

from pyansys_bridge.core.ansys_solver import AnsysSolver


def test_user300_execution_env_uses_configured_library_directory(tmp_path: Path) -> None:
    user_element_dir = tmp_path / "user300"
    user_element_dir.mkdir()
    (user_element_dir / "UserElemLib.dll").write_bytes(b"test")
    solver = AnsysSolver(
        damper_module="damper_user300_viscous",
        user_element_path=user_element_dir,
    )

    env = solver._execution_env(tmp_path / "case")

    assert env["ANS_USER_PATH"] == str(user_element_dir.resolve())
    assert env["ANS_USER_PATH_242"] == str(user_element_dir.resolve())


def test_user300_execution_env_rejects_directory_without_library(tmp_path: Path) -> None:
    solver = AnsysSolver(
        damper_module="damper_user300_viscous",
        user_element_path=tmp_path / "missing-user300",
    )

    with pytest.raises(RuntimeError, match="UserElemLib.dll"):
        solver._execution_env(tmp_path / "case")


def test_user300_execution_env_reads_local_environment_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_element_dir = tmp_path / "configured-user300"
    user_element_dir.mkdir()
    (user_element_dir / "UserElemLib.dll").write_bytes(b"test")
    monkeypatch.setenv("MOMO_ANSYS_USER_ELEMENT_PATH", str(user_element_dir))
    solver = AnsysSolver(damper_module="damper_user300_viscous")

    env = solver._execution_env(tmp_path / "case")

    assert env["ANS_USER_PATH"] == str(user_element_dir.resolve())
    assert env["ANS_USER_PATH_242"] == str(user_element_dir.resolve())
