# 修复报告 JSON 与刷新失败关闭

## Goal

保证工程报告始终是浏览器可解析的标准 JSON，并保证 Job 终态后的验收/报告异常只执行一次、可审计地终止 run。

## Requirements

- F1：递归处理 dict、list、tuple 中的 float；有限值原样保留，`nan`、`inf`、`-inf` 转为 `None`。
- F2：`agent_service.py:1011/1135/1349/1729` 的预览和内容必须使用同一个清洗后对象，并用 `json.dumps(..., allow_nan=False)` 生成字节。
- F3：清洗发生在报告拥有者 `AgentService`，不得让 `PlatformStore.register_artifact()` 隐式改写所有调用方数据。
- F4：`_refresh_agent_run()` 捕获终态处理中的非业务预期异常，服务端记录堆栈，run 写入 `status/currentStage/currentStep=FAILED` 与结构化 `workflowGateError`。
- F5：失败时保留原有 `completedSteps`、Job 和已有 Artifact 引用，不把 `EVIDENCE_REVIEW` 或 `REPORT` 补入完成列表。
- F6：带 `REPORT_GENERATION_ERROR` 的失败 run 后续 `get_run()` 直接返回，不重复调用 review/build/register。
- F7：如果连 `repository.save_run()` 都失败，不得伪称失败已持久化；该基础设施错误允许继续由 API 错误边界暴露并记录。

## Acceptance Criteria

- [x] 嵌套 `nan/inf/-inf` 报告制品文本不含这些字面量，严格 Python 解析与实际 Node `JSON.parse` 均通过。
- [x] Artifact preview 与下载内容在相同路径上均返回 `null`，普通有限浮点、字符串、布尔和整数不变。
- [x] review、build_report 和 register_artifact 异常测试均不从 `get_run()` 逸出，run 被持久化为 `FAILED`。
- [x] 错误响应不含异常消息中的敏感路径；结构化 details 至少包含失败阶段和异常类型。
- [x] 连续两次读取失败 run，失败注入函数调用计数保持 1。
- [x] 正常 `SUCCEEDED`、`COMPLETED_DIAGNOSTIC`、Job `FAILED/CANCELLED` 路径不回归。

## Out of Scope

- 清洗平台状态数据库中的所有历史非有限浮点。
- 自动重试 review 或报告生成；避免重复副作用优先于自动恢复。
- 修改证据门禁的业务判定规则。
