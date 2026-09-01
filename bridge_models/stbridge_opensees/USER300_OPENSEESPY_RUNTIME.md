# USER300 OpenSeesPy Runtime

此文件夹已内置带 USER300 C++ 材料的 OpenSeesPy 运行包。

运行验证：

```powershell
chcp 65001
cd /d <提交目录>\bridge_models\stbridge_opensees
python verify_user300_openseespy_runtime.py
```

后续分析脚本只要从本文件夹启动，就会优先导入本地 `openseespy` 包；该包会调用 `sitecustomize.py` 补齐 DLL 搜索路径：

```python
import openseespy.opensees as ops
```

当前模块为 Python 3.13 ABI：`opensees.cp313-win_amd64.pyd`。
