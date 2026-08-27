# 批量时程对比与证据复核修复

## Goal

让已完成的真实阻尼器参数批量准确反映其求解证据状态，并让用户可以按阻尼器参数组合叠加比较同一工程响应的时程曲线。

## Confirmed Facts

- 最新真实 OpenSeesPy 参数扫描 `agr_995c3e303fc6` / `job_54edb4a16bc3` 已成功完成 4 个工况，且结果目录、输出 manifest、哈希、求解器版本和输入溯源均通过。
- 四个案例的原始 `summary.json` 均为 `status=completed`，记录 `metadata.solver_design.execution_mode="run"` 和 `metadata.command_stream.dry_run=false`。
- `PlatformStore._generate_real_damper_parameter_sweep_artifacts` 当前从不存在的 `metadata.execution_mode` 读取执行模式，因而把全部案例写成 `isVerifiedSolverOutput=false`。
- `allCasesVerified` 因四个案例的 false 标记失败；`realArtifactEvidence` 随后因案例摘要 Artifact 含该 false 标记失败。这不是数值求解失败。
- 当前时程接口 `GET /agent/runs/{runId}/timeseries` 只返回一个 `timeseries.csv`；前端 `TimeseriesSection` 只能选择该单一来源内的响应列，不能选择不同参数工况并叠加。

## Requirements

- R1：批量案例的真实输出验证必须读取实际契约中的 `metadata.solver_design.execution_mode`，同时继续拒绝 dry-run、未完成、哈希不匹配或无效输出。
- R2：`allCasesVerified` 与 `realArtifactEvidence` 必须从修正后的案例验证结果派生，不能因字段位置错误否决已完成的真实求解。
- R3：时程图必须允许先选择同一响应量，再多选参数工况；每条曲线的图例展示可读的 `c`、`alpha`、`vfloor` 参数。
- R4：时程比较必须从已登记且已验证的每个案例 CSV 读取；不得重新求解、不得让 LLM 改写数值、不得读取未登记路径。
- R5：不同物理单位的响应不得在同一纵轴混绘；用户选择多个响应量时，应按响应量分别绘图。

## Acceptance Criteria

- [x] 对真实完成、`solver_design.execution_mode="run"`、命令流非 dry-run 的案例，`isVerifiedSolverOutput=true`。
- [x] 对 dry-run、未完成或缺少真实求解证据的案例，验证仍为 false。
- [x] 修复后新提交的 4 工况批量可通过 `allCasesVerified` 与 `realArtifactEvidence`。
- [x] 时程区域可显示 4 个参数组合的同一响应曲线，图例含各自参数；曲线数据来自对应案例已登记 CSV。
- [x] 选择多个响应量时按响应量分图，且每张图只包含单位一致的曲线。
- [x] 为验证器、批量时程 API 和前端选择/绘图增加回归测试。

## Out of Scope

- 重新求解、修改四个阻尼器参数或改变 OpenSeesPy 数值模型。
- 把阻尼器参数本身作为随时间变化的 Y 轴数据；它们是工况选择和曲线图例。
- 跨不同 Agent run 或跨不同荷载记录的时程叠加。

## Key Decision

- 用户允许在不重新求解的前提下，依据当前 Job 已保存的原始输出、哈希和 manifest 重建其证据结论并回写该 Job/Run。
