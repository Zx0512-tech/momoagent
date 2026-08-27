# Python 工作流契约规范

- `WorkflowDefinition` 是唯一事实源，系统提示和执行授权从同一快照生成。
- 每个步骤明确允许工具、前置步骤、成功迁移、失败路由和重试上限。
- run 启动时冻结规范化快照、版本和 SHA256。
- 工具不在当前 `allowedTools` 时必须在调用 handler 前拒绝。
- 运行时只能沿快照声明的边迁移；不可由模型直接写入 `currentStep`。
- Solver Job 创建后不得自动重复；失败后的新求解必须重新审批。

## 游标、门禁与资源契约

### 1. Scope / Trigger

所有 `WORKFLOW_HARNESS` run 的流程进度、工具授权、审批和 Job 恢复都必须读取同一份冻结 `workflowSnapshot`。

### 2. Signatures

```python
freeze_workflow(definition: WorkflowDefinition) -> dict[str, Any]
WorkflowGuard.authorize(
    *, workflow_snapshot, current_step, tool_call,
    completed_steps=(), step_attempt=1, repeated_no_progress=0,
) -> WorkflowAuthorization
WorkflowGuard.advance(
    *, workflow_snapshot, current_step, completed_steps,
    gate_passed, failure_code=None, gate_context=None,
) -> tuple[str, list[str]]
```

### 3. Contracts

- `freeze_workflow` 将每个步骤的直接前置展开为传递闭包，并把规范化 JSON、`workflowVersion`、`workflowSha256` 一并持久化。
- `currentStep` 是唯一流程游标；读取 run 时只能由游标派生展示 `status`，禁止再从 legacy `status` 覆盖游标。
- 首次挂载旧 run 允许按 status 做一次迁移；发现快照外游标时只迁移到 `initialStep` 并写入 `workflowCursorError`。
- `WorkflowToolCall.usage` 中的资源值不得超过快照 `limits`；分析 solver 调用默认 `realSolveCount=1`，无阻尼分析上限为 1。
- 提供 `gate_context` 时，`successGate`（如 `preflight.passed == true`、`job.id != null`）必须通过，否则返回 `WORKFLOW_GATE_FAILED`。

### 4. Validation & Error Matrix

| 条件 | 错误码 |
| --- | --- |
| 工具不在当前步骤 | `WORKFLOW_STEP_VIOLATION` |
| 任一传递前置未完成 | `WORKFLOW_PREREQUISITE_MISSING` |
| 超过 limits | `WORKFLOW_LIMIT_EXCEEDED` |
| 推进成功但未提供 gate context | `WORKFLOW_GATE_CONTEXT_REQUIRED` |
| 成功门禁未满足 | `WORKFLOW_GATE_FAILED` |
| 重复无进展两次 | `HARNESS_LOOP_DETECTED` |
| 步骤尝试次数耗尽 | `WORKFLOW_RETRY_EXHAUSTED` |

### 5. Good / Base / Bad

- Good：比较工作流在 `WAITING_APPROVAL` 同时完成 `REQUIREMENTS/CALIBRATION/PREFLIGHT` 后才允许审批工具。
- Base：旧 run 没有游标时只在挂载瞬间创建合法初始游标。
- Bad：`_decorate_run` 根据 `WAITING_JOB` 把 Guard 已推进的 `completedSteps` 重算并抹掉。

### 6. Tests Required

- 遍历四类工程工作流 × 全部 legacy status，断言当前步骤在快照内、前置闭包完整。
- 比较审批缺任一祖先步骤时断言 `WORKFLOW_PREREQUISITE_MISSING`。
- 分析 `realSolveCount=2` 断言 `WORKFLOW_LIMIT_EXCEEDED`。
- 提供失败 gate context 断言 `WORKFLOW_GATE_FAILED`。
- 服务重启/装饰 run 后断言 Guard 游标保持不变。

### 7. Wrong vs Correct

```python
# Wrong: 每次读取都按 status 反推游标
run['currentStep'], run['completedSteps'] = _runtime_progress(task_type, run['status'])

# Correct: 只由已持久化游标派生展示状态
run['status'] = _status_from_workflow_cursor(run)
```
