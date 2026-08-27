# 实施计划与验收门

## 剩余子任务地图

父任务继续作为集成收口；未完成项已拆成子任务，按依赖顺序实施、单独归档：

| 顺序 | 子任务 | 交付 | 依赖 |
|---|---|---|---|
| 1 | `08-13-isolate-platform-placeholders` | 消灭独立入口伪成功 | 无 |
| 2 | `08-13-unlock-standalone-solver-batch` | 独立 SOLVER_BATCH / RESULT_EXTRACTION 解除 501 | 1 |
| 3 | `08-13-unlock-standalone-surrogate-learning` | 独立 DOE / 代理 / 主动学习解除 501 | 2 |
| 4 | `08-13-align-capability-scenario-matrix` | 目录与实现一致；无物理模型组合不下线广告 | 建议在 2 之后 |
| 5 | `08-13-browser-live-e2e` | 上传→审批→执行→结果→追问 | 建议在 4 之后 |

独立 `MULTI_OBJECTIVE_OPTIMIZATION` / TOPSIS / 导出若本轮证据不足，留在父任务最终收口，不阻塞 1–5。

下一步：`08-13-isolate-platform-placeholders` 已完成占位成功隔离。下一子任务 `08-13-unlock-standalone-solver-batch` 需单独确认后再开始。

## 执行原则

- 先测试后实现；每个阶段单独提交、单独定向验收，再进入下一阶段。
- 主任务进入 `in_progress` 后，复杂阶段拆成可归档的子任务；不要一次性重写 `PlatformStore`。
- 未通过本阶段放行门前，保持 501/`DISABLED`，不修改 Live 默认能力。

## Ordered Checklist

### 0. 能力契约与基线冻结

- [x] 新增 typed real execution contracts、registry、capability status 和结构化错误模型。
- [x] 为现有 `REAL_AGENT_ANALYSIS`、`REAL_DAMPER_COMPARISON`、`REAL_BASELINE_OPTIMIZATION` 保存确定性输入/输出 manifest 与关键响应金样。
- [x] 增加 placeholder/PENDING/fixed-metric 禁止清单测试；能力 API 返回明确 `LIVE`/`MOCK_ONLY`/`DISABLED`。
- [x] 在 backend/frontend spec 中记录契约和 capability gating。

验收：未登记组合在创建 Job/Artifact 前 501；已登记组合返回完整输入/输出/预算/取消能力；现有 359+ backend tests 不回归。

当前进度：能力注册表、`GET /api/v1/capabilities`、Live 创建前门禁、前端客户端门禁、真实基线 manifest 对照和定向回归已完成。

本轮进度：新增共享 `RealSolverExecutor`、确定性 DOE 生成器、真实 surrogate 训练/reload 证据和主动学习预算选择器；现有 `REAL_*` Agent 原子路径暂未切换，独立平台能力仍按目录保持 501/Mock-only。

本轮追加：`REAL_AGENT_ANALYSIS`、`REAL_DAMPER_COMPARISON`、`REAL_BASELINE_OPTIMIZATION` 的配置 runner 已统一经过 `ConfigExecutionRequest` 生命周期边界，保留原数值适配器，同时记录执行耗时、超时/取消检查和完成心跳；独立 `SOLVER_BATCH` 仍需完整 result-catalog 对等验收后才能解除 501。

本轮验收：共享执行/能力定向测试通过；后端完整测试 `378 passed`；`compileall`、`verify_submission.py`（429 files）、前端 Vitest `37 passed`、`tsc -b`、生产构建通过；oxlint 仍只有既有 3 条告警。

### 1. 共享 solver/result executor

- [x] 抽取 ANSYS/OpenSeesPy batch handler，复用 `pyansys_bridge.batch` 与现有 worker/adapter。
- [x] 接入真实 output manifest、result catalog、单位/列检查、取消、超时、恢复和心跳。
- [x] 迁移 ANALYSIS，再迁移 DAMPER_COMPARISON；比较只读取两个结果 Artifact，并登记统一 `result_catalog.json`。
- [ ] 通过旧 `REAL_*` 对等测试后，解除对应独立 `SOLVER_BATCH` 501。

验收：确定性 fake solver、OpenSeesPy smoke、取消/恢复和失败关闭均通过；来源 SHA 与旧路径一致。

### 2. DOE 与真实训练数据集

- [x] 将审批冻结 `doeDesignCount` 传入 shared DOE executor，保持 15 点模板兼容、5–24 范围和固定种子。
- [x] 每个设计点映射真实 solver Job；结果提取拒绝缺失、未验证、非有限或单位不一致样本。
- [x] 生成 design set、training dataset、usage 和 SHA-256 provenance；真实优化链不再依赖 fixed preview 或 `PENDING_REPLACEMENT` 分支（独立未实现入口仍失败关闭）。
- [x] 接通 DAMPER_OPTIMIZATION/FULL_OPTIMIZATION 的 BASELINE→DOE 阶段。

验收：请求/实际/补点/总求解数一致，训练集可重算；预算超限、样本缺失和求解失败全部失败关闭。

本轮追加：真实优化会在 output run 目录登记 `real_design_set.json` 和 `real_training_dataset.json`；每个 DOE 行必须有 completed solver caseId，训练集 SHA 与 executionEvidence 对齐，缺行或失败结果返回结构化 422。

### 3. Surrogate 与 active learning

- [x] 用 `pyansys_bridge.surrogate` 训练真实模型，登记模型字节、版本、schema、fold、CV 指标和选择依据。
- [x] 用 `pyansys_bridge.active_learning` 选择不重复 infill 点；每轮最多 2 点，最多 2 轮，返回真实新增求解数。
- [x] 达标时不补点，不达标时只在冻结预算内补点，预算耗尽结构化终止。
- [ ] 解除独立 `SURROGATE_TRAINING`、`ACTIVE_LEARNING` 501 前增加模型 reload/reproduce 和取消/恢复测试。

验收：模型预测可复现，CV 来自真实 fold，infill 不重复且不超过预算；不存在假 `.pkl`、固定 R²/RMSE/CV。

本轮验收：受控 Agent 优化链已使用真实结果训练并保留模型 reload 一致性、CV 和 active-learning 预算测试；独立平台入口仍因缺少完整 solver/result 请求契约与取消/恢复 worker 生命周期保持 501，未伪造成功。

### 4. Optimization / decision / FEM review

- [x] 接入 NSGA-II/Pareto、约束筛选、熵权 TOPSIS 和 explainability。
- [x] 推荐候选全部经过登记数据来源校验和独立 FEM validation；误差不达标按 review 预算回退。
- [x] 输出候选、权重、排名、误差、推荐和报告 manifest；LIVE 详情接口不再回放 `topsis_result()` 示例。

验收：报告可由 Artifact 重算；候选来源、约束和 FEM 证据缺失时不生成成功推荐。

本轮追加：独立 TOPSIS 详情路由在 `LIVE` 下跟随能力目录失败关闭；`MOCK` 返回显式 `executionMode=MOCK`、`simulation=true`，不再把示例结果伪装成真实执行。

本轮追加：TOPSIS 详情接口现在优先从成功的 `real_optimization_summary.json` 回放真实 Pareto/TOPSIS 证据，校验报告 SHA、候选有限数值、排名/权重完整性、全部 FEM review 通过和 `finalRecommendationStatus=ACCEPTED`；缺证据时返回结构化 422。

### 5. 全 Agent 与场景支持矩阵

- [ ] ANALYSIS 接入地震、风、交通和已建模组合；显式要求载荷 Artifact、列、单位、施加对象。
- [ ] DAMPER_COMPARISON 接入已校准黏滞/电涡流/摩擦组合；不支持 solver/scenario 组合从 capability API 隐藏。
- [ ] DAMPER_OPTIMIZATION/FULL_OPTIMIZATION 复用 Phase 1–4，不再硬编码单一地震动作。
- [x] RESULT_INQUIRY 读取统一 result catalog，保留运行白名单、列和单位验证。

验收：五类 Agent 每个已广告组合均有真实 smoke、失败关闭、取消/恢复；不支持组合明确不可用。

本轮边界：能力目录只广告已完成的 EARTHQUAKE 受控组合；WIND、TRAFFIC 和组合工况目前不进入 Agent LIVE 目录，避免把 operation 模板或未绑定荷载误当成真实 ANALYSIS。平台已有的独立入口继续在创建 Job 前 501。

本轮追加：结果追问已改为消费运行挂载的 `result_catalog.json`。目录存在时，查询白名单只来源于目录条目，并逐项校验目录 JSON SHA、来源路径、CSV 类型、列顺序、单位和来源 SHA；目录缺项、篡改或列不一致时结构化失败关闭。没有目录的历史 run 继续走旧 CSV 白名单兼容路径。

本轮追加（5.1 纵向切片 `ANALYSIS × WIND × ANSYS`）：上面“本轮边界”对 WIND 的限制仅在 `ANSYS` 求解器上解除，其余组合保持原状。新增 `docs/examples/templates/ansys_run_wind_baseline_template.json` 作为不绑定任何荷载路径的风工况 undamped baseline 模板，`load_case.path/dt/duration` 只能由冻结的荷载 Artifact 填充。`AnalysisAgent` 用 `_load_kind_gate` 取代原先的“仅地震”硬门：风工况必须同时满足求解器为 ANSYS、已登记且已标准化的荷载 Artifact（ID + SHA-256）、单通道 `NODAL_FORCE`/`FORCE`/`N|kN` 映射、以及显式作用目标 `NODE_GROUP:STBRIDGE_WIND_DECK_NODES`；任一项缺失或不匹配都返回结构化 `UNSUPPORTED`（`WIND_SOLVER_UNSUPPORTED` / `WIND_LOAD_ARTIFACT_REQUIRED` / `WIND_LOAD_MAPPING_UNSUPPORTED` / `WIND_LOAD_TARGET_UNSUPPORTED`），不生成占位成功。无上传时由项目内置 momo 风荷载补登记，不在审批门里静默编造通道。`loadTargetSetId` 进入冻结动作白名单并在 dispatch 校验中与 `loadKind` 强绑定；执行侧 `PlatformStore._apply_agent_standard_wind_load` 重新校验制品 SHA、荷载类型、施加方式、量纲、单位、目标集、均匀时间步，再把 `agent_wind_nodal_force_n.txt`、`dt`、`duration` 与 `wind_girder_load_nodes` 写入 solver 配置。证据方面复用地震路径的同一批门禁：`input_provenance_passed`（新增 `loadTargetSetId` 来源项）、`output_manifest_passed`、`result_catalog_passed`、`solver_version_profile_passed`，产出同样的 `real_analysis_summary.json` / `real_analysis_overview.json` / `result_catalog.json` / `real_output_manifest.json`。能力目录通过 `CapabilityDescriptor.solverScenarios` 精确广告：`ANSYS` 放行 `EARTHQUAKE + WIND`，`OPENSEESPY_INPROC` 仍只有 `EARTHQUAKE`；`docs/examples/real_agent_baseline_manifest.json` 同步新增 `REAL_AGENT_ANALYSIS_ANSYS_WIND` 基线用例，基线清单测试改为逐 solver 校验广告与清单一一对应。新增 43 项风工况测试（39 项单元/契约 + 4 项端到端真实 smoke、失败关闭与取消），后端 677 tests、compileall、`_manifest_rebuild.py`、`verify_submission.py`（465 文件）、前端 62 tests、生产构建与 oxlint 全绿。仍然门禁未放行：TRAFFIC 场景、WIND+TRAFFIC 组合工况、OpenSeesPy 风工况路径，以及 5.2/5.3 的 DAMPER_COMPARISON 与优化场景扩展，全部保持隐藏且失败关闭。5.1 复选框不勾选，因为该条目要求地震、风、交通和已建模组合全部接入，本轮只交付了风工况这一条纵向切片。

本轮质量门：核对时发现 `REAL_AGENT_ANALYSIS_ANSYS_WIND` 的 `templateSha256` 与 `ansys_run_wind_baseline_template.json` 实际内容不一致，已按文件重算为 `87b538251bc2c5377d0e71e2dd19199f9fa7538733d45be1427d40daf5afd8f5`。能力契约补上 `solverScenarios` 为逐 solver 广告真值，`GET /api/v1/capabilities` 测试锁定 ANSYS 才广告 WIND。后端 679 tests、compileall、`verify_submission.py`（465 文件）、前端 62 tests、生产构建与 oxlint（2 条既有告警）通过。许可环境的真实 ANSYS 风工况认证仍未做，与地震路径同一条发布门。

本轮追加：无上传的 ANSYS 风分析不再失败关闭，改为登记 momo 运营默认风荷载 `analysis_data/wind_inputs/wind_vertical_10mps_3600s.csv`（3600 s、dt=1 s、12 个竖向力通道，来自 `operation_3600s_dt1_10mps`）。当前 ANALYSIS 风路径仍只放行单通道节点力，因此先对空间列求和得到总竖向力，再按等权分配到 `STBRIDGE_WIND_DECK_NODES`；来源 SHA 指向原始空间场文件，聚合方式写入 `SUM_SPATIAL_FORCE_COLUMNS`。用户上传的风荷载路径不变。用户未提供文件且内置文件不可读时仍返回结构化失败。

### 6. Live 前端与发布

- [x] `client.ts` 增加 capability API 和 Live/Mock 能力过滤；Solver/Surrogate/Optimization 页面禁止硬编码放行。
- [x] Live 页面显示“真实执行”状态、预算、来源 SHA、验证状态和结构化失败；Mock 页面显著显示“模拟数据”。
- [x] 审批后的聊天消息续接同一活动 run；LLM 先调用 `workflow.observe` 获取最新状态，完成后再进入结果追问，不重复启动工程任务。
- [x] 增加 feature-gated 持久化 Harness 循环纵向切片：Job 终态只完成真实执行步骤，后续原子 Job 内部阶段及 evidence/review 动作由 LLM 按冻结 `allowedTools` 每轮推进一步，并把恢复点与确定性审查结果写入 run。
- [x] 验证同一会话的 LLM 历史跨请求持久化，并增加保留工程审计证据的会话历史删除功能。
- [x] 无上传的地震工程任务将项目内置 4001 点加速度记录标准化为当前 run 的 Artifact，并把 ID、SHA、单位、方向和来源冻结到审批及真实 Job。
- [x] 无上传的 ANSYS 风分析将 momo 运营默认风荷载 `wind_vertical_10mps_3600s.csv` 标准化为当前 run 的 Artifact，并把 ID、SHA、单位、目标集和来源冻结到审批及真实 Job。
- [ ] 增加浏览器端到端测试：上传→审批→执行→结果→报告→RESULT_INQUIRY。
- [ ] 通过全量质量门后按能力逐项移除 501；失败时回滚到 `DISABLED`/501。

本轮验收：前端已覆盖 capability API、创建前门禁、Mock/Live 标识、Agent 输入 SHA、计划预算和结果证据展示；独立平台能力未通过真实 worker 对等认证，继续保留 501。

本轮追加：普通分析与批量任务的主百分比改为汇总真实算例完成数和进行中算例百分比；畸形进度记录被隔离，进度汇总异常只记录日志，不会终止 worker 对真实求解器的监督。OpenSeesPy 继续从求解循环原子写入进度，ANSYS 新增只读 `ansys.out` 探针，按最新数值 `TIME` 与冻结的 `dt/duration` 推导步数，不修改 APDL、求解结果、指纹或工程归档元数据。确定性探针与完整测试通过；真实 ANSYS 许可证环境的发布认证仍保留为解除能力门禁前的要求。

本轮追加：修复生产流程七项阻断。启动脚本默认 LIVE、加载平台/持久循环开关并告警未知 `MOMO_*`，readiness 暴露并门禁执行模式；持久循环连续三次模型失败后终止；同步 Job 创建与持久化受同一状态事务保护，前端 404 可见；求解默认 7200 秒超时落盘并由 dispatcher 硬墙钟终止进程树；审批重建修正状态迁移、写序和等待审批取消出口；OpenSeesPy 复核不再错误要求 USER300。后端 504 tests、前端 48 tests、生产构建与编译门通过。

本轮追加：修复结果追问的严格网关 400。Harness 只保留首条固定 system，把工作流状态、用户追问和动态 `resultInquiryContext` 合并到末尾 user 包装载荷，最大化固定 system + 历史前缀缓存；全局结果目录允许跨会话读取已完成结果，但仍逐 run 校验 result catalog、CSV、列和 SHA 白名单。完成结果卡新增工况、模型、求解器、阻尼器及实际参数标记。删除可见会话时先取消非终态 run，保留 run/Job/Artifact 审计证据，解决历史测试会话因遗留状态无法删除。后端 514 tests、前端 50 tests、生产构建、lint、compileall、submission manifest 验证通过。

实机复测追加：严格网关 400 已消失，但启动路由模型面对“分别给出所有统计量的峰值”仍返回文字澄清。AUTO 现在对保守匹配的明确结果查询直接进入跨会话只读 `RESULT_INQUIRY`；模糊请求和显式工程任务仍保留原路由与工作流门禁。

实机复测再追加：跨会话查询已成功执行六次 `result.peak`，最终模型叙述因列表序号/单位换算触发数字证据门。连续两次叙述不合格时现在保留门禁并回退为工具原始输出的确定性表格，不再丢弃已验证查询结果。

最终门禁：后端 517 tests、compileall、454 文件 submission manifest 与 USER300 runtime 全部通过；LIVE 实机新会话跨会话查询返回 `INQUIRY / SUCCEEDED / DETERMINISTIC`，共读取六项峰值。

本轮追加：结果追问改为 NDJSON 渐进响应。查询 run 创建后立即返回读取状态，每次 `result.peak` 落盘后推送结构化指标；结果卡按项目符号展示中文指标、工程单位和简化时刻，隐藏原始 Markdown、重复叙述和内部数字门文案。历史查询记录通过只读投影获得相同结构化展示，不改写审计数据。

实机兼容复测追加：旧查询之后若出现新的失败 run，历史助手消息不再回退显示原始 Markdown；会话详情按旧 run 的结构化投影替换可见文案，底层消息和工具轨迹保持原样。

## Validation Commands

Backend:

```powershell
python -m pytest momo_agent/backend/tests -q --tb=short -p no:cacheprovider --basetemp D:\helloagent\.pytest-live-full
python -m compileall -q momo_agent/backend/app pyansys_bridge
python _manifest_rebuild.py
python verify_submission.py
```

Frontend:

```powershell
npm.cmd test
npm.cmd run build
npm.cmd run lint
```

真实执行门：

- OpenSeesPy 最小真实 smoke 必须在 CI/本地通过。
- ANSYS 需要具备许可证的发布环境通过同一输入 manifest 对等认证。
- 每次解除 501 前记录真实 Job、Artifact manifest、SHA、usage、取消/恢复和失败关闭证据。

## Risky Files / Rollback Points

- 高风险：`platform_store.py`、`agent_service.py`、`agent_harness.py`、`api/v1/router.py`、`api/v1/schemas.py`、`pyansys_bridge/batch`、`pyansys_bridge/surrogate`、`pyansys_bridge/active_learning`、`pyansys_bridge/optimization`、前端 `api/client.ts` 和能力页面。
- 每个 Phase 以独立提交为回滚点；回滚只关闭该 Phase capability flag，不恢复占位成功。
- 不删除旧 `REAL_*` 路径，直到新链对等测试、证据回放和发布认证完成。
