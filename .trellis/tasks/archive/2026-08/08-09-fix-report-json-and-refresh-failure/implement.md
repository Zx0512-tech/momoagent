# 实施清单

1. [x] 在现有 Agent 刷新测试中加入非法 JSON 和三类异常重放失败用例。
2. [x] 增加 `_coerce_floats()` 与严格 JSON 内容生成，替换四个报告写入点。
3. [x] 增加 `_refresh_agent_run_once()` 和不可重放的失败收敛包装器。
4. [x] 运行定向 pytest，并用 Node `JSON.parse` 解析测试生成的实际报告文本。
5. [x] 运行后端完整 pytest、compileall、`verify_submission.py`，更新提交清单并审查 diff。

风险文件：`agent_service.py`。回滚时整体撤销该子任务提交；不得只撤销 `allow_nan=False` 而保留未清洗 preview。
