# 实施清单

- [ ] 导出当前 registry 与代码路径对照表。
- [ ] 处理 `DAMPER_COMPARISON` × OpenSees：登记或下架。
- [ ] 从 LIVE/可广告列表移除无物理模型的 WIND/TRAFFIC/组合。
- [ ] 补齐已广告组合的 smoke / 失败关闭 / 取消测试缺口。
- [ ] 更新 `.trellis/spec/backend/real-execution-capabilities.md`。

依赖：不阻塞占位隔离；与 501 解锁顺序上建议在 solver 解锁之后，避免目录刚 LIVE 又改矩阵。
