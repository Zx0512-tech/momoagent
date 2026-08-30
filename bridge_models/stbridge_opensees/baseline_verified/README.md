# STbridge OpenSees Verified Baseline

这个目录保存当前建议继续使用的 STbridge OpenSees 基准模型。

## 文件

- `stbridge_baseline_openseespy_gravity_modal.py`
  - 推荐主模型文件。
  - 可导入的 OpenSeesPy builder 模块，提供 `build_model()`、`apply_gravity_loads()`、`build_gravity_model()`、`run_modal()` 等入口。
  - 保留原始对齐命令作为内部基准源，但导入模块时不会建模或运行分析。
- `stbridge_baseline_modal_plain.tcl`
  - 同一基准建模假设下的 OpenSees Tcl 模态模型。
- `mode_match_report.json`
  - ANSYS 与 OpenSees 模态频率/振型 MAC 对比明细。
- `validation_summary.json`
  - 当前基准模型的简要验证结论。

## 推荐运行方式

OpenSeesPy 运行时使用 Python 3.12 embedded 环境：

```powershell
<提交目录>\.venv\Scripts\python.exe bridge_models\stbridge_opensees\baseline_verified\stbridge_baseline_openseespy_gravity_modal.py
```

不要用默认 `python` 跑 OpenSeesPy；当前默认 Python 是 3.13，而 `openseespywin 3.8.0.0` 的二进制依赖 `python312.dll`。

## Builder 入口

- `build_model()`：只构建结构模型，不施加荷载。
- `apply_gravity_loads()`：施加重力荷载，不冻结状态。
- `commit_gravity_state()`：执行静力预载并 `loadConst`。
- `build_gravity_model()`：结构 + 重力预载，供模态/瞬态基线使用。
- `run_modal(num_modes=10)`：默认基于重力状态运行模态。
- `apply_earthquake_loads()`、`apply_wind_loads()`、`apply_traffic_loads()`：预留给上层 case 配置注入荷载，基础模型不内置这些工况。

## 基准建模假设

- 源模型：`D:\pyansys\STbridge.txt`
- 约束处理：`constraints Plain`
- 几何变换：`PDelta`
- 质量：BEAM 使用 `-mass DENS*A -cMass`，LINK 使用 `-rho DENS*A -cMass 1`
- LINK10 初应变：通过 `InitStrainMaterial`
- 重力状态：先静力预载，再 `loadConst('-time', 0.0)`

## 验证结论

前 10 阶频率与 ANSYS 的最大误差为 `0.314332%`，最低 MAC 为 `0.9999795`。短地震 200 点瞬态对比中，最大增量峰值误差为 `0.5398%`，最大增量 RMS 误差为 `3.9491%`，满足 5% 要求。
