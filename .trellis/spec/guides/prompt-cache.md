# Prompt Cache 规范

- system prompt 必须是静态常量，不插入当前步骤或时间。
- 工具数组按名称稳定排序，同一工具的 Schema 字段顺序保持确定。
- 对话上下文采用只追加模式；动态状态放在最新用户包装或工具结果。
- 记录网关返回的 `cached_tokens`；字段缺失时使用 `null`。
- 缓存只用于性能优化，权限和流程正确性不得依赖缓存命中。
- 参数校验错误以工具结果追加到消息尾部，最多允许模型修正两次，不修改历史前缀。
- OpenAI 兼容网关可能拒绝出现在 user/assistant 之后的任何 `system` 消息，并返回 `System message must be at the beginning`。
- Harness 请求保持唯一固定 system 在首位，随后保持原历史顺序，把 `workflowState`、`userContent` 和服务端生成的 `resultInquiryContext` 一起放进最后一条 user 包装载荷。这样既满足严格网关顺序，也保留最大可复用前缀。
- 不要把动态结果目录作为第二条 system：即使位置合法，结果变化也会使其后的全部历史缓存失效。

```python
# Wrong: dynamic system context invalidates the history prefix and may violate gateway ordering.
messages = [stable_system, *history, dynamic_result_system, current_user]

# Correct: stable system/history stay cacheable; dynamic state is last.
messages = [stable_system, *history, {
    'role': 'user',
    'content': json.dumps({
        'workflowState': workflow_state,
        'userContent': user_content,
        'resultInquiryContext': result_context,
    }),
}]
```

## 原生消息保留

- 存储消息投影到模型请求时必须保留 `role`、`content`、`tool_calls`、`tool_call_id`、`name` 等协议字段；不得只保留用户和助手纯文本。
- 纠错和工具结果只能追加新的 assistant/tool 消息，不能重写已发送历史。
- 新增工具时只追加稳定 Schema 到固定排序目录；流程授权由 Guard 控制，不通过删工具改变前缀。
