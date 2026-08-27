# 技术设计:ANALYSIS × TRAFFIC 真实接入

## 分支与合并策略

- 分支 `feat/08-13-analysis-traffic-scenario`,基于 master `caff8e1`,工作目录为 worktree `D:\helloagent-traffic`。
- 风工况(WIND)在主工作区未提交进行中。两条线会修改同一批文件;本分支刻意采用与风分支**同名同形**的共享结构,把合并冲突收敛为"字典条目/分支取并集":

| 文件 | 风分支引入 | 本分支引入 | 合并解法 |
|---|---|---|---|
| `agents/analysis.py` | `ANALYSIS_WORKFLOW_PATHS_BY_LOAD_KIND`(含 WIND)、`_load_kind_gate` WIND 分支、`ANALYSIS_WIND_*` 常量 | 同结构含 TRAFFIC、gate TRAFFIC 分支、`ANALYSIS_TRAFFIC_*` 常量 | dict/gate 并集 |
| `services/platform_store.py` | `AGENT_ANALYSIS_CONFIGS_BY_LOAD_KIND`、`AGENT_LOAD_TARGET_SETS`(WIND 集)、`_apply_agent_standard_wind_load` | 同结构 TRAFFIC 条目、`_apply_agent_standard_traffic_load` | dict 并集 + 两个方法共存 |
| `real_execution/contracts.py` | `solverScenarios` 字段 + `supports()` | 完全相同的代码 | 保留任一侧 |
| `real_execution/registry.py` | ANALYSIS/受控 SOLVER_BATCH 增 WIND | 同两条目增 TRAFFIC | scenarios/solverScenarios 并集 |
| `services/agent_evidence.py` | `loadTargetSetId` provenance | 完全相同的代码 | 保留任一侧 |
| 风分支 `test_wind_analysis_agent.py` | 断言 `not supports(ANSYS, TRAFFIC)`、TRAFFIC 走 PRODUCTION_GATE | (本分支无此文件) | 合并时删除/更新这两处断言 |
| `SUBMISSION_MANIFEST.json` | 重建含风模板 | 重建含交通模板 | 合并后重跑 `_manifest_rebuild.py` |

## 数据流

```
上传/映射(version-2 mapping, loadKind=TRAFFIC, 单通道)
  → 标准化产出标准 CSV Artifact(load_kind=TRAFFIC 行,SHA 冻结)
  → AnalysisAgent.prepare_approval:_load_kind_gate 校验 solver=ANSYS、单通道
    NODAL_FORCE/FORCE/N|kN、NODE_GROUP=STBRIDGE_TRAFFIC_DECK_NODES
  → frozen_action 冻结 workflowConfigPath / loadTargetSetId / ArtifactId / SHA
  → AnalysisDispatchInput 复核冻结组合合法
  → PlatformStore._apply_agent_standard_traffic_load:
      校验 CSV 内容与冻结值一致 → 写 agent_traffic_nodal_force_n.txt
      → load_case{name=traffic, load_type=traffic, path, dt, duration}
      → bridge_model.metadata.traffic_load_nodes = 登记节点集
  → config_runner → ansys_load_rendering(DISTRIBUTED_VECTOR_TABLE,
      traffic 方向由模板 direction (0,-1,0) 提供,等权分布到离散节点)
```

## 关键契约

- 目标集注册:`AGENT_LOAD_TARGET_SETS['STBRIDGE_TRAFFIC_DECK_NODES'] = list(DEFAULT_STBRIDGE_TRAFFIC_LOAD_NODES)`(pyansys_bridge 常量为唯一事实来源,不复制字面量)。
- 模板 `ansys_run_traffic_baseline_template.json` 以风模板为蓝本:`omit_dampers=true`、`include_modal=true`、response nodes 36/107、`interpolate_time_history_tables=true`;`load_case` 不含 path/dt/duration;metadata 含 `traffic_load_nodes` 与 template_note(声明荷载只来自冻结制品)。
- 错误码(HTTP 422/409,结构化 detail.code):`TRAFFIC_LOAD_ARTIFACT_REQUIRED`、`TRAFFIC_LOAD_TARGET_REQUIRED`、`LOAD_ARTIFACT_HASH_MISMATCH`、`EMPTY_STANDARD_LOAD`、`UNSUPPORTED_STANDARD_TRAFFIC_LOAD`、`TRAFFIC_LOAD_TARGET_MISMATCH`、`INVALID_STANDARD_LOAD`、`INVALID_STANDARD_LOAD_TIME`、`NON_UNIFORM_STANDARD_LOAD_TIME`;命名与风工况对应码同构。
- 能力目录 reason 文案说明"交通工况仅放行 ANSYS 单通道节点力"。

## 权衡

- **ANSYS-only**:OpenSees 无已验证 traffic baseline,按父任务约束不承诺未验证组合;OPENSEESPY × TRAFFIC 返回 `TRAFFIC_SOLVER_UNSUPPORTED`。
- **单通道限制**:与风工况一致的保守 MVP;多通道/车道分布留待后续增量。
- **方向放模板**:标准 CSV 只携带力幅值(N),方向 (0,-1,0) 由登记模板声明,与 `load_case.py` 的 traffic 默认方向一致,避免制品与模板双重声明冲突。
- **WIM 宏模式不做**:`.mac` 移动轴载链路(单车道质心移动荷载)物理上更精细,但依赖生成器与宏制品登记,是独立验收单元。

## 回滚

- 整个能力由 registry 两条目控制;revert 本分支即可完全回退,不影响 EARTHQUAKE 现网行为。
- 分支内每个阶段独立提交(测试→实现→模板/清单),可按提交粒度回退。
