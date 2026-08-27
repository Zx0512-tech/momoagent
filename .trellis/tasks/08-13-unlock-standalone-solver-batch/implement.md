# 实施清单

- [ ] 将独立 `SOLVER_BATCH` 请求接到共享 executor，补齐 typed request。
- [ ] 独立 `RESULT_EXTRACTION` 只消费 output manifest → `result_catalog.json`。
- [ ] 对等测试、取消/超时、失败关闭、OpenSeesPy smoke。
- [ ] 通过后门改 registry：`SOLVER_BATCH/PLATFORM_API` 与 `RESULT_EXTRACTION/PLATFORM_API` 升为 LIVE。
- [ ] 定向 pytest + `compileall`；失败则回滚为 DISABLED/501。

依赖：`08-13-isolate-platform-placeholders`。
并行：不可与 surrogate 解锁并行改同一 registry 项，可在本任务归档后再开始下一子任务。
