# 技术设计

## 边界

生产智能体采用三层：模型只提出下一动作；`WorkflowGuard` 对当前步骤和前置条件授权；`TypedToolRegistry` 负责 Schema、审批、幂等和 handler 执行。Trellis 只约束开发过程，不进入生产运行时。

## 数据流

1. 用户消息创建或恢复 run。
2. Harness 从 Python 注册表冻结 `WorkflowDefinition`，保存版本与 SHA256。
3. 模型收到静态系统提示、固定工具数组、对话历史和尾部 `workflowState`。
4. 模型返回最终文本或 `tool_calls`。
5. Guard 校验步骤、前置条件、尝试次数和审批；Registry 校验参数并执行。
6. 工具结果更新 `agent_tool_calls`、run 步骤和下一 `workflowState`。
7. 长任务进入 `WAITING_JOB`，Job 终态后通过幂等恢复继续推理。

## 兼容性

- 现有会话、消息、run 和审批 URL 保持不变。
- run 响应新增流程字段和 `toolCalls`，旧客户端可忽略。
- `MOMO_AGENT_RUNTIME` 控制双轨，完成验收前不删除旧路由。

## 缓存策略

- 系统提示词为模块常量，运行时不插值。
- 工具按名称排序，使用确定性 JSON Schema。
- 动态工作流状态只添加到最新用户包装或工具结果。
- 缓存统计缺失不影响执行。

## 风险与回滚

- 新运行时先通过开关启用；旧运行时保持可用。
- Solver 调用以冻结参数 SHA256 和幂等键去重。
- 工作流快照持久化，部署升级不改变进行中的 run。
