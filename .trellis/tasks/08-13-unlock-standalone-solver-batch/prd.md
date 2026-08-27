# 解锁独立批量求解入口

## Goal

独立 `SOLVER_BATCH` 与 `RESULT_EXTRACTION` 走与受控 Agent 相同的共享 solver/result executor；通过对等、取消/恢复和失败关闭门后解除 501。

## Background / Confirmed Facts

- 受控 Agent 的 `SOLVER_BATCH` 已 LIVE；独立 `PLATFORM_API` 仍 `DISABLED`，unlock 要求为 `PHASE_1_SOLVER_RESULT_PARITY` 与 `CANCEL_RESUME_SMOKE`。
- 独立 `RESULT_EXTRACTION` 仍 `MOCK_ONLY`，unlock 要求为 `PHASE_1_RESULT_CATALOG`。
- 父任务要求：解除 501 前记录真实 Job、Artifact manifest、SHA、usage、取消/恢复和失败关闭证据。
- `supportsResume` 目前全部为 `False`。

## Requirements

- 独立创建的 `SOLVER_BATCH` 必须使用共享 `RealSolverExecutor`，输出统一 `result_catalog.json`、单位/列检查和来源 SHA。
- 独立 `RESULT_EXTRACTION` 只读取 solver output manifest，不得走摘要占位分支。
- 支持取消；恢复若未实现，能力目录保持 `supportsResume=false` 并有失败关闭测试，不得假装可恢复。
- 确定性 fake solver 与 OpenSeesPy 最小 smoke 必须通过；ANSYS 许可证环境作为发布认证，不作为本任务开发门。

## Acceptance Criteria

- [ ] 独立 `SOLVER_BATCH` 与受控 Agent 对同一冻结输入的关键响应、来源 SHA 和失败行为一致。
- [ ] 取消能终止 worker 进程树；超时由 dispatcher 硬墙钟处理。
- [ ] Live 创建前不再对已通过门的独立 `SOLVER_BATCH` / `RESULT_EXTRACTION` 返回 501；未通过组合仍 501。
- [ ] 能力目录更新 status、handler、unlockRequirements；`supportsResume` 与真实行为一致。

## Constraints

- 依赖 `08-13-isolate-platform-placeholders` 已归档或至少已消灭伪成功。
- 不解锁 surrogate / active learning / 优化独立入口。
- 不把 WIND/TRAFFIC 标成 LIVE。

## Out of Scope

- 代理训练、主动学习、独立 TOPSIS。
- 浏览器 E2E。
