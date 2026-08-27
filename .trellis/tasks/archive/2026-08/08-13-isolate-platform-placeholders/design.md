# 隔离占位成功路径

## 边界

只改独立平台 Job 的成功/失败关闭语义。`RealExecutionRegistry` 状态保持 `DISABLED` / `MOCK_ONLY`，不放行 LIVE。

## 做法

1. 在 `platform_store.py` 把仍写入 placeholder 字节或 `PENDING_REPLACEMENT` 的成功分支改为结构化失败，或仅在明确 `executionMode=MOCK` 时生成带模拟标记的演示制品。
2. 前端独立 Solver / Surrogate / Optimization 页面删除“Job 成功后本地生成指标/队列”的 Live 路径。
3. 用现有 `test_platform_api_v1.py` 的 fail-before-create 测试扩展：即使内部 handler 被误调用，也不能落成功占位制品。

## 回滚

只关闭伪成功，不改变能力目录。回滚即恢复占位分支，因此本任务必须先合入再做 501 解锁。
