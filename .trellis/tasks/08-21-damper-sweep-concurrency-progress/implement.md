# 实施计划：阻尼器参数扫描并发与进度

## 1. 先写回归测试与进度别名接缝

- 为 OpenSeesPy solver 的 `progress_case_id` 增加测试：显式业务 ID 覆盖内部指纹，缺省行为保持不变。
- 为参数扫描的意图/API/合同默认并发数增加测试，断言未传值时冻结为 4。
- 为进度聚合增加业务案例 ID / 多活动案例场景测试。
- 文件：`pyansys_bridge/core/openseespy_inproc_solver.py`、相关 backend progress tests。
- 验证：定向 pytest 先失败，再在实现后通过。

## 2. 将 OpenSeesPy 扫描改为 ProcessPoolExecutor

- 提取模块级、可 pickle 的真实 case worker；父进程继续拥有 config 创建、future 汇总、Artifact 登记和 `_batch.json` 写入。
- 对 OpenSeesPy 选择 ProcessPoolExecutor；ANSYS 保持当前 ThreadPoolExecutor 和许可证逻辑。
- 将 `maxConcurrentCases` 的全部默认和审批更新回退值统一为 4，保留 1–8 校验和案例数收敛。
- 每个 OpenSeesPy case 配置写入同一 progress 目录及其冻结业务 `caseId`。
- 更新 `parallelism` 审计字段，记录 `process_pool` 与有效并发数。
- 文件：`momo_agent/backend/app/services/platform_store.py`、`tests/test_damper_sweep_license.py`（或同职责测试文件）。
- 验证：伪 worker / ProcessPool 选择测试证明 4 个 OpenSeesPy case 的 effective 并发为 4；非许可证错误仍失败关闭；现有 ANSYS cap/retry 测试继续通过。

## 3. 修正进度卡的重复口径

- 批量 Job 只显示一次完成计数，并保留活动案例列表和单案例百分比；非批量 Job 继续显示后端 message。
- 增加 SSR 测试，断言“已完成 X/Y”不会因 message 和结构化字段重复出现，并保留业务案例 ID。
- 文件：`platform-ui/src/pages/chat/cards/ProgressCard.tsx`、`platform-ui/src/pages/chat/cards/cards.test.tsx`。
- 验证：`npm.cmd test -- --runInBand`（定向 cards 测试），随后 `npm.cmd run build`。

## 4. 质量门与端到端复核

- 运行受影响 backend pytest、`python -m compileall` 和前端测试/构建。
- 执行 4 案例 OpenSeesPy smoke：运行期间能同时看到至多 4 个业务 case ID，主进度遵循批次口径，取消/超时仍终止完整进程树。
- 复查 summary 的 `parallelism`、输出 manifest 与进度目录，确认没有残留内部 case ID 或重复完成计数。

## Rollback Point

若 OpenSeesPy process smoke 出现全局状态、资源或进程回收异常，恢复为单 worker 串行执行并保留本次前端重复文案/案例 ID 修复；不发布 `process_pool` 行为。
