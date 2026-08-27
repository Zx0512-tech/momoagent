# 规划智能体真实流程完整接入

## Goal

形成可按阶段实施和验收的真实流程路线图，使全部已广告智能体能力最终复用真实数值执行链，不再依赖占位制品、固定示例指标或伪成功 Job。

## Requirements

- P1：建立能力矩阵，覆盖五类工作流：`ANALYSIS`、`DAMPER_COMPARISON`、`DAMPER_OPTIMIZATION`、`FULL_OPTIMIZATION`、`RESULT_INQUIRY`。
- P2：覆盖支撑阶段：载荷/组合、DOE、命令流、批量求解、结果提取、代理训练、主动学习、Pareto/NSGA-II、熵权 TOPSIS、独立 FEM 复核、报告和图件。
- P3：标注当前真实、受限真实、失败关闭和占位分支，包括 `surrogate-model-placeholder`、`PENDING_REAL_TRAINING`、`PENDING_REPLACEMENT` 与通用 summary fallback。
- P4：目标架构必须让 Agent 与独立平台 API 共用同一套类型化执行器、Artifact 契约、队列、取消、预算、审批和证据门禁。
- P5：优先复用 `pyansys_bridge.batch/surrogate/active_learning/optimization`，不得在 `PlatformStore` 中复制数值算法。
- P6：每个阶段给出依赖、可观察验收、真实求解最小 smoke、自动化回归、回滚开关和进入下一阶段的门槛。
- P7：只有真实制品、输入来源、输出 manifest、版本信息和非 dry-run 证据全部通过，才移除 501 或启用 Live 前端入口。
- P8：所有广告组合必须收敛为“真实支持”或“能力目录明确不支持”；不允许 Live UI/API 留下生成示例数据的第三种状态。

## Acceptance Criteria

- [ ] 路线图列出当前每个 Agent/阶段的实现来源和缺口，不把现有原子真实优化误报为占位。
- [ ] 阶段顺序先建立 solver/result 基座，再接 DOE/surrogate/active learning/optimization，最后切换 Agent 与独立入口。
- [ ] `SOLVER_BATCH`、`SURROGATE_TRAINING`、`ACTIVE_LEARNING` 的 501 解除条件分别明确。
- [ ] 地震、风、交通、风车组合与 ANSYS/OpenSeesPy 的支持矩阵有明确验收或明确下架规则。
- [ ] 计划包含生产运行的恢复、取消、幂等、预算、可观测性和制品保真要求。
- [ ] 计划可被拆成后续独立 Trellis 实施任务，不要求一次大爆炸重构。

## Out of Scope

- 在本规划子任务中直接实现数值模块。
- 承诺没有对应求解器物理模型的组合一定上线。
- 用 Mock 或 dry-run 结果替代真实 smoke 验收。
