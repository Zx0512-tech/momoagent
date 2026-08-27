# 实施清单

- [ ] 独立 DOE 请求接到确定性生成器 + 真实求解批次。
- [ ] 独立训练：持久化模型字节/schema/CV；增加 reload 复现测试。
- [ ] 独立 infill：去重、预算、回 solver；取消/失败关闭。
- [ ] registry 将对应 `PLATFORM_API` 项改为 LIVE。
- [ ] 评估独立优化/TOPSIS/导出是否具备重算证据；不具备则保持 MOCK_ONLY/DISABLED。

依赖：`08-13-unlock-standalone-solver-batch`。
