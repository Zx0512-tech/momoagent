# 执行计划：风工况阻尼器方案比选

工作目录：`D:/helloagent/.claude/worktrees/wind-comparison`（分支 `feat/08-22-wind-damper-comparison`，基线 `fbe4290`）
后端根：`momo_competition_submission/momo_agent/backend`

## 顺序

### 1. 契约层 `app/services/agent_engineering.py`
- 加 `COMPARISON_LOAD_KINDS = {'EARTHQUAKE': ('ANSYS',), 'WIND': ('ANSYS',)}`
- `build_damper_comparison_contract` 加 `load_kind: str = 'EARTHQUAKE'`，校验 `load_kind` 与 solver 组合，`loadKind` / `scenario` 用 `load_kind`

验证：`pytest tests/test_agent_engineering.py -q`

### 2. Agent 门禁 `app/agents/damper_comparison.py`
- `import` 补 `ANALYSIS_WIND_TARGET_SET_ID` / `ANALYSIS_WORKFLOW_PATHS_BY_LOAD_KIND` / `AnalysisAgent`
- `DAMPER_COMPARISON_PLAN` → `_comparison_plan(load_kind)`，第 0 条按工况改荷载名
- `plan()` 传 `load_kind=getattr(intent, 'load_kind', None) or 'EARTHQUAKE'`，`field_sources.loadKind` 按是否用户指定标注
- `prepare_approval()`：solver 门保留 → `load_kind` 分派（EARTHQUAKE 保留单通道判定 / WIND 委托 `AnalysisAgent._load_kind_gate` / 其他 `PRODUCTION_GATE`）→ 模板按 `ANALYSIS_WORKFLOW_PATHS_BY_LOAD_KIND[load_kind]['ANSYS']` → 冻结动作 `scenario=load_kind`，风工况补 `loadTargetSetId`
- **`solver_profile_builder` 仍读 joint 模板**（风模板无 `damper_calibration`，改了会失败关闭）

验证：`pytest tests/test_damper_comparison_agent.py -q`

### 3. 内置风荷载 `app/services/agent_service.py:537`
`contract_task == 'ANALYSIS'` → `contract_task in {'ANALYSIS', 'DAMPER_COMPARISON'}`

### 4. 平台侧 `app/services/platform_store.py`
- `_is_real_damper_comparison_request`：`scenario == 'EARTHQUAKE'` → 按 `AGENT_ANALYSIS_CONFIGS_BY_LOAD_KIND` 校验 `scenario × solver`
- `_generate_real_damper_comparison_artifacts`：模板与荷载绑定按 `loadKind` 分派，summary 补 `loadKind`
- 422 消息覆盖风工况

验证：`pytest tests/test_agent_damper_comparison_api.py -q`

### 5. 能力目录 `app/services/real_execution/registry.py`
`DAMPER_COMPARISON` → `scenarios=('EARTHQUAKE','WIND')` + `solverScenarios={'ANSYS': ('EARTHQUAKE','WIND')}`

### 6. 测试
- `tests/test_wind_analysis_agent.py::test_other_controlled_capabilities_stay_earthquake_only`：从列表移除 `DAMPER_COMPARISON`
- 新增 `tests/test_wind_damper_comparison.py`：覆盖设计文档的失败关闭矩阵全部 8 行 + 契约 + 冻结动作 + 能力目录 + 计划文案

### 7. 全量回归
```
pytest tests/test_agent_engineering.py tests/test_damper_comparison_agent.py \
       tests/test_agent_damper_comparison_api.py tests/test_wind_analysis_agent.py \
       tests/test_real_execution_capabilities.py tests/test_wind_damper_comparison.py -q
pytest tests -q -x
```

## 完成判据
- 失败关闭矩阵 8 行全部有测试
- 地震比选行为逐字节不变（消息、冻结动作字段、报告结构）
- 全量 pytest 无新增失败
- 未改 `COMPARISON_PROFILE`、`review()` 门槛、工作流定义、前端

## 回滚
`git checkout -- <file>`；worktree 独立，不影响主工作树。
