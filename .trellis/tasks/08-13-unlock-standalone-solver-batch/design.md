# 独立求解入口设计

## 契约

`RealExecutionRegistry.resolve('SOLVER_BATCH', params)` 在无 `REAL_AGENT_*` runMode 时改为可 LIVE 的 `PLATFORM_API` handler，但必须走同一 `solver_executor` / `result_executor`。禁止 `PlatformStore` 再复制求解算法。

## 放行门

1. 与现有 `REAL_AGENT_ANALYSIS` 金样对等：输入 SHA、result catalog 列/单位、输出 manifest。
2. 取消/超时：queued worker + dispatcher 墙钟，锁外执行。
3. 失败关闭：缺制品、非法单位、非有限值不得 `SUCCEEDED`。

恢复：若本阶段做不到与取消对等，保持 `supportsResume=false`，测试断言 resume API 结构化拒绝。
