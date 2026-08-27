# 完整接入智能体真实流程

## Goal

将 Momo 当前已广告的智能体与工程平台能力收敛到同一条可审计的真实数值执行链：Agent、专用 API 和前端入口使用相同的类型化请求、求解器、结果提取、代理模型、主动学习、优化决策和证据制品。未达到真实验收门的能力继续失败关闭或从 Live 能力目录隐藏，不允许返回占位成功。

## Background / Confirmed Facts

- 当前真实 Agent 路径包括 `REAL_AGENT_ANALYSIS`、`REAL_DAMPER_COMPARISON` 和 `REAL_BASELINE_OPTIMIZATION`；优化路径目前受限于已登记地震基准配置。
- `platform_store.py` 仍有独立 `SOLVER_BATCH`、`SURROGATE_TRAINING`、`ACTIVE_LEARNING` 的 501 保护，同时保留 `surrogate-model-placeholder`、`doe-surrogate-model-placeholder`、`realSolverExecution: PENDING_REPLACEMENT` 和 `realExecution: PENDING_REPLACEMENT` 等占位分支。
- `pyansys_bridge` 已有 `batch`、`surrogate`、`active_learning` 和 `optimization` 模块；这些模块应成为真实数值实现的复用来源，不能在 `PlatformStore` 复制算法。
- Agent 工作流已经声明 DOE、代理、主动学习、候选、推荐、FEM 复核和报告步骤，但部分步骤尚未接入独立可恢复的真实 Job/Artifact 执行器。
- 前端 `VITE_API_MODE` 支持 `mock` 和 `live`，并且 Solver、Surrogate、Optimization 等页面当前可触发独立能力；Live 模式必须由后端能力目录决定可用性。

## Requirements

### R1. 统一能力契约

建立 `JobType -> typed request -> real handler -> typed result` 注册表，覆盖 `ANALYSIS`、`DAMPER_COMPARISON`、`DAMPER_OPTIMIZATION`、`FULL_OPTIMIZATION`、`RESULT_INQUIRY` 及其支撑阶段。Agent 与独立 API 不得绕过同一契约。

### R2. 真实求解与结果提取

将 ANSYS/OpenSeesPy 求解、批量执行、取消、超时、恢复、结果提取和输出 manifest 收敛到共享执行器。结果只能来自真实求解输出和已登记输入 Artifact，并携带 solver、版本、来源 Artifact、SHA-256、单位和列目录。

### R3. 真实 DOE、代理和主动学习

DOE 使用确定性设计生成器并严格消费审批冻结的预算；训练数据拒绝缺列、非法单位、非有限值和未验证求解结果。代理模型必须持久化真实模型字节、特征/目标 schema、交叉验证指标和模型版本。主动学习新增点必须回到真实求解器，并受已批准的最大预算约束。

### R4. 真实优化、决策和 FEM 复核

Pareto/NSGA-II、熵权 TOPSIS、约束筛选、解释性结果和独立 FEM 复核都必须消费登记数据或模型制品。推荐结论必须能从 Artifact 重算；误差不达标时按冻结的 review 预算处理，不使用固定示例指标。

### R5. Agent 全能力接入

五类 Agent 工作流要覆盖真实荷载来源、组合、求解器、结果查询和报告。当前不具备物理模型或真实 smoke 的 solver × scenario × damper 组合必须明确标记为不支持，不得由模型自由承诺。

### R6. Live/Mock 边界

Live API 在创建 Job 或 Artifact 前拒绝未注册的真实能力，返回结构化 501 `CAPABILITY_NOT_IMPLEMENTED`；Mock 模式可以演示，但必须显著标注模拟数据。前端 Live 页面从能力目录动态启用入口，不能通过硬编码绕过后端门禁。

### R7. 运行安全和审计

所有阶段保留审批、幂等键、预算、取消、心跳、恢复、失败原因、执行来源和 Artifact manifest。失败关闭时不得留下伪成功 Job、占位制品或虚假 R²/RMSE/CV/FEM 指标。

## Acceptance Criteria

- [ ] 能力目录能逐项说明每个 JobType 的真实 handler、solver/scenario 支持矩阵、输入/输出 Artifact 和当前状态。
- [ ] Agent 与专用 API 对同一冻结输入走同一 handler，返回的关键响应、来源 SHA、预算消耗和失败行为一致。
- [ ] 独立 `SOLVER_BATCH`、`SURROGATE_TRAINING`、`ACTIVE_LEARNING` 只有在各自真实 smoke、回归、取消/恢复和审计门通过后才解除 501；否则创建前失败关闭。
- [ ] DOE 的请求点数、实际点数、补点数、真实求解总数和设计集哈希一致；训练集和模型制品可重新加载并复现预测。
- [ ] 主动学习不重复设计点、不超过冻结预算；精度达标不补点，精度不足按固定批次补点并在预算耗尽时结构化终止。
- [ ] 推荐候选、TOPSIS 排名、约束、权重、FEM 误差和最终报告均可由登记 Artifact 重算，不存在占位字节或固定示例指标。
- [ ] 五类 Agent 各有至少一个真实 smoke、一个失败关闭测试和一个取消/恢复测试；不支持组合从 Live 目录隐藏或明确标记。
- [ ] Live 前端只显示后端能力目录已放行的入口；Mock 页面显示“模拟数据”标识，生产构建和浏览器端到端链路通过。
- [ ] 完整后端 pytest、`compileall`、提交清单校验、前端 `tsc -b`、Vitest、oxlint 和生产构建通过。

## Constraints

- 不做数据库迁移；新增审计、能力和估算字段采用兼容扩展。
- 不改写已冻结的历史工作流快照；新流程使用新版本并保留旧运行读取能力。
- 不以 Mock、dry-run 或固定 preview 代替真实生产证据。
- 不执行需要许可证的 ANSYS 全量回归；使用确定性 fake solver、OpenSeesPy 最小真实 smoke 和具备许可证环境的发布门。
- 不在本任务中承诺没有物理实现的荷载/阻尼器/求解器组合上线；这类组合保持失败关闭。

## Out of Scope

- 重写已有 `pyansys_bridge` 数值算法或改变其物理模型参数定义。
- 一次性删除现有 `REAL_*` 原子路径；迁移完成前保留为数值和证据对照基线。
- 为了让 UI 显示“可用”而放宽审批、预算、制品来源或证据门禁。

## Open Questions

无阻塞问题。ANSYS 许可证环境作为发布验收门，开发和 CI 使用确定性 fake solver 与 OpenSeesPy smoke。
