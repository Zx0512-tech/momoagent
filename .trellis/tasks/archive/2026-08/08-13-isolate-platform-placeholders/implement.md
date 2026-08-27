# 实施清单

- [x] 定位 `platform_store.py` 中 placeholder / `PENDING_REPLACEMENT` 成功写入点并改为失败关闭或 Mock-only。
- [x] 删除或门禁前端 Live 自制 R²/RMSE/CV/FEM 队列。
- [x] 增加回归：Live 独立 Job 不得登记 `surrogate-model-placeholder` 等伪制品。
- [x] 跑 `pytest momo_agent/backend/tests/test_platform_api_v1.py test_real_execution_capabilities.py -q` 与受控 Agent 定向测试。

依赖：无。完成后才能开始 `08-13-unlock-standalone-solver-batch`。
