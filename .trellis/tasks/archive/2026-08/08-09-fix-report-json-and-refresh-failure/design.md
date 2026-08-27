# 技术设计

## 标准 JSON 边界

在 `agent_service.py` 增加模块内纯函数：

```python
def _coerce_floats(value: Any) -> Any:
    """递归把非有限浮点转成 None，其余 JSON 值保持原样。"""
```

- 使用 `math.isfinite()` 判断 float。
- dict 保持键和值结构，list 保持顺序，tuple 输出为 list 以匹配 JSON 语义。
- 每次 `JSON_SUMMARY` 注册前先得到 `safe_report`，同一对象同时用于 `preview` 和严格序列化内容。
- `allow_nan=False` 是遗漏清洗分支时的第二道失败关闭门。

## 刷新失败边界

保留公开 `_refresh_agent_run()`，把现有主体移动到 `_refresh_agent_run_once()`：

1. 如果 run 已带 `workflowGateError.code=REPORT_GENERATION_ERROR`，直接返回，阻止确定性重放。
2. 调用一次现有刷新主体。
3. 捕获异常后使用结构化日志记录堆栈和 `job_id`。
4. 将 run 收敛到 `FAILED`，设置安全的 `workflowGateError`，保留已有进度并持久化。

错误对象：

```json
{
  "code": "REPORT_GENERATION_ERROR",
  "message": "结果验收或报告生成失败，运行已安全终止。",
  "details": {
    "stage": "REVIEWING",
    "exceptionType": "RuntimeError"
  }
}
```

不把 `str(exc)`、堆栈或本地路径写入 run。终态失败直接落到冻结快照已有的 `FAILED`，不补写未执行步骤。

## 测试设计

- 纯函数单测覆盖有限/非有限值和嵌套容器。
- 集成测试让真实报告注册路径接收非有限数据，检查 preview、原始字节、SHA 和严格解析。
- 参数化注入 review/build/register 三处异常；第二次 `get_run()` 验证不重放。
- 手工验收命令使用 Node 对实际制品文本执行 `JSON.parse`，不是只依赖 Python 默认解析器。
