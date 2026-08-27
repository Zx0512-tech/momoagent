# 独立代理/主动学习设计

复用 `pyansys_bridge.surrogate` / `active_learning` 与现有 Agent 优化链执行器，不在 `PlatformStore` 复制训练。独立 API 的 typed request 必须带登记数据集/模型 Artifact ID 和冻结预算。Worker 生命周期与 solver 子任务相同：排队、心跳、取消、失败关闭。
