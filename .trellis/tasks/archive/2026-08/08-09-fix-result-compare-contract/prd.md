# 修复 result.compare 契约冲突和描述冗余

## Goal

消除 `result.compare` 在结果追问与阻尼器工程对比阶段之间的同名异义，使每个公开工具名称只对应一套输入合同和使用语义；同时保留服务端幂等元数据，但不再把协议上不可传入的幂等键说明重复塞进模型描述。

## Background

- `RESULT_INQUIRY.QUERY` 使用 `result.compare` 查询同一登记 CSV 的两列，公开输入合同为 `artifactId + columns`。
- `DAMPER_COMPARISON.COMPARISON` 当前也登记 `result.compare`（`app/agents/workflows.py:135`），但 Python Job 阶段轨迹使用 `runId + jobId`，与查询合同不兼容。
- `WorkflowGuard` 只授权工具名、步骤、前置、限额和幂等键，不校验 Pydantic 参数，因此同名异义不会立即报错，却会让目录 Schema、描述和审计轨迹互相矛盾。
- `HarnessToolSpec.model_description()` 当前对所有 `SERVER_DERIVED` 工具追加幂等提示；这些工具的 `additionalProperties=false` Schema 已不允许模型传入 `idempotencyKey`，目录元数据也已独立保存幂等来源。

## Requirements

1. `RESULT_INQUIRY.QUERY` 继续使用 `result.compare`，保留 `ResultDerivedInput` 的 `artifactId + columns` 合同和 CSV 两列峰值比较语义。
2. `DAMPER_COMPARISON.COMPARISON` 改用独立的 `comparison.compare`，其登记描述必须明确工程对比阶段的触发条件，不得复用结果追问描述。
3. `comparison.compare` 的登记输入 Schema 必须与 Python Job 阶段登记的实际参数一致，接受且仅接受 `runId + jobId`。
4. 工具目录名称一致性检查必须继续覆盖新工具，防止工作流 `allowedTools` 与 `_HARNESS_TOOL_SPECS` 漂移。
5. `idempotencyKeySource=SERVER_DERIVED` 目录元数据和持久化审计字段保持不变。
6. 面向模型的 description 不再自动追加“幂等键由 Harness……模型不得传入或改写”文本；Schema 继续通过 `additionalProperties=false` 禁止未登记字段。

## Acceptance Criteria

- [x] `DAMPER_COMPARISON.COMPARISON.allowedTools == ['comparison.compare']`，不再出现 `result.compare`。
- [x] `result.compare` 的目录 Schema 仍要求 `artifactId`、`columns`，描述仍仅表达结果追问语义。
- [x] `comparison.compare` 的工具登记 Schema 仅包含且要求 `runId`、`jobId`，描述仅表达阻尼器工程对比语义。
- [x] 使用 `{runId, jobId}` 对 `comparison.compare` 执行 Guard 授权并按目录输入模型校验均成功。
- [x] 标记 `SERVER_DERIVED` 的工具仍输出 `idempotencyKeySource` 元数据，但所有模型描述均不包含自动追加的幂等提示。
- [x] 工具目录稳定排序及目录/工作流一致性回归测试通过。
- [x] 后端相关测试和全量测试通过，提交清单重新生成且离线校验通过。

## Out of Scope

- 不把 Pydantic Schema 校验职责移入 `WorkflowGuard`。
- 不改变幂等键生成、审批或持久化行为。
- 不重构其他阶段工具的执行流程或工程算法。
- 不修改 `result.compare` 的数值计算逻辑。

## Notes

- 本任务范围小且方案明确，采用 PRD-only 轻量任务。
- 阻塞问题：无。
