# 解锁独立代理与主动学习入口

## Goal

独立 `EXPERIMENT_DESIGN`、`SURROGATE_TRAINING`、`ACTIVE_LEARNING` 使用已有真实 DOE / 训练 / infill 执行器；模型可重载复现、infill 回真实求解且不超过冻结预算后解除 501。

## Background / Confirmed Facts

- 受控 Agent 优化链已有真实 DOE、surrogate reload 一致性和 active-learning 预算测试。
- 独立入口仍 `MOCK_ONLY` 或 `DISABLED`，缺完整请求契约与取消/恢复 worker 生命周期。
- 父任务明确：不存在假 `.pkl`、固定 R²/RMSE/CV。

## Requirements

- DOE 消费审批冻结点数；训练集拒绝缺列、非法单位、非有限值和未验证求解结果。
- 代理制品必须包含模型字节、schema、fold、CV 和版本；reload 后预测与训练时一致。
- 主动学习不重复设计点；每轮最多 2 点、最多 2 轮；精度达标不补点；预算耗尽结构化终止。
- 每个 infill 点必须调用已放行的真实 solver，不得用占位求解。

## Acceptance Criteria

- [ ] 独立训练可 reload 并复现预测；CV 来自真实 fold。
- [ ] infill 去重且不超过冻结预算；超预算或求解失败失败关闭。
- [ ] `EXPERIMENT_DESIGN`、`SURROGATE_TRAINING`、`ACTIVE_LEARNING` 在 Live 下按目录解除 501，其余未验收入口保持关闭。
- [ ] 无 placeholder 模型字节，无前端自制指标。

## Constraints

- 依赖独立 solver 入口已放行，否则 infill 无法回真实求解。
- 不承诺独立 `MULTI_OBJECTIVE_OPTIMIZATION` / `ENTROPY_TOPSIS_DECISION` / `OPTIMIZATION_EXPORT` 同期解锁；若证据已可重算可在本任务末尾评估，否则留给父任务收口。

## Out of Scope

- 新工况（风/交通）。
- 浏览器 E2E。
