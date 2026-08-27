# 实施计划：批量时程对比与证据复核

## 1. 统一真实案例验证

- 先为嵌套 `solver_design.execution_mode`、dry-run 和缺失证据写失败回归。
- 在 `PlatformStore` 提取共享验证器，并替换参数扫描与阻尼器对比中重复的顶层字段读取。
- 验证：相关 pytest 先失败后通过；现有 dry-run 拒绝测试保持通过。

## 2. 重建获准的历史 Job 证据

- 实现仅针对已成功真实参数扫描的幂等重建路径：复核现有 manifest、Artifact 和 case summary，更新验证布尔值、摘要和 Agent 报告，不调用 solver。
- 对 `job_54edb4a16bc3` 执行一次该路径，并从 API 读取检查结果确认第 4、6 项通过。
- 验证：伪造已完成 Job 的重建测试；重建前后断言无求解器调用、Artifact 哈希和数值不变。

执行记录：`job_54edb4a16bc3` 的重建已完成并落库，`evidenceReverification.status=SUCCEEDED`，4 个工况 `isVerifiedSolverOutput` 全为 true、`allVerifiedExecution=true`，`supersededArtifactIds` 记录了被取代的 1 份汇总摘要与 4 份案例摘要。重建按一次性数据迁移执行，未暴露 HTTP 路由：`reverify_real_damper_parameter_sweep` / `reverify_damper_parameter_sweep` 仅保留服务层入口与回归测试，避免长期存在可把验证标记改写为 true 的端点。因此本步的"从 API 读取检查结果"仅通过既有 run/job 查询接口核对，触发侧不经 API。

## 3. 新增多工况时程比较 API

- 编写 API/服务测试，覆盖默认全部工况、指定多个 `caseIds`、不共同列、未验证工况和重复 `timeseries.csv` 文件名。
- 新增比较读取方法与路由，按 `caseId` 绑定已登记 Artifact，并复用 `ResultInquiryService` 的目录/哈希校验与降采样。
- 验证：后端定向 pytest 与 API 契约断言。

## 4. 前端多工况时程图

- 扩展 API 类型/客户端，保留旧单时程请求。
- 把时程区改成“响应量选择 + 参数工况选择 + 分响应量叠加图”；图例使用中文参数标签。
- 为选择器、图例和每响应量分图补充组件测试。
- 验证：前端测试、lint、生产构建；可用时进行浏览器交互检查。

## 5. 质量门与提交

- 运行受影响后端 pytest、后端全量 pytest、`compileall`、前端测试、lint 与构建。
- 通过 API 复核当前 Job 的 10 项门槛和比较时程返回的四个案例。
- 更新后端/前端代码规范，核对仅本任务文件后分批提交。

## 风险与回滚点

- 历史证据重建必须在任何元数据或哈希不一致时失败关闭；不可把未验证结果改写为通过。
- 多案例曲线必须限制到已批准的降采样点数，避免大批次导致浏览器渲染阻塞。
- 若比较 API 的 Artifact 绑定无法证明来源，保留原单案例时程功能并拒绝该比较请求。
