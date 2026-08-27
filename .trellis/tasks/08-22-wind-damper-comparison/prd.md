# 风工况阻尼器方案比选接入智能体

## 背景

`DAMPER_COMPARISON`（阻尼器方案比选）目前是**地震工况专属**能力：同一已验证地震荷载 + 同一 STbridge 布置 + 等最大出力参数剖面，串行跑 2~3 个真实 ANSYS 工况后按同一基准对比响应。

风工况已在 `ANALYSIS`（单次分析）链路打通：登记模板 `ansys_run_wind_baseline_template.json`、目标集 `STBRIDGE_WIND_DECK_NODES`、单通道 `UY` 节点力时程、内置风荷载 `provision_bundled_wind`，能力目录 `ANALYSIS` / `SOLVER_BATCH` 已声明 `ANSYS: (EARTHQUAKE, WIND)`。

缺口是：**风工况下无法做阻尼器方案比选**。用户问"风荷载下黏滞和电涡流阻尼器哪个好"，当前链路会被审批门拒绝。

## 目标

让 `DAMPER_COMPARISON` 支持 `loadKind=WIND`，复用已登记的风荷载模板、目标集和通道口径，风工况仅放行 ANSYS。

## 范围内

- `DAMPER_COMPARISON` 契约、审批门、冻结动作按 `loadKind` 参数化（EARTHQUAKE / WIND）
- 风工况比选自动登记项目内置风荷载（复用 `provision_bundled_wind`）
- 平台侧真实执行按 `loadKind` 选择模板与荷载绑定
- 能力目录 `DAMPER_COMPARISON` 声明 `solverScenarios: {ANSYS: (EARTHQUAKE, WIND)}`
- 单元测试覆盖风工况放行与失败关闭路径

## 范围外

- OpenSeesPy 风工况比选（无已登记风模板，必须 fail closed）
- TRAFFIC / 风-车组合工况
- `DAMPER_OPTIMIZATION` / `FULL_OPTIMIZATION` 的风工况（保持 EARTHQUAKE-only）
- 等最大出力剖面（`COMPARISON_PROFILE`）的工程取值调整——与荷载类型无关，沿用现值
- 前端改动：能力矩阵由后端 `solverScenarios` 驱动，无需前端硬编码

## 约束

1. **失败关闭优先**：任何未登记的 `loadKind × solver` 组合必须在生成审批前拒绝，不得进入求解。
2. **与 ANALYSIS 口径一致**：风荷载制品、单通道、`UY` 分量、`STBRIDGE_WIND_DECK_NODES` 目标集的判定必须复用 `AnalysisAgent._load_kind_gate`，不得写第二份放行逻辑。
3. **地震路径零回归**：现有地震比选的契约字段、冻结动作、证据门槛、报告结构不变。
4. **USER300 校准门不放松**：比选仍要求 `require_user300=True`，校准剖面来自 `ansys_run_joint_baseline_workflow_template.json`（与荷载类型无关）。

## 验收标准

### 风工况放行
- 意图 `loadKind=WIND` + `solver=ANSYS` + 2~3 个不同阻尼器 → `prepare_approval` 通过，冻结动作含 `loadKind=WIND`、`scenario=WIND`、`workflowConfigPath=ansys_run_wind_baseline_template.json`、`loadTargetSetId=STBRIDGE_WIND_DECK_NODES`
- 风工况比选自动登记内置风荷载，`loadDatasetArtifactId` / `loadDatasetSha256` 非空
- 平台侧 `_is_real_damper_comparison_request` 接受 `scenario=WIND`，真实执行走风模板 + `_apply_agent_standard_wind_load`

### 风工况失败关闭
- `solver=OPENSEESPY_INPROC` + `loadKind=WIND` → 拒绝，`reason=WIND_SOLVER_UNSUPPORTED`
- 缺少登记风荷载制品 → 拒绝，`reason=WIND_LOAD_ARTIFACT_REQUIRED`
- 通道非单通道 / 非 `NODAL_FORCE` / 非 `UY` / 单位非 N|kN → 拒绝，`reason=WIND_LOAD_MAPPING_UNSUPPORTED`
- 目标集非 `STBRIDGE_WIND_DECK_NODES` → 拒绝，`reason=WIND_LOAD_TARGET_UNSUPPORTED`
- `TRAFFIC` / `GENERIC_NODAL` → 拒绝，`reason=PRODUCTION_GATE`

### 地震回归
- 现有 `test_damper_comparison_agent.py`、`test_agent_damper_comparison_api.py` 全部通过且无需修改断言语义
- 地震冻结动作仍不含 `loadTargetSetId`

### 能力目录
- `DAMPER_COMPARISON` 的 `supports(ANSYS, WIND)` 为真，`supports(OPENSEESPY_INPROC, WIND)` 为假
- `DAMPER_OPTIMIZATION` / `FULL_OPTIMIZATION` 仍为 `EARTHQUAKE`-only
