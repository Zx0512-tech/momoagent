"""使用本机 Intel oneAPI 编译 ANSYS USER300 动态库。"""

from __future__ import annotations

import argparse
import hashlib
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE = ROOT / "ansys" / "user300" / "eddy_current_userelem.f90"
DEFAULT_BUILD_DIR = ROOT / "ansys" / "build_userelem"
DEFAULT_SETVARS = Path(r"C:\Program Files (x86)\Intel\oneAPI\setvars.bat")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Build ANSYS USER300 UserElemLib.dll.")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--build-dir", type=Path, default=DEFAULT_BUILD_DIR)
    parser.add_argument("--setvars", type=Path, default=DEFAULT_SETVARS)
    args = parser.parse_args()

    source = args.source.resolve()
    build_dir = args.build_dir.resolve()
    setvars = args.setvars.resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    if not setvars.is_file():
        raise FileNotFoundError(setvars)

    build_dir.mkdir(parents=True, exist_ok=True)
    dll_path = build_dir / "UserElemLib.dll"
    command = (
        f'call "{setvars}" intel64 vs2022 >nul 2>&1 && '
        f'ifx /dll /exe:"{dll_path}" "{source}"'
    )
    subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            f"& cmd.exe /d /c '{command}'",
        ],
        check=True,
        cwd=build_dir,
    )
    if not dll_path.is_file():
        raise FileNotFoundError(dll_path)

    print(f"USER300 source SHA256: {_sha256(source)}")
    print(f"USER300 DLL: {dll_path}")
    print(f"USER300 DLL SHA256: {_sha256(dll_path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
