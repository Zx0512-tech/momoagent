# ANSYS USER300 运行时

`eddy_current_userelem.f90` 实现桥梁非线性黏滞阻尼器使用的 `USER300` 用户单元。

在安装 Intel oneAPI（含 `ifx`）和 Visual Studio 2022 后，于仓库根目录执行：

```powershell
.\.venv\Scripts\python.exe tools\scripts\build_ansys_user300.py
```

脚本会生成被 Git 忽略的 `ansys\build_userelem\UserElemLib.dll`。如需使用其他目录，在本机 `.env` 中配置：

```dotenv
MOMO_ANSYS_USER_ELEMENT_PATH=D:\path\to\build_userelem
```

求解器会在调用 MAPDL 前验证 DLL 是否存在，并为 ANSYS 2024 R2 设置 `ANS_USER_PATH` 与 `ANS_USER_PATH_242`。
