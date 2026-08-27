# 设计：风工况阻尼器方案比选

## 现状：三条链路的风工况支持度

| 链路 | 任务类型 | 风工况 | 缺口 |
|---|---|---|---|
| 单次分析 | `ANALYSIS` | ✅ 已打通 | — |
| 批量参数计算 | `DAMPER_PARAMETER_SWEEP` | ⚠️ 主工作树有未提交改动（另一会话在做） | 不属本任务 |
| **方案比选** | `DAMPER_COMPARISON` | ❌ 硬编码 EARTHQUAKE | **本任务** |

`ANALYSIS` 已经把风工况需要的全部基础设施建好了，本任务是把 `DAMPER_COMPARISON` 接到这些既有设施上，不新建任何风荷载机制。

可复用的既有设施：
- 登记模板 `docs/examples/templates/ansys_run_wind_baseline_template.json`
- 目标集 `STBRIDGE_WIND_DECK_NODES` → `AGENT_LOAD_TARGET_SETS` → 8 个主梁节点
- 放行判定 `AnalysisAgent._load_kind_gate`（制品、单通道、`UY`、`NODAL_FORCE`、N/kN、目标集）
- 内置风荷载 `load_artifact_service.provision_bundled_wind`（空间场列求和 → 单通道总竖向力）
- 平台侧荷载绑定 `PlatformStore._apply_agent_standard_wind_load`
- 模板路由表 `AGENT_ANALYSIS_CONFIGS_BY_LOAD_KIND` / `ANALYSIS_WORKFLOW_PATHS_BY_LOAD_KIND`

## 改动点（6 处）

### 1. 契约构造 `services/agent_engineering.py`

`build_damper_comparison_contract` 硬编码 `'loadKind': 'EARTHQUAKE'` / `'scenario': 'EARTHQUAKE'`。

加 `load_kind: str = 'EARTHQUAKE'` 参数 + 放行表：

```python
COMPARISON_LOAD_KINDS: dict[str, tuple[str, ...]] = {
    'EARTHQUAKE': ('ANSYS',),
    'WIND': ('ANSYS',),
}
```

比选两种工况都只放行 ANSYS（地震比选本来就只放行 ANSYS——`prepare_approval` 里 `solver != 'ANSYS'` 直接拒），所以这张表的两行都是 `('ANSYS',)`。它存在的价值是**声明**，让新增工况时有唯一改动点，而不是散在 `prepare_approval` 的 if 分支里。

默认值 `EARTHQUAKE` 保证现有调用点零改动。

> 与 `PARAMETER_SWEEP_LOAD_KINDS`（主工作树另一会话在加）刻意分开：批量计算放行 `EARTHQUAKE: (ANSYS, OPENSEESPY_INPROC)`，比选只放行 ANSYS。两者放行口径不同，合成一张表会让其中一条链路被错误放宽。

### 2. Agent 审批门 `agents/damper_comparison.py`

当前 `prepare_approval` 的门禁：

```python
if solver != 'ANSYS':
    failure_message = '真实双工况对比首期仅放行已校准 ANSYS USER300；...'
elif load_kind != 'EARTHQUAKE' or (channels and len(channels) != 1):
    failure_message = '真实双工况对比仅放行单通道地震一致激励；...'
```

改为：solver 门保留（比选两工况都只放行 ANSYS）；`load_kind` 门按类型分派——
- `EARTHQUAKE`：保留 `channels and len(channels) != 1` 判定，消息不变
- `WIND`：委托 `AnalysisAgent._load_kind_gate`，与单次分析同一口径同一 reason 码
- 其他：`PRODUCTION_GATE` 拒绝

模板选择从硬编码 `ANALYSIS_WORKFLOW_PATHS['ANSYS']` 改为 `ANALYSIS_WORKFLOW_PATHS_BY_LOAD_KIND[load_kind]['ANSYS']`。

冻结动作 `'scenario': 'EARTHQUAKE'` → `load_kind`；风工况补 `loadTargetSetId`。

**校准剖面不动**：`solver_profile_builder` 仍读 `ansys_run_joint_baseline_workflow_template.json`。这是本设计里最容易踩错的一点——风模板 `ansys_run_wind_baseline_template.json` 声明 `omit_dampers: true` 且**没有 `damper_calibration` 段**，若改为从风模板取 profile，`solver_version_profile_passed(require_user300=True)` 会因 `userElement` 缺失而失败关闭。USER300 校准是阻尼器单元的属性，与荷载类型无关，必须继续从 joint 模板取。

预检 `config_preflight` 断言需放宽：当前硬编码 `kind == 'undamped_baseline' and solver == 'ansys' and execution_mode == 'run'`。风模板 `preflight_config` 返回 `kind=undamped_baseline` / `solver=ansys` / `execution_mode=run` / `load_type=wind`（已由 `test_wind_analysis_agent.py` 验证），三个断言都成立，**无需改动**。

### 3. 内置风荷载登记 `services/agent_service.py:537`

```python
elif load_kind == 'WIND' and contract_task == 'ANALYSIS':
```

`contract_task == 'ANALYSIS'` 限制去掉 `DAMPER_COMPARISON`。改为允许 `{'ANALYSIS', 'DAMPER_COMPARISON'}`。

不放开 `DAMPER_OPTIMIZATION` / `DAMPER_PARAMETER_SWEEP`：前者风工况本任务范围外，后者由另一会话负责，避免两个会话改同一行语义。

### 4. 平台侧执行门 `services/platform_store.py`

`_is_real_damper_comparison_request` 的 `params.get('scenario') == 'EARTHQUAKE'` 改为按 `AGENT_ANALYSIS_CONFIGS_BY_LOAD_KIND` 校验 `scenario × solver`，与 `_is_real_damper_parameter_sweep_request` 同构。

`_generate_real_damper_comparison_artifacts` 的模板与荷载绑定按 `loadKind` 分派：
- 模板：`AGENT_ANALYSIS_CONFIGS_BY_LOAD_KIND[load_kind]['ANSYS']`
- 荷载：`WIND` → `_apply_agent_standard_wind_load(config, case_dir, params)`；否则 `_apply_agent_standard_earthquake_load`
- summary 的 `'solver': 'ANSYS'` 保留，补 `'loadKind': load_kind`

422 拒绝消息 `'REAL_DAMPER_COMPARISON 仅支持 ANSYS 地震双工况 SOLVER_BATCH'` 更新为覆盖风工况。

### 5. 能力目录 `services/real_execution/registry.py`

`DAMPER_COMPARISON` 描述符：

```python
solvers=('ANSYS',),
scenarios=('EARTHQUAKE', 'WIND'),
solverScenarios={'ANSYS': ('EARTHQUAKE', 'WIND')},
```

`SOLVER_BATCH/CONTROLLED_AGENT` 已声明 `ANSYS: (EARTHQUAKE, WIND)`，无需改动。

`test_wind_analysis_agent.py::test_other_controlled_capabilities_stay_earthquake_only` 当前断言 `DAMPER_COMPARISON` 为 EARTHQUAKE-only，需从该参数化列表移除并在比选测试中正向断言。这是**有意的契约变更**，不是回归。

### 6. 计划文案 `agents/damper_comparison.py`

`DAMPER_COMPARISON_PLAN[0]` 写死"冻结同一已验证地震荷载"。改为按 `load_kind` 生成，风工况显示"风荷载"。计划文案会进入审批卡片和 LLM grounding，留着"地震"会让风工况的用户看到错误描述。

## 不改动的部分

- **等最大出力剖面** `COMPARISON_PROFILE`：`forceCapN=4e6`、`designVelocityMps=0.2`、`viscousAlpha=0.3` 与荷载类型无关，是阻尼器参数标定基准。风工况下阻尼器速度分布确实与地震不同，但改这些值属于工程口径决策，不在本任务范围。
- **`_compare_case_objectives`**：按共有数值指标对比，与荷载类型无关。
- **证据门槛** `review()`：所有 checks 与荷载类型无关，含 `require_user300=True`。
- **工作流定义** `_comparison_workflow()`：步骤序列（含 CALIBRATION）与荷载类型无关。
- **前端**：能力矩阵由后端 `solverScenarios` 驱动。

## 失败关闭矩阵

| loadKind | solver | 结果 | reason |
|---|---|---|---|
| EARTHQUAKE | ANSYS | ✅ 放行 | — |
| EARTHQUAKE | OPENSEESPY_INPROC | ❌ | solver 门（消息不变） |
| WIND | ANSYS + 合规通道/目标集 | ✅ 放行 | — |
| WIND | ANSYS + 缺登记制品 | ❌ | `WIND_LOAD_ARTIFACT_REQUIRED` |
| WIND | ANSYS + 多通道/错分量/错单位 | ❌ | `WIND_LOAD_MAPPING_UNSUPPORTED` |
| WIND | ANSYS + 错目标集 | ❌ | `WIND_LOAD_TARGET_UNSUPPORTED` |
| WIND | OPENSEESPY_INPROC | ❌ | solver 门 |
| TRAFFIC / GENERIC_NODAL | 任意 | ❌ | `PRODUCTION_GATE` |

## 兼容性

契约新增 `load_kind` 关键字参数带默认值；冻结动作风工况新增 `loadTargetSetId`（地震仍不含，与 `ANALYSIS` 一致）；平台侧 summary 新增 `loadKind`。历史地震运行的报告与结果目录结构不变。

## 并发风险

主工作树有另一会话正在给 `DAMPER_PARAMETER_SWEEP` 加风工况支持，未提交改动涉及 `agent_engineering.py`、`platform_store.py`、`agent_service.py`、`damper_parameter_sweep.py`。本任务在独立 worktree（分支 `feat/08-22-wind-damper-comparison`，基线 `fbe4290`）实施，与其无文件级冲突风险，但合并时 `agent_engineering.py` 与 `platform_store.py` 会有相邻改动，需人工核对：两边都会在这两个文件里加"按 loadKind 分派模板"的逻辑，语义互补但位置相近。
