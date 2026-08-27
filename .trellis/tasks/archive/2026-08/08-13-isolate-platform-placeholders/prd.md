# 隔离平台占位成功路径

## Goal

独立平台 Job 在 Live 下不得再把占位字节、固定示例指标或 `PENDING_REPLACEMENT` 写成成功工程结果。Mock 演示可以保留，但必须全程标注模拟数据。

## Background / Confirmed Facts

- 父任务 `08-09-full-live-agent-integration` 已对独立高风险入口做创建前 501。
- `platform_store.py` 曾保留 `surrogate-model-placeholder`、`doe-surrogate-model-placeholder`、`realSolverExecution: PENDING_REPLACEMENT`、`realExecution` / `realFemExecution: PENDING_REPLACEMENT` 等成功分支；现已改为 Mock 标注或 Live 501。
- 旧审计 P1-1 的根因仍在文件中：501 一旦被绕过或 Mock 未标注，用户会下载无效模型并看到伪造指标。
- 本任务是后续解除 501 的前置条件：先消灭伪成功，再谈放行。

## Requirements

- Live 创建或执行独立 `SOLVER_BATCH`、`SURROGATE_TRAINING`、`ACTIVE_LEARNING`、`EXPERIMENT_DESIGN`、`RESULT_EXTRACTION`、`MULTI_OBJECTIVE_OPTIMIZATION`、`ENTROPY_TOPSIS_DECISION`、`OPTIMIZATION_EXPORT` 时，未通过真实 handler 的路径必须失败关闭，不得登记 placeholder 制品。
- 任何仍返回的演示路径必须带 `executionMode=MOCK` / `simulation=true`，前端不得把 mock 指标显示为真实拟合或 FEM 复核。
- 受控 Agent `REAL_*` 路径和已 LIVE 的能力不得回退，不得改写已冻结历史 Job。

## Acceptance Criteria

- [x] Live 下上述独立 JobType 无法通过占位分支得到 `SUCCEEDED` 和可下载伪模型/伪报告。
- [x] 代码和测试中不再把 `PENDING_REPLACEMENT` 或 placeholder 字节当作成功证据。
- [x] Mock 页面和 Mock API 响应持续显示模拟标识；Live 页面不渲染前端自制 R²/RMSE/CV/FEM 队列。
- [x] 现有受控 Agent 真实路径回归通过。

## Constraints

- 不在本任务解除任何 501 / `DISABLED`。
- 不重写 `pyansys_bridge` 数值算法。
- 不做数据库迁移。

## Out of Scope

- 独立入口的真实 handler 对等验收与放行（后续子任务）。
- 浏览器端到端。
- 风/交通工况接入。
