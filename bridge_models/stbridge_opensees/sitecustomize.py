from __future__ import annotations

import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parents[1]
_DLL_DIRECTORY_HANDLES = []


def _add_dll_directory(path: Path) -> None:
    if not path.exists():
        return
    # Windows 导入 opensees.pyd 前需要把 MKL、Fortran、Conan 依赖加入 DLL 搜索路径。
    if hasattr(os, "add_dll_directory"):
        _DLL_DIRECTORY_HANDLES.append(os.add_dll_directory(str(path)))
    os.environ["PATH"] = str(path) + os.pathsep + os.environ.get("PATH", "")


for runtime_path in (
    Path(sys.executable).resolve().parent,
    ROOT / "openseespy",
    REPO_ROOT / "third_party" / "OpenSees" / "build-msvc" / "build" / "Release",
    Path("C:/Program Files (x86)/Intel/oneAPI/mkl/2025.3/bin"),
    Path("C:/Program Files (x86)/Intel/oneAPI/compiler/2025.3/bin"),
    Path.home() / ".conan2" / "p" / "hdf57c9c4f78b57d4" / "p" / "bin",
    Path.home() / ".conan2" / "p" / "zlibfaa27933c9bf2" / "p" / "bin",
    Path.home() / ".conan2" / "p" / "tcl636abb7a009a3" / "p" / "bin",
):
    _add_dll_directory(runtime_path)

os.environ.setdefault("OPENSEESPY_USER300_RUNTIME", str(ROOT / "openseespy"))
