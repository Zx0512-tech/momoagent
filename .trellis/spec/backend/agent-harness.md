# Agent Harness 规范

- LLM 只提出动作；所有工具执行必须经过 `WorkflowGuard` 和 `TypedToolRegistry`。
- `READ_ONLY` 与受控制品写入可自动执行；Solver 和其他副作用必须审批。
- 副作用调用必须具有服务端生成的稳定幂等键；模型原始参数与实际执行参数分别保存并计算 SHA256。
- 工具错误使用稳定错误码和结构化 details，不把异常堆栈发送给用户。
- 大型结果保存为制品，模型上下文只包含紧凑摘要和制品引用。
- 最终数值结论必须通过既有证据门禁。
- AUTO 与显式工程任务默认走 `WORKFLOW_HARNESS`；`LEGACY` 只作为迁移回滚开关。
- `currentStep` 与 `completedSteps` 是冻结快照内的唯一流程事实；读取 run 时只能由游标单向派生展示状态，不能用 legacy status 反推并覆盖游标。
- `workflow.start` 同时承载流程选择和类型化工程意图，Harness 路径不得再调用旧 `classify_task` 或二次工程规划提示词。
- 结果追问在同一原生工具循环中执行；可读取所有会话的已完成工程结果，但每个制品 ID 必须属于对应 run 经 `result_catalog.json` 校验后的登记白名单。
- 三类工程工作流的 `WAITING_APPROVAL` 成功门统一为 `approval.status == APPROVED`；审批放行后才允许进入 solver/baseline 工具。
- 兼容模式下 Job 内部由 Python 完成的阶段必须写入 `executionSource=PYTHON_JOB`；启用持久化循环后使用 `executionSource=LLM_PERSISTENT_LOOP`。两种模式都不能把阶段完成仅表达为游标跳转。
- 取消和审批拒绝必须通过 Guard 将游标推进到快照中的 `CANCELLED`；只写 legacy `status` 不足以表达终止。
- `ToolExecutionError` 在 v1 API 由全局异常处理器统一转换为结构化 422，任何工具入口都不得泄漏为 HTTP 500。
- Job 阶段门禁只能使用 Job 状态、已登记制品、审批工具轨迹和报告字段等真实证据；禁止构造全为 `true` 的 gate context。
- 审批授权以幂等键复用已有工具轨迹，进入求解步骤时 `stepAttempt` 从 1 开始，审批重放不得消耗重试额度。
- 原生用户包装消息持久化为 `harnessContent`，历史回放按字符预算保留最新消息并压缩工具结果；请求必须设置 `parallel_tool_calls=false`。
- 工具目录中的每个工具必须说明“什么时候用”和不适用边界；公开 JSON Schema 必须直接来自登记的 Pydantic 输入模型，不得维护宽泛的手写 Schema。
- 模型不得传入或改写幂等键；目录/审计元数据使用 `idempotencyKeySource=SERVER_DERIVED` 声明来源，description 只说明使用时机和边界，不重复解释 Schema 已禁止的字段。
- 参数校验不得静默执行字符串转数值、标签转 ID、大小写修正或字段裁剪；默认值或别名展开后的 `effectiveArguments` 必须返回模型并写入审计轨迹。
- Bootstrap 和结果 `QUERY` 回合只向模型暴露当前 `workflowState.allowedTools`；供应商仍返回未公开工具时，必须把 `WORKFLOW_STEP_VIOLATION` 作为 tool result 反馈给模型，并与参数校验共享最多两次修复额度。不得在首次误选时直接创建用户可见的失败 run，连续三次仍越权时必须失败关闭。
- Harness 默认启用模型内部思考，并结合完整会话历史完成意图理解和澄清合并；Python 规则只负责工作流、权限、预算、Schema 和证据门禁，不能替代 LLM 做关键词路由。
- 模型内部思维链不持久化、不返回前端。原生工具参数 JSON 无法解析时，Planner 必须将格式错误反馈给模型并最多修复两次；合法 JSON 的类型/语义错误由 Harness 以 tool result 反馈并采用相同修复上限。
- `AUTO` 消息命中同会话最近终态结果时必须直接进入只读 `QUERY`，不得先进入 `ROUTING`、调用 bootstrap 模型或伪造 `workflow.start(taskType='RESULT_INQUIRY')` 工具轨迹；显式工程任务和带上传的消息仍走工程启动路径。
- 原生结果追问循环最多 10 回合，必须容纳当前目录的 6 个语义指标逐项查询和一个最终回答回合；达到上限仍无回答时返回 `HARNESS_LOOP_DETECTED`，不得改成无界循环。
- 会话存在 `WAITING_JOB`/`REVIEWING` 的 Harness 工程 run 时，新消息必须先绑定该 run 并调用 `workflow.observe`；不得重新进入 bootstrap 或创建第二个工程 run。

## 终态结果直接查询路由契约

### 1. Scope / Trigger

适用于 `WORKFLOW_HARNESS` 会话中已经存在带 `reportArtifactId` 的 `SUCCEEDED` 或 `COMPLETED_DIAGNOSTIC` 工程 run，用户以 `AUTO` 继续查询已有结果的场景。结果查询是只读数据访问，不属于求解阶段编排。

### 2. Signatures

```python
AgentConversationMixin._dispatch_message(
    repository, session, content, now, load_import, task_type,
) -> dict[str, Any]
WorkflowHarnessMixin._create_native_inquiry_run(
    repository, session, source_run, content, now, *, prior_messages=None,
) -> dict[str, Any]
```

### 3. Contracts

- `AUTO` 路由优先级保持待审批 → 待澄清 → 活动 run → 最近终态结果 → 工程 bootstrap。
- 命中终态结果且本轮没有上传时，直接创建 `taskType=INQUIRY` 的审计 run，并绑定 `sourceRunId`；不得执行 `workflow.start` 或创建 `ROUTING` run。
- 查询 run 从冻结的 `RESULT_INQUIRY` 定义直接落在 `currentStep=QUERY`，只暴露 `workflowState.allowedTools` 中的 `result.*` 只读工具。
- 服务端把默认来源 run、全局 `availableResults`、逐 run 目录和登记制品注入 `resultInquiryContext`；制品白名单仍逐 run 由 `result_catalog.json` 决定，模型不能传入任意路径或未登记制品。
- 新会话没有本会话终态结果时，bootstrap 可收到最近的全局终态结果 ID，由模型在 `RESULT_INQUIRY` 与新工程任务之间选择；显式工程任务不受全局结果截获。
- 每个完成结果向前端投影 `resultMetadata`，字段至少包括 `runId/sessionId/taskType/status/condition/model/solver/hasDamper/damperTypes/damperParameters/updatedAt`。字段只从持久化 intent、合同、case 参数或报告推荐参数读取，不猜测缺失参数。
- 活动 run 在刷新或报告恢复后变为可查询终态时，也直接进入同一查询入口，不重新 bootstrap。
- 显式 `ANALYSIS`、`DAMPER_COMPARISON`、`DAMPER_OPTIMIZATION`、`FULL_OPTIMIZATION` 仍进入工程工作流；带上传的 `AUTO` 消息也不得被旧结果截获。

### 4. Validation & Error Matrix

| 条件 | 行为 |
| --- | --- |
| `AUTO` + 最近终态结果 + 无上传 | 直接创建 `INQUIRY`，不调用 bootstrap，不保存 `workflow.start` 轨迹 |
| 显式工程任务 + 最近终态结果 | 进入指定工程工作流，终态结果只作为可用上下文，不截获请求 |
| `AUTO` + 上传 + 最近终态结果 | 进入工程/载荷路由，不把上传误解为结果查询 |
| 终态 run 没有报告 | 不具备直接查询资格，继续正常路由 |
| 查询引用非全局结果目录登记制品 | `ARTIFACT_NOT_REGISTERED`，不读取文件 |
| 某个历史 run 的目录/SHA/列校验失败 | 该 run 加入 unavailable 清单，不把其制品加入白名单 |
| 查询工具越过 `QUERY` 白名单 | `WORKFLOW_STEP_VIOLATION`，游标不变 |

### 5. Good / Base / Bad Cases

- Good：求解成功后用户问“分别给出所有统计量的峰值”，系统直接把最近结果目录交给只读查询循环并回答。
- Base：用户显式选择 `ANALYSIS` 并要求换一条地震记录，系统启动新工程 run，而不是查询旧结果。
- Bad：已有结果时仍先让模型在 `ROUTING` 调用 `workflow.start`；历史上下文可能诱导模型选择 `workflow.observe`，造成“步骤 ROUTING 不允许调用工具”的假失败。

### 6. Tests Required

- 断言终态结果的 `AUTO` 追问不调用 `_dispatch_harness_message` 或 bootstrap Planner，直接绑定原 `sourceRunId`。
- 断言直接查询的模型消息中没有 `workflow.start` 工具调用，工具目录只包含当前 `QUERY` 的五个 `result.*` 工具。
- 断言 `resultInquiryContext` 包含来源 run、登记制品和冻结查询状态。
- 断言同会话与其他会话的已校验登记制品可查询，任意未登记 artifactId 返回 `ARTIFACT_NOT_REGISTERED`。
- 断言新会话 bootstrap 能选择全局 `RESULT_INQUIRY`，显式工程任务仍按原工作流启动。
- 断言会话详情和结果卡显示持久化工况、模型、求解器、阻尼类型和实际参数。
- 断言显式工程任务在存在终态结果时仍调用工程 Harness 路由。
- 断言活动 run 刷新成功或报告恢复成功后同样绕过 bootstrap。

### 7. Wrong vs Correct

```python
# Wrong：查询已有结果仍回到工程启动路由
return self._dispatch_harness_message(..., inquirable_run=source_run)

# Correct：直接绑定终态结果并进入受限只读 QUERY
return self._create_native_inquiry_run(
    repository, session, source_run, content, now, prior_messages=None,
)
```

## 结果追问流式证据契约

### 1. Scope / Trigger

适用于聊天端对已完成工程结果执行一个或多个 `result.*` 只读工具的场景。目标是在查询完成前逐项显示已验证指标，同时保留原同步消息接口兼容调用方。

### 2. Signatures

```python
AgentService.create_message(..., *, event_sink=None) -> dict[str, Any]
WorkflowHarnessMixin._create_native_inquiry_run(..., *, prior_messages, event_sink=None) -> dict[str, Any]
stream_agent_message_events(session_id, payload) -> Iterator[str]
```

流端点：`POST /api/v1/agent/sessions/{session_id}/messages/stream`，响应媒体类型为 `application/x-ndjson`。

### 3. Contracts

- 流事件顺序为 `accepted` → 可选 `run` → 零个或多个 `progress` → `complete`；失败以 `error` 终止，不把异常堆栈或内部异常文本返回前端。
- `run/progress/complete` 的 `run` 必须是冻结快照，后续原地更新不得污染已经排队的旧事件。
- 查询 run 创建后立即发送 `run`；每次 `result.peak` 成功并持久化工具结果后发送 `progress`，不得等模型最终叙述后一次性返回。
- `resultSummary.inquiryMetrics` 是用户可见数值的结构化唯一来源；每项至少包含 `metricId/label/sourceColumn/peakAbsolute/peakSigned/unit/peakTimeS/sampleCount`。
- `queryProgress.completed` 只统计已经完成的 `result.peak` 指标，不统计 `result.columns` 等辅助查询。
- 模型叙述未通过数字证据门时仍保留审计用 `narrativeSummary/narrativeMode`，用户可见 `message` 只给出简短来源说明，不能暴露“校验失败”等内部控制语句或回退为 Markdown 表。
- 历史 `INQUIRY` run 已保存 `inquiryFacts.queries` 但没有结构化指标时，读取接口按查询事实投影 `inquiryMetrics`；投影不能改写持久化审计记录。
- 会话详情中绑定旧查询 run 的助手 Markdown 消息也投影为同一简短 `message`；即使后续 run 失败，历史原始表格也不能重新出现在聊天线程，且底层消息审计记录保持不变。
- 同步 `/messages` 接口继续可用；流式能力只增加事件观察，不改变工作流、工具白名单、证据门或副作用权限。

### 4. Validation & Error Matrix

| 条件 | 行为 |
| --- | --- |
| 查询 run 刚创建 | 发 `run`，状态为 `PLANNING/QUERY` |
| 一个 `result.peak` 成功 | 先持久化，再发包含该指标的 `progress` |
| `result.columns` 成功 | 可发进度快照，但 `completed` 不增加 |
| 模型数值未通过证据门 | 指标仍按工具原值完成；不向用户展示内部校验文案 |
| 历史 Markdown 查询被读取 | 只读投影为结构化指标，原记录不变 |
| 执行异常 | 发通用 `error`，流关闭；不得泄漏堆栈 |

### 5. Good / Base / Bad Cases

- Good：六个峰值查询完成一个就新增一条指标，最终卡片保留六条，用户无需等待完整模型回合。
- Base：只有一项峰值时显示一条工程值和发生时刻，最终补充只读来源说明。
- Bad：后端拼 Markdown 表，前端把表字符串和卡片摘要各渲染一次；或把列检查计成一项峰值。

### 6. Tests Required

- 断言事件顺序、冻结快照和 `complete` 终态。
- 断言 NDJSON 被拆分在任意字节块时前端仍能逐行解析。
- 断言每次 `result.peak` 后 `inquiryMetrics` 与 `queryProgress.completed` 单调增加。
- 断言辅助查询不增加指标计数。
- 断言旧 Markdown run 读取时得到结构化指标且源字典不被修改。
- 断言数字门回退文案不包含内部“校验”语句。

### 7. Wrong vs Correct

```python
# Wrong：最终把原始工具数值拼成 Markdown，并同步一次返回
summary['message'] = build_markdown_table(query_results)

# Correct：每项工具证据持久化后发送结构化冻结快照
summary['inquiryMetrics'] = structured_metrics(query_results)
event_sink({'type': 'progress', 'run': frozen_run_snapshot(run)})
```

## 活动运行续接与观察工具契约

### 1. Scope / Trigger

适用于同一会话中已有 `WORKFLOW_HARNESS` 工程 run 处于 `WAITING_JOB`、`REVIEWING`，或因 `REPORT_GENERATION_ERROR` 暂时标记为 `FAILED`，用户继续询问进度、完成状态或结果是否可用的场景。

### 2. Signatures

```python
AgentConversationMixin._find_active_harness_run(repository, session_id) -> dict[str, Any] | None
WorkflowHarnessMixin._dispatch_harness_active_run_message(...) -> dict[str, Any]
WorkflowGuard.authorize(..., observational_tools=('workflow.observe',)) -> WorkflowAuthorization
```

`workflow.observe` 使用 `HarnessNoInput`，公开参数必须是空对象 `{}`。

### 3. Contracts

- 路由优先级为待审批 → 待澄清 → 活动 run → 终态结果追问 → bootstrap；活动 run 不得落回 `workflow.start`。
- `FAILED + REPORT_GENERATION_ERROR` 是可恢复的活动候选：先刷新原 run 并尝试关联已经持久化的报告；不得把用户追问送回 bootstrap。
- `workflow.observe` 是全局只读元工具，不写入历史冻结快照的 `allowedTools`；调用时通过 `observational_tools` 显式授权。
- 观察调用不推进 `currentStep`、不增加 `stepAttempt`、不消耗业务步骤重试次数，也不创建 Job/Artifact。
- 活动状态投影把 `allowedTools` 收窄为 `['workflow.observe']`，同时用 `stepActionTools` 只读展示 Python Job 当前负责的阶段动作。
- LLM 必须先观察再回答；观察结果保存原始/实际参数、SHA256、`executionSource=WORKFLOW_HARNESS`，且 `arguments == effectiveArguments == {}`。
- 首次刷新发现 run 已持久化报告时，直接将同一 run 作为 `RESULT_INQUIRY` 来源，不重新确认求解器、荷载或响应量。
- 报告恢复成功时直接将同一 run 作为 `RESULT_INQUIRY` 来源；恢复失败时返回原 run 的保守失败说明，不调用 `workflow.observe`，也不新建工程 run。

### 4. Validation & Error Matrix

| 条件 | 行为 |
| --- | --- |
| 活动 run 中调用 `workflow.start` | 反馈 `ACTIVE_RUN_OBSERVATION_REQUIRED` 并要求改用观察工具 |
| `workflow.observe` 带任意参数 | `INPUT_VALIDATION_ERROR`，不执行观察 |
| 观察工具被标为非只读风险 | `WORKFLOW_STEP_VIOLATION` |
| 活动 run 仍在执行 | 返回同一 `runId` 和真实 `WAITING_JOB`/`REVIEWING` 状态 |
| 刷新后已完成且报告存在 | 进入结果追问，不创建第二个工程 run |
| `FAILED + REPORT_GENERATION_ERROR` 且报告可恢复 | 恢复原 run 后进入结果追问，不调用 `workflow.start` |
| `FAILED + REPORT_GENERATION_ERROR` 且报告不可恢复 | 说明报告生成失败，保留原 run，不调用观察工具或 bootstrap |
| 模型连续三次拒绝观察 | 使用基于持久化状态的保守文字回复，原 run 保持不变 |

### 5. Good / Base / Bad Cases

- Good：审批后用户问“完成了吗”，模型调用 `workflow.observe({})`，工具返回原 `runId` 的最新状态，模型据此回答。
- Good：Job 已成功且报告制品已经落盘，但 run 曾因关联竞态失败；用户追问时系统恢复同一 run 并展示结果。
- Base：Job 仍运行时只说明尚未完成，不承诺完成时间或伪造进度。
- Bad：把活动或可恢复失败 run 的消息送回 bootstrap，让模型再次调用 `workflow.start` 并重新询问已经冻结的求解器、荷载和响应量。

### 6. Tests Required

- 断言活动 Job 追问只保留一个 run，工具轨迹为 `workflow.observe`，回复消息绑定原 `runId`。
- 断言观察授权不修改冻结步骤的 `allowedTools`，也不受业务步骤尝试次数影响。
- 断言刷新时从 `WAITING_JOB` 转为 `SUCCEEDED` 后，结果追问收到原 run，且没有第二个工程 run。
- 断言 `REPORT_GENERATION_ERROR` 关联既有报告后进入原 run 的结果追问；报告缺失时只返回失败说明，且两种情况都不调用 bootstrap。
- 断言 bootstrap 模型目录只有 `workflow.start`，且供应商返回未公开的观察工具时仍由 Guard 拒绝并触发受控修复。

### 7. Wrong vs Correct

```python
# Wrong：审批后的普通消息重新进入工作流启动路由
return self._dispatch_harness_message(..., inquirable_run=None)

# Correct：先续接活动或可恢复失败 run；刷新后按真实状态进入结果追问或失败说明
active_run = self._find_active_harness_run(repository, session_id)
if active_run:
    return self._dispatch_harness_active_run_message(..., active_run, ...)
```

## Job 后内部阶段的持久化 LLM 循环

### 1. Scope / Trigger

当 `MOMO_AGENT_PERSISTENT_LOOP=true` 且真实 Job 到达终态时，后续已由该原子 Job 产生证据的内部阶段不再由 Python 批量补写完成，而是由 LLM 按冻结游标逐轮选择工具。

### 2. Signatures

```python
harness_step_tool_catalog(allowed_tools) -> list[dict[str, Any]]
WorkflowHarnessMixin._resume_persistent_job_stage(
    repository, run, *, artifact_ids
) -> bool
```

run 兼容扩展字段：`harnessLoop.version/status/revision/wakeReason/currentStep/externalJobId/lastToolCallId/modelFailureCount/maxModelFailures/updatedAt/error?`。

### 3. Contracts

- 每轮目录只能包含冻结快照当前步骤的 `allowedTools`，并设置 `parallel_tool_calls=false`。
- 每轮必须且只能执行一个工具；参数先过公开 Pydantic Schema，再过 `WorkflowGuard` 和 `TypedToolRegistry`，不得裁剪或修正。
- 创建外部 Job 后写 `WAITING_EXTERNAL`；Job 成功只完成对应执行步骤并写 `READY/JOB_SUCCEEDED`，不得批量快进。
- 工具轨迹使用稳定调用 ID `{runId}:loop:{stepId}`，保存模型调用 ID、原始/实际参数及其 SHA、审批、风险、Job 和紧凑制品证据。
- 已保存成功工具而游标尚未推进时，下一次刷新从同一轨迹恢复，不重复执行 handler。
- 同一步骤连续模型回合失败最多重试 3 次；计数持久化在 `harnessLoop.modelFailureCount`。第三次失败必须把 run 与 loop 一并置为 `FAILED`，写 `PERSISTENT_LOOP_RETRY_EXHAUSTED`，不得回到 `READY`。
- 活动会话始终绑定原 `runId`：运行中通过 `workflow.observe` 回答；报告存在后进入该 run 的结果查询，不得调用 `workflow.start`。
- 原子 Job 已产生证据的中间阶段和 evidence/review 动作均由模型逐轮发起；审查结论仍由确定性 Agent handler 计算并以 `pendingReviewOutcome` 持久化，报告生成继续使用确定性处理器。

### 4. Validation & Error Matrix

| 条件 | 行为 |
| --- | --- |
| 当前步骤引用未登记工具 | `TOOL_NOT_REGISTERED`，不调用模型 |
| 模型未调用或一次调用多个工具 | `HARNESS_SINGLE_TOOL_REQUIRED`，循环回到 `READY/MODEL_TURN_RETRY` |
| 模型调用非当前步骤工具 | `WORKFLOW_STEP_VIOLATION`，游标不变 |
| 同一步骤连续三次模型/校验/工具错误 | run=`FAILED`、loop=`FAILED/MODEL_TURN_EXHAUSTED`，停止后续模型调用 |
| 参数校验会修改输入 | `INPUT_VALIDATION_ERROR`，handler 不执行 |
| 外部 Job 未产生登记制品 | `WORKFLOW_GATE_FAILED`，不得进入下一步 |
| 进程在工具落盘后、游标落盘前退出 | 从稳定 toolCallId 恢复并只推进游标 |

### 5. Good / Base / Bad Cases

- Good：统一求解 Job 启动后，BASELINE 与 DOE 由服务器并行执行；两者完成后才进入
  SURROGATE，基线指标约束只在优化后处理阶段生效。
- Base：用户此时问“算完了吗”，系统观察同一 run，说明真实求解完成、正在处理当前内部阶段。
- Bad：Job 成功后用 `while` 把 DOE 到 FEM_VALIDATION 全部补成 `PYTHON_JOB/SUCCEEDED`，或把用户追问送回 bootstrap。

### 6. Tests Required

- 断言 Job 成功只完成真实执行步骤，后续没有自动阶段轨迹。
- 断言模型只收到当前步骤工具目录，每轮只推进一步并写 `LLM_PERSISTENT_LOOP`。
- 断言原始参数等于实际参数，两个 SHA 和服务端幂等来源完整。
- 断言 review 必须先由模型选择；进程在审查轨迹落盘后退出时，从 `compactResult` 恢复且不重复验收。
- 断言活动 run 追问保持唯一 run；刷新完成后沿原 run 进入结果查询。
- 断言循环处理中保守回复区分“仍在求解”和“求解完成、内部阶段处理中”。
- 断言前三次连续模型失败分别为 `READY(1)`、`READY(2)`、`FAILED(3)`，第四次刷新不再调用模型；切换到新步骤时计数清零。

### 7. Wrong vs Correct

```python
# Wrong：真实 Job 一结束就伪造全部内部阶段完成
while current != target:
    record_python_stage(status='SUCCEEDED')
    current = guard.advance(...)

# Correct：先持久化恢复点，每次模型只选择当前步骤的一个工具
set_loop_state(status='READY', wake_reason='JOB_SUCCEEDED')
resume_persistent_job_stage(run, artifact_ids=artifact_ids)
```

## Harness 模型思考与格式修复契约

### 1. Scope / Trigger

适用于 `WORKFLOW_HARNESS` 的每轮 LLM 调用，包括首次路由、多轮澄清、审批理解和只读追问。结构化 JSON 专用的旧接口继续关闭思考，避免思维文本污染 JSON。

### 2. Signatures

```python
OpenAICompatiblePlanner.run_harness_turn(...) -> HarnessModelTurn
OpenAICompatiblePlanner._parse_harness_response(response) -> HarnessModelTurn
```

环境变量：`MOMO_LLM_TIMEOUT_S` 默认 `120`；`MOMO_LLM_HARNESS_THINKING=true|false`，默认 `true`；`MOMO_LLM_HARNESS_MAX_TOKENS=1600..32768`，默认 `4096`。单条消息仍由 `MOMO_AGENT_MESSAGE_BUDGET_S`（默认 `300`）限制总耗时。

### 3. Contracts

- LLM 结合完整历史把自然语言语义转换为公开工具 Schema ID；Python 不做关键词路由，也不静默改写工具参数。
- `<think>...</think>` 和供应商 `reasoning_content` 属于模型内部推理，不进入普通消息、工具轨迹或前端。
- 非法工具参数 JSON 由 Planner 反馈格式错误；合法 JSON 的 Schema/语义错误由 Harness 作为 tool result 反馈。两层均最多允许两次修复。
- 修复后的参数仍必须通过相同 Pydantic Schema、WorkflowGuard、审批和证据门禁。
- 首次请求可以携带供应商思考扩展 `enable_thinking` 和
  `chat_template_kwargs`；若兼容网关返回 HTTP 400，Planner 必须自动重试一次
  严格 OpenAI 载荷，移除这些扩展。空工具目录同时不得发送
  `tool_choice=auto` 或 `parallel_tool_calls`。
- HTTP 错误详情只保留响应正文的有限片段写入 `LLMUnavailableError.detail`；用户文案
  只能展示再次截断且脱敏后的诊断，API Key 和完整请求体不得进入日志、轨迹或前端。
- 每次只读工具成功后，工具结果都作为 `tool` 消息回送给模型；下一回合模型可以据此直接回答，普通最终回答不需要再次调用查询工具。仅当最终模型回合超时、不可用或数字证据不合格时，Harness 才以已持久化工具结果 `narrativeMode=DETERMINISTIC` 收尾。
- 对明确的 TOPSIS 前 N 项提问，若本轮模型没有工具调用且全局目录恰好只有一份可用优化摘要，可补发该唯一 `result.topsis` 调用；补发调用仍须持久化 `READ_ONLY` 工具轨迹和原始结果。存在多份候选时不得按最近时间自动选择。

### 4. Validation & Error Matrix

| 条件 | 行为 |
| --- | --- |
| 工具 arguments 不是合法 JSON | `TOOL_ARGUMENTS_INVALID_JSON` 反馈模型并重试 |
| 工具参数不满足 Schema/语义 | `INPUT_VALIDATION_ERROR` tool result 反馈模型并重试 |
| 连续三次仍无效 | `LLM_INVALID_RESPONSE` 或结构化 Harness 失败 |
| 模型返回 `<think>` 标签 | 剥离内部思考，只保留最终 content |
| 显式关闭思考 | 发送 `enable_thinking=false`，其他 Guard 不变 |
| 兼容网关对供应商扩展返回 HTTP 400 | 删除思考扩展后重试一次严格 OpenAI 载荷 |
| 网关返回 HTTP 400/401/403 | 保留有限响应正文作为诊断 detail，并按状态码分类 |
| 连接或服务错误带有 detail | 用户文案展示截断/脱敏后的诊断，不暴露认证信息 |
| `result.topsis` 已成功 | 持久化后回送模型；模型基于该结果给出最终回答时不要求再次调用工具 |
| 工具已经成功、最终模型回合超时或不可用 | 保留已持久化结果并以确定性答案完成查询 |
| 模型未调用工具且 TOPSIS 候选唯一 | 持久化补发的 `result.topsis` 轨迹后完成查询 |
| 模型未调用工具且 TOPSIS 候选多份 | 保持 `INQUIRY_EVIDENCE_REQUIRED`，不能自动猜选来源 |

### 5. Good / Base / Bad Cases

- Good：用户说“OpenSees、梁端位移和塔底内力”，模型选择 `OPENSEESPY_INPROC` 及对应三个响应 ID。
- Base：供应商不支持思考时通过环境变量关闭，仍使用原生 tool call。
- Bad：解析失败后直接要求用户重说，或把 `<think>` 内容显示在聊天窗口。

### 6. Tests Required

- 断言 Harness 默认开启思考、预算为 4096，且可显式关闭。
- 断言截断的工具 JSON 会在第二轮修复为合法调用。
- 断言澄清意图缺字段时模型可根据 tool result 修复。
- 断言 `<think>` 内容不会出现在 `HarnessModelTurn.content`。
- 断言严格网关 400 后的降级请求仍保留工具 Schema，并移除供应商扩展字段。
- 断言 HTTP 错误 detail 包含有限网关正文且不包含认证头。
- 断言默认单次 LLM 超时为 120 秒，并仍受消息总预算夹限。
- 断言 `result.topsis` 成功后，第二轮模型能使用其工具结果直接回答而无额外查询；模型漏调工具时仅在唯一候选下补发且留下审计轨迹。

### 7. Wrong vs Correct

```python
# Wrong：禁用推理并在第一次格式错误时终止
payload['enable_thinking'] = False
raise LLMUnavailableError('HARNESS', 'LLM_INVALID_RESPONSE')

# Correct：内部推理开启，格式错误受控反馈，最终参数仍严格校验
payload['enable_thinking'] = True
messages.append(format_correction)
start = WorkflowStartInput.model_validate(call.arguments)

# Correct：只有兼容网关拒绝扩展时，才重试标准载荷
strict_payload = remove_vendor_extensions(payload)
```

```python
# Wrong：模型已看到 TOPSIS 工具结果，却要求它在最终回答时再查询一次。
if not final_turn.tool_calls:
    fail_inquiry('INQUIRY_EVIDENCE_REQUIRED')

# Correct：已有工具证据时，普通最终回答可直接使用证据；仅异常时确定性回退。
return self._complete_native_inquiry(..., answer=final_turn.content, narrative_mode='LLM')
```

## 工具边界与错误契约

### 1. Scope / Trigger

原生 `tool_calls`、结果追问和审批回复跨越 LLM、Python Guard、Typed Registry、持久化和前端，所有边界必须返回结构化结果。

### 2. Signatures

```python
TypedToolRegistry.execute(name, payload, *, approved=False, idempotency_key=None)
    -> BaseModel
WorkflowHarnessMixin._dispatch_harness_approval_reply(...)
    -> dict[str, Any]
WorkflowHarnessMixin._authorize_approved_execution(
    repository, run, *, idempotency_key, effective_arguments=None
) -> str | None
```

### 3. Contracts

- `_HARNESS_TOOL_SPECS` 是全部工作流工具合同的唯一事实源，记录 description、Pydantic 输入模型和幂等键来源；其名称集合必须与所有工作流 `allowedTools` 的并集完全一致。模型目录只暴露 `_MODEL_INVOCABLE_TOOLS` 子集。
- 工具目录按名称排序且每个工具都有 `additionalProperties=false` 的受限 JSON Schema；结果查询输入使用 camelCase alias，同时保留 Python snake_case 属性访问。
- `result.columns`、`result.peak`、`result.at_time`、`result.correlate`、`result.compare` 只能接受来源 run 白名单中的 CSV 制品；`result.topsis` 只接受来源 run 白名单中的优化摘要 JSON。差值、比值和相对变化统一由 `result.compare` 返回，TOPSIS 排名直接读取已登记摘要，不重新求解。
- 一个工具名只能对应一套语义和输入合同：`result.compare` 专用于只读结果追问，输入为 `artifactId + columns`；阻尼器工程阶段使用 `comparison.compare`，输入为 `runId + jobId`。
- `solver.capabilities` 只保留为 Python 内部通用能力，不向模型目录公开；工程证据步骤使用各自的 `analysis/comparison/optimization.review`，通用 `evidence.verify` 仅用于 `RESULT_INQUIRY`。
- Registry 对模型已提供字段逐层比较校验前后值与类型：任何强制转换、大小写修正或字段丢弃都拒绝执行。调用方传入的已构造 `BaseModel` 仅按 JSON alias 展开，不视为规范化。
- `agent_tool_calls.arguments`/`argumentsSha256` 保存模型原始参数，`effectiveArguments`/`effectiveArgumentsSha256` 保存实际执行参数；Solver 轨迹还保存 `frozenActionSha256` 和 `idempotencyKeySource=SERVER_DERIVED`。
- handler 抛出的 `ResultInquiryError`、`LLMUnavailableError` 或其他业务异常必须转为 `ToolExecutionError`，不得越过 API 形成 HTTP 500。
- 审批回复在 `WAITING_APPROVAL` 步骤调用 `approval.decide`，通过 Guard 后才调用 `decide_approval`。
- `WAITING_APPROVAL -> FAILED` 必须是合法状态转换。审批重建应先准备新审批并保存新 run，再把旧审批标记为 `SUPERSEDED`；准备失败时旧 `PENDING` 审批仍保持可操作，run 可安全落到 `FAILED`。
- 没有 `runtimeMode=WORKFLOW_HARNESS` 和 `workflowSnapshot` 的旧审批/澄清 run 必须回到兼容处理，不得强行读取 Harness 快照。
- 原生 assistant/tool 协议消息持久化到消息表供跨请求恢复，但 `HARNESS_TOOL_CALL`/`HARNESS_TOOL_RESULT` 不直接展示在普通聊天窗口。
- AUTO 消息命中保守的明确结果查询表达（如“给出所有统计量峰值”），且全局存在已验证终态结果时，直接进入 `RESULT_INQUIRY`，不得再次让启动路由模型用文字澄清；不明确的新工程请求仍经过 `workflow.start`。
- 结果查询的模型答案连续两次未通过数字证据校验时，不得把已成功完成的只读查询整体标记失败；改用工具原始输出生成确定性表格，并以 `narrativeMode=DETERMINISTIC` 标记。
- 结果查询中模型仍负责从 `availableResults`/`catalogsByRunId` 选择目标历史 run；`artifactBindings` 为每个 run 提供带作用域的登记制品映射，不能按关键词或最近时间由 Python 预选 TOPSIS 摘要。模型已成功调用只读工具后，若最终叙述回合发生 `LLM_TIMEOUT`，Harness 必须保留已查询的工具结果并以 `narrativeMode=DETERMINISTIC` 完成查询，不得丢弃结果。

### 4. Validation & Error Matrix

| 条件 | 错误码/行为 |
| --- | --- |
| schema 参数错误 | `INPUT_VALIDATION_ERROR` |
| 输入校验将改变已提供值、类型或裁剪字段 | `INPUT_VALIDATION_ERROR`，handler 不执行，details 给出 `provided`/`effective` |
| handler 输出将被强制转换或裁剪 | `OUTPUT_VALIDATION_ERROR`，不向模型返回被修改结果 |
| handler 业务异常 | `TOOL_HANDLER_FAILED` |
| 未登记制品 | `ARTIFACT_NOT_REGISTERED` |
| 审批回复越过当前步骤 | `WORKFLOW_STEP_VIOLATION` |
| 审批重建预检失败 | run 落盘为 `FAILED`；旧审批不得提前变为 `SUPERSEDED` |
| 工具副作用无幂等键 | `IDEMPOTENCY_KEY_REQUIRED` |
| 工作流工具缺少 Spec 或存在多余 Spec | 工具目录构建时抛出 `RuntimeError`，不得带漂移合同运行 |
| 取消/拒绝 | 游标迁移到 `CANCELLED`，并清除 `pendingApprovalId` |
| Job 成功但缺少制品 | `WORKFLOW_GATE_FAILED`，停留在当前阶段并记录 `workflowGateError` |

### 5. Good / Base / Bad

- Good：查询使用 `result.compare(artifactId, columns)`；Python Job 工程阶段登记 `comparison.compare(runId, jobId)`，两者名称、描述和 Schema 均独立。
- Base：模型省略 Schema 声明的可选默认字段时，Pydantic 可填充默认值，但返回模型的工具结果必须包含 `effectiveArguments`。
- Bad：让工程阶段复用 `result.compare` 并登记 `{runId, jobId}`，而该名称向模型声明的却是 `{artifactId, columns}`。

### 6. Tests Required

- 工具目录稳定排序、Schema 非空、`additionalProperties=false`，并断言目录与工作流登记名称无漂移。
- 阻尼器 `COMPARISON` 步骤只允许 `comparison.compare`；其 `HarnessJobInput` 必须接受且仅接受 `runId + jobId`，同时 `result.compare` 保持查询合同。
- `SERVER_DERIVED` 元数据必须保留，面向模型的所有 description 不得自动追加幂等键说明。
- 所有公开和 Registry 工具 description 包含使用触发条件；高重叠的 `result.delta/ratio` 与公开 `solver.capabilities` 不得重新出现。
- Registry 必须拒绝字符串到整数的输入强制转换和 handler 额外输出字段裁剪，分别断言 `INPUT_VALIDATION_ERROR`、`OUTPUT_VALIDATION_ERROR`。
- 工程意图必须拒绝中文响应标签、字符串布尔值和小写审批决定，不得在执行前修正。
- 工具轨迹必须同时断言原始参数、实际执行参数、两个 SHA256 与 `SERVER_DERIVED` 幂等来源。
- 原生历史保留 assistant `tool_calls` 和 tool `tool_call_id`。
- 结果查询 handler 异常断言 `TOOL_HANDLER_FAILED`。
- 审批回复只能由 `approval.decide` 在 Guard 允许时生效。
- 缺失工作流快照的状态查询必须返回 `WORKFLOW_STEP_NOT_FOUND` 结构化错误。
- 审批重复提交复用同一工具调用，取消/拒绝后的装饰读取不能改回等待态。
- 审批更新测试必须在准备回调内断言旧审批仍为 `PENDING`，并覆盖 `WAITING_APPROVAL -> FAILED`。
- 默认测试运行时为 `WORKFLOW_HARNESS`；需要兼容旧路由的测试必须显式声明 `LEGACY`。

### 7. Wrong vs Correct

```python
# Wrong：同名工具在两个步骤承载不兼容参数
comparison_step = WorkflowStep(allowedTools=('result.compare',))
stage_arguments = {'runId': run_id, 'jobId': job_id}

# Correct：按使用场景拆名并绑定唯一类型合同
comparison_step = WorkflowStep(allowedTools=('comparison.compare',))
spec = HarnessToolSpec(
    description='当两个阻尼器真实案例完成后使用……',
    input_model=HarnessJobInput,
    idempotency_key_source='SERVER_DERIVED',
)
```
