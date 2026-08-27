# 交通工况真实分析接入(ANALYSIS × TRAFFIC)

## Goal

ANALYSIS Agent 在受控真实执行链上支持 TRAFFIC 荷载工况:ANSYS 单通道竖向节点力时程,复用风工况的接入模式与门禁粒度;所有未验证组合保持失败关闭。在独立分支 `feat/08-13-analysis-traffic-scenario`(worktree `D:\helloagent-traffic`)交付。

## Background / Confirmed Facts

- 分支基线 master(caff8e1)上 ANALYSIS 真实链仅放行 EARTHQUAKE;风工况接入在主工作区进行中(未提交),本任务不依赖其代码,但镜像其结构以便合并。
- `pyansys_bridge` 已有交通荷载物理路径:`load_case.py` 的 `LOAD_KINDS` 含 `traffic`(默认方向 0,-1,0 竖直向下)、ANSYS `DISTRIBUTED_VECTOR_TABLE` 时程渲染、目标节点 `DEFAULT_STBRIDGE_TRAFFIC_LOAD_NODES`(主梁 8 节点,与风主梁节点相同)、模型元数据键 `traffic_load_nodes` / `traffic_load_component`。
- OpenSees 有 `traffic.pyfrag` 但无已验证 baseline;WIM 随机车流宏(`.mac`,`STBRIDGE_SINGLE_ROAD_*`)是另一条重型链,均不在本任务范围。
- master 上 `CapabilityDescriptor` 尚无 `solverScenarios` 字段(风分支新增);本分支按相同实现新增,合并时取并集。
- 荷载上传→映射→标准化管道对 loadKind 无白名单限制,version-2 mapping 携带 `loadKind` 与 `channels`。

## Requirements

- ANALYSIS Agent 审批门支持 `loadKind=TRAFFIC`:仅 ANSYS;必须引用审批冻结的标准荷载 Artifact(SHA 一致);单通道 `NODAL_FORCE` / `FORCE` / 单位 N 或 kN;目标必须是登记节点集 `STBRIDGE_TRAFFIC_DECK_NODES`。其余组合按结构化 reason 失败关闭:`TRAFFIC_SOLVER_UNSUPPORTED`、`TRAFFIC_LOAD_ARTIFACT_REQUIRED`、`TRAFFIC_LOAD_MAPPING_UNSUPPORTED`、`TRAFFIC_LOAD_TARGET_UNSUPPORTED`。
- 新登记模板 `docs/examples/templates/ansys_run_traffic_baseline_template.json`:undamped_baseline、`execution_mode=run`、`load_type=traffic`、方向 (0,-1,0)、不捆绑荷载 path/dt/duration(只能来自冻结 Artifact)、metadata 冻结 `traffic_load_nodes`。
- PlatformStore 绑定(`_apply_agent_standard_traffic_load`):校验标准 CSV(`load_kind=TRAFFIC`、单通道、`NODAL_FORCE`、`FORCE`、单位 N、`NODE_GROUP` 且 target_id 与冻结目标集一致、时间列严格等步长),写求解器输入文件,把 dt/duration/目标节点注入 `load_case` 与 `bridge_model.metadata`;证据含来源 Artifact 与求解输入双 SHA。
- 能力目录:ANALYSIS 与受控 SOLVER_BATCH 广告 TRAFFIC 仅限 ANSYS(`solverScenarios`);DAMPER_* 与其余能力保持 EARTHQUAKE 不变。
- `GENERIC_NODAL` 等其它 loadKind 保持 PRODUCTION_GATE 关闭;OPENSEESPY_INPROC × TRAFFIC 失败关闭。
- 冻结动作新增 `loadTargetSetId` 进入 provenance(与风工况同构)。

## Acceptance Criteria

- [ ] 审批门放行合法 TRAFFIC 组合并冻结模板路径、`loadTargetSetId`、Artifact ID/SHA;全部非法组合按 reason 失败关闭(与风工况同粒度的参数化测试)。
- [ ] `AnalysisDispatchInput` 拒绝未登记 solver/模板/loadKind/targetSet 组合;EARTHQUAKE 动作不得携带 traffic 目标集。
- [ ] 模板 preflight:`kind=undamped_baseline`、`solver=ansys`、`load_type=traffic`、`execution_mode=run`、路径检查全部存在。
- [ ] PlatformStore 绑定:合法制品绑定后 `load_case`/metadata/证据正确;≥10 个失败关闭用例(缺制品、SHA 不符、空数据、错误 load_kind/单位/目标、非均匀时间步、多通道等),失败时不留下任何文件。
- [ ] 能力目录:ANALYSIS/受控 SOLVER_BATCH `supports(ANSYS, TRAFFIC)=True`、`supports(OPENSEESPY_INPROC, TRAFFIC)=False`;其余能力矩阵不变。
- [ ] 分支上定向测试、后端全量 pytest、compileall 通过;新模板文件通过 `_manifest_rebuild.py` 进入提交清单并通过 `verify_submission.py`。

## Constraints

- 只在 worktree `D:\helloagent-traffic` 开发;不修改 `D:\helloagent` 主工作区(风工况会话在用)。
- 不引入 WIND 功能代码;但共享结构(`*_BY_LOAD_KIND` 注册表、`_load_kind_gate`、`solverScenarios`、`AGENT_LOAD_TARGET_SETS`)与风分支同名同形,合并时按并集处理。
- 不做 WIM 随机车流宏链、OpenSees traffic、组合工况;不放宽任何审批/预算/证据门。
- 真实 ANSYS 许可证执行留给发布认证;开发阶段用确定性测试与 preflight 验证。

## Out of Scope

- 前端 UI 改动(能力 API 自动带出新工况)。
- DAMPER_COMPARISON / 优化链的 TRAFFIC 支持。
- 浏览器 E2E(由 `08-13-browser-live-e2e` 负责)。
