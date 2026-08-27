# 智能体流程进度规范

- 对话中显示当前步骤、已完成数量、总步骤和等待状态。
- 工具轨迹只显示工具名、状态、简要错误和制品引用，不展示模型思维过程。
- 审批继续作为历史对话消息存在，不覆盖旧审批。
- 状态不能只依赖颜色，必须提供中文文本和图标。
- 未提供流程字段的旧 run 保持原有渲染。

## 快照阶段投影契约

- `ProgressCard` 只有在 `currentStep` 属于 `workflowSnapshot.steps` 时才直接使用它。
- 快照外游标或旧 run 必须通过 `WAITING_JOB -> BASELINE/EXECUTION`、`REVIEWING -> REVIEW/EVIDENCE_REVIEW` 等安全投影显示，不能让 `indexOf` 为 `-1` 导致进度条空白。
- `completedSteps` 只消费快照内的步骤；没有该字段的旧 run 才允许使用阶段索引作为兼容 fallback。
- 终态和快照阶段标题来自同一快照，工具轨迹只显示工具名、状态和短错误，不显示模型内部思维。

### 验收

- FULL_OPTIMIZATION 的旧 `EXECUTION` 游标仍显示 `BASELINE` 或下一合法阶段。
- ANALYSIS 的 Guard 游标被读取装饰后不会从完成勾选退回。

## 结果追问渐进展示契约

### 1. Scope / Trigger

适用于聊天页发送消息后，后端通过 NDJSON 返回已有结果查询的 `run/progress/complete` 快照。此展示是只读结果访问，不复用求解进度卡的工作流步骤含义。

### 2. Signatures

```typescript
agentApi.streamMessage(..., onEvent): Promise<AgentRun>
type AgentMessageStreamEvent = Accepted | Run | Progress | Complete | Error
ResultCard({ run }: { run: AgentRun }): JSX.Element | null
```

### 3. Contracts

- `chatStore.send` 在每个 `run/progress/complete` 事件到达时立即更新当前 run；仍须用请求版本和 `sessionId` 防止切换会话后的迟到写入。
- NDJSON 解析必须支持 JSON 行跨浏览器数据块拆分，流结束前没有 `complete` 时形成可见错误。
- 查询处于 `PLANNING` 且已有 `inquiryMetrics` 时直接渐进渲染 `ResultCard`；尚无指标时显示“正在读取已完成结果”，不能显示求解阶段的通用文案。
- 结果查询卡以逐条项目符号展示中文指标名、经唯一元数据表换算的工程单位峰值和发生时刻；原始 N/N·m 应分别转为 MN/GN·m，不能显示未格式化的长浮点数。
- 峰值位移、加速度等保留至多 6 位小数，剪力 3 位小数、弯矩 4 位小数；时间去掉无意义尾零。
- `narrativeSummary`、`message` 和结构化指标不能重复显示同一批数据；有结构化指标时禁止渲染旧 Markdown 表。
- 查询卡不显示“未通过真实 FEM 门槛”的求解诊断警告；但必须保留来源结果的工况、模型、求解器、阻尼器及参数标记。
- DOM 使用 `aria-live=polite` 与 `aria-busy` 暴露渐进读取状态，不能只靠徽标颜色表达。

### 4. Validation & Error Matrix

| 条件 | 行为 |
| --- | --- |
| 收到 `accepted` | 保持忙碌态，不创建假指标 |
| 收到第一个 `progress` | 立即显示一条结果及“读取中”徽标 |
| 收到 `complete` | 替换为终态 run，显示“只读结果” |
| JSON 行跨 chunk | 缓冲到换行后再解析，不丢事件 |
| 流无 `complete` 或 `error` | 结束忙碌态并显示错误 |
| 历史 run 已投影指标 | 直接用相同结果卡，不显示原 Markdown |

### 5. Good / Base / Bad Cases

- Good：`最大塔底剪力：52.300 MN（发生时刻 22.4 s）`，后续指标逐项追加。
- Base：读取中只有状态文字，收到首项后原位置替换为结果列表。
- Bad：显示 `52299849.63715227`、竖线 Markdown、重复两遍结果，或一直等最终模型回答才更新。

### 6. Tests Required

- API 测试把同一 NDJSON 行拆成多个 chunk，断言事件仍按顺序回调。
- Store 测试在 `complete` 未决时先发 `progress`，断言 run 已更新。
- 卡片测试断言中文标签、工程单位、精度、项目符号和时间格式。
- 卡片测试断言不出现原始大数、内部校验文案、旧 Markdown 和查询诊断警告。

### 7. Wrong vs Correct

```tsx
// Wrong：把后端 Markdown 当正文再次渲染
<p>{run.resultSummary?.narrativeSummary}</p>

// Correct：以结构化指标渐进渲染，并让旧叙述退出主展示
<ResultCard run={progressEvent.run} />
```

## 异步会话一致性

- `switchSession` 必须立即更新活动会话，并用请求版本丢弃乱序返回的旧会话详情。
- `switchSession` 清空旧会话内容后必须显示明确的会话加载状态，不能在慢请求期间呈现无反馈的空白线程，也不能把旧会话消息显示在新的活动会话标题下。
- `send`、映射、审批、取消和轮询在写入 Zustand 前必须复核捕获的 `sessionId/runId`。
- 同一 run 的轮询必须 single-flight：前一个 `refreshAgentRun(runId)` 未结束时不得发起第二个同 `runId` 请求；请求版本继续负责丢弃跨 run、跨会话的迟到响应。
- 新建或切换会话必须使旧轮询失效，迟到错误也不能覆盖新会话的错误状态。
- 测试必须使用延迟 Promise 复现“轮询 A → 切换 B → A 返回”，并断言 B 的 run 不变。

## 对话中的结果历史

- 结果展示必须挂在带有相同 `runId` 的助手消息下，而不能只挂在当前会话最后一条消息下。
- `chatStore` 必须维护当前会话的 `runHistory` 快照；新追问、模型错误或轮询刷新不能清除已经展示过的结果。
- 会话详情返回的 runs 只允许合并更新，不能用不完整的旧服务端响应覆盖已有快照。
- 历史结果继续使用与当前结果相同的结构化 `ResultCard`；查询指标已存在时，不再重复渲染后端旧 Markdown 叙述。
- 审批摘要只在 `ApprovalCard` 中渲染一次；承载审批的助手消息正文必须留空，不能同时显示原始摘要。
- 工程证据细节不另起卡片；已完成结果的制品下载以内联、可换行列表展示，并优先使用服务端返回的文件语义名称。

## 场景：同一运行的 single-flight 轮询

### 1. Scope / Trigger

- `ChatPage` 的定时器、用户操作或状态更新可能在上一次请求结束前再次调用 `refreshRun(runId)` 时适用。

### 2. Signatures

```typescript
type ChatStore = {
  refreshRun: (runId: string) => Promise<void>
}
```

### 3. Contracts

- 模块内保存当前进行中的 `runId`；同一 `runId` 的重入调用立即返回，不能再次请求 API。
- 请求开始前登记进行中标记；`finally` 只清理由本次请求持有的同一标记。
- 不同 run 和会话切换仍由既有 `sessionId/runId` 复核与单调请求版本防止迟到写入。
- 成功、失败和异常都必须释放 single-flight 标记，使下一轮定时刷新可以继续。

### 4. Validation & Error Matrix

| 条件 | 行为 |
| --- | --- |
| 同一 run 的第二次刷新重入 | 立即返回，API 调用总数保持 1 |
| 首次刷新成功或失败 | 在 `finally` 释放标记 |
| 请求期间切换会话 | 迟到响应不得写入新会话 |
| 后续定时器再次刷新 | 前次已结束时允许发起新请求 |

### 5. Good / Base / Bad Cases

- Good：2 秒定时器与一次手动刷新重叠，同一 run 只有一个后端终态处理请求。
- Base：上一轮结束后，下一轮正常刷新最新进度。
- Bad：仅比较响应版本但仍并发请求；即使前端丢弃旧响应，两个后端请求仍可能同时触发终态副作用。

### 6. Tests Required

- 使用未决 Promise 同时调用两次 `refreshRun(runId)`，断言 `getAgentRun` 只调用一次。
- 解析该 Promise 后再次刷新，断言调用数增加，证明标记已释放。
- 保留“轮询 A → 切换 B → A 返回”的迟到响应测试，断言 B 的状态和错误不被覆盖。

### 7. Wrong vs Correct

```typescript
// Wrong：版本号只能阻止迟到写入，不能阻止后端并发副作用。
const requestVersion = ++refreshRequestVersion
await getAgentRun(runId)

// Correct：同一 run 先 single-flight，再用版本号保护跨会话写入。
if (refreshInFlightRunId === runId) return
refreshInFlightRunId = runId
try {
  await getAgentRun(runId)
} finally {
  if (refreshInFlightRunId === runId) refreshInFlightRunId = null
}
```

## 场景：任务丢失与等待审批的可恢复出口

### 1. Scope / Trigger

- 平台 Job 轮询返回错误，或 Agent run 停在 `WAITING_APPROVAL` 时适用。
- 目标是让后端丢失/失败可见，并确保用户始终能从等待审批状态取消运行。

### 2. Signatures

```typescript
jobStore.startJobPolling(jobId: string, intervalMs?: number): void
ProgressCard({ run, busy, onCancel }): JSX.Element
```

### 3. Contracts

- `getJob` 轮询异常必须写入 `jobStore.error`；错误为 `ApiClientError(404)` 时同时停止该 Job 的 poller 并从 `activeJobs` 移除。
- 错误消息优先使用 `Error.message`，非 Error 值使用“读取任务状态失败”。不得只写 `console.error`。
- `ProgressCard` 在 `WAITING_APPROVAL`、`WAITING_JOB`、`REVIEWING` 三种状态渲染“取消运行”。
- `WAITING_APPROVAL` 不需要加入自动轮询集合；取消按钮直接调用既有 cancel API，避免为了显示出口制造永久轮询。
- 结果卡的制品元数据允许为空；展示下载入口时必须回退到 `artifactIds`，不能因为空数组而只留下空标题。
- 时程曲线始终使用时间列作为横坐标，响应列按位移、加速度/塔底内力、阻尼器、地震输入分组并使用中文语义标签。
- 下载项主标题必须说明文件对应的工程结果（如综合响应时程、塔底剪力明细），文件类型作为辅助标签，不能把内部 artifact ID 直接作为用户可读名称。
- baseline-first 优化的无控基线与受控 DOE 统一展示为一个“求解计算”阶段；总量必须包含基线 1 个算例和 DOE 算例。批量求解只要仍处于运行态或存在活动算例，主进度最高显示 99%。

### 4. Validation & Error Matrix

| 条件 | 行为 |
|---|---|
| Job 轮询 404 | 展示服务端消息、停止 poller、清理 `activeJobs[jobId]` |
| Job 轮询其他 Error | 展示消息，保留现有重试策略 |
| Job 轮询抛非 Error | 展示通用错误文案 |
| run=`WAITING_APPROVAL` | 显示可点击取消按钮 |
| run 已终态 | 不显示取消按钮 |

### 5. Good / Base / Bad Cases

- Good：作业因竞态缺失时，用户看到“任务不存在”，而不是进度卡无声消失。
- Base：运行等待审批时不主动轮询，但用户可随时取消并由 API 返回终态。
- Bad：404 只打印控制台并停止轮询，或后端支持取消但 UI 在审批状态隐藏按钮。

### 6. Tests Required

- mock `getJob` 抛结构化 404，断言 `error`、空 `activeJobs` 和停止 poller。
- 静态渲染 `WAITING_APPROVAL` 的 `ProgressCard`，断言包含“取消运行”。
- 保留终态停止轮询、取消后同步服务端状态的现有回归。

### 7. Wrong vs Correct

```typescript
// Wrong：错误只存在于开发者控制台。
console.error(err)
if (err.status === 404) stopJobPolling(jobId)

// Correct：先形成用户可见状态，再停止丢失任务的轮询。
set({ error: err instanceof Error ? err.message : "读取任务状态失败" })
if (err instanceof ApiClientError && err.status === 404) stopJobPolling(jobId)
```
