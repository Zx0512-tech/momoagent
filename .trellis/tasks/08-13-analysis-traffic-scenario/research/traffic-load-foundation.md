# 调研:交通荷载既有基础与风工况接入模式

日期:2026-08-13。基线:master caff8e1。所有路径相对 `momo_competition_submission/`。

## 交通荷载既有物理基础(master 已存在,不需新造)

- `pyansys_bridge/models/load_case.py`
  - `LOAD_KINDS = {"earthquake", "wind", "traffic"}`;`load_traffic_csv()` 已存在。
  - `_default_direction('traffic') == {'x': 0, 'y': -1, 'z': 0}`(竖直向下),wind 是 (0, 1, 0)。
  - `LoadCase.command_template_context()` 会展开 `traffic_name/traffic_path/traffic_scale/traffic_dt/traffic_duration/traffic_direction_*` 到渲染上下文。
- `pyansys_bridge/core/ansys_load_rendering.py`
  - 上下文含 `traffic_name` 时渲染交通荷载;非 `.mac` 路径走 `DISTRIBUTED_VECTOR_TABLE`(`_ansys_distributed_vector_table_commands`,base_name `TRAFFIC_LOAD`),与风的分布式时程同构。
  - `.mac` 路径是 WIM 随机车流宏模式(`STBRIDGE_SINGLE_ROAD_*`),本任务不做。
- `pyansys_bridge/core/ansys_load_targets.py`
  - `DEFAULT_STBRIDGE_TRAFFIC_LOAD_NODES = DEFAULT_STBRIDGE_WIND_GIRDER_NODES = (1, 23, 43, 61, 132, 114, 94, 72)`。
  - `_traffic_load_points()` 读取 `model_metadata['traffic_load_nodes']`(缺省用上面常量),等权分布;`traffic_load_component` 可选。
- `pyansys_bridge/templates/opensees/traffic.pyfrag` 存在但无验证 baseline → OpenSees 保持失败关闭。
- `momo_agent/backend/app/services/traffic_library.py`:WIM 2021-01 车流库(生成"随机车流"荷载 Artifact 的数据源),与本任务的标准化时程链路正交。

## 风工况接入模式(主工作区未提交 diff,本分支镜像其结构)

风工况改动(参考 `D:\helloagent\.tmp\wind_diff.patch`,只读,不要把 WIND 功能带入本分支):

1. `agents/analysis.py`
   - `ANALYSIS_WIND_WORKFLOW_PATHS`(仅 ANSYS)→ `ANALYSIS_WORKFLOW_PATHS_BY_LOAD_KIND` → `ANALYSIS_REGISTERED_WORKFLOW_PATHS`。
   - `ANALYSIS_WIND_TARGET_SET_ID = 'STBRIDGE_WIND_DECK_NODES'`;`ANALYSIS_WIND_FORCE_UNITS = {'N', 'kN'}`。
   - `ANALYSIS_FROZEN_ACTION_FIELDS` 增 `loadTargetSetId`。
   - `AnalysisPreflightInput.load_kind: Literal[...]`;preflight 按 loadKind 选模板,fail 返回 `UNREGISTERED_ANALYSIS_TEMPLATE`,并断言 `config_preflight['load_type'] == loadKind.lower()`(`preflight_config` master 已输出 `load_type`)。
   - `_load_kind_gate(load_kind, *, solver, channels, standard_artifact_id, standard_sha256) -> (reason, message) | None`:EARTHQUAKE 保留旧行为(多通道拒绝,reason=PRODUCTION_GATE);未识别 loadKind → PRODUCTION_GATE;WIND → 依次校验 solver 注册、制品存在、单通道、applicationType=NODAL_FORCE + quantity=FORCE + sourceUnit∈{N,kN}、targetType=NODE_GROUP + targetId=登记集。
   - `AnalysisDispatchInput.validate_frozen_action`:按 loadKind 查注册表,校验 solver/模板匹配、`loadTargetSetId` 与 loadKind 匹配(非 WIND 必须为 None)。
   - 通过门后 `frozen_action['loadTargetSetId'] = 目标集 ID`(仅该 loadKind)。
2. `services/platform_store.py`
   - `AGENT_WIND_ANALYSIS_CONFIGS` → `AGENT_ANALYSIS_CONFIGS_BY_LOAD_KIND`;未注册 loadKind → 422 `UNSUPPORTED_REAL_ANALYSIS_LOAD_KIND`;solver 不支持 → 422 `UNSUPPORTED_REAL_ANALYSIS_SOLVER`。
   - `AGENT_LOAD_TARGET_SETS = {'STBRIDGE_WIND_DECK_NODES': list(DEFAULT_STBRIDGE_WIND_GIRDER_NODES)}`。
   - `_apply_agent_standard_wind_load(config, run_dir, params)`:检查顺序 = 制品 ID → 目标集 → SHA(409)→ 非空 → 行契约(单 channel_id、load_kind、application_type、quantity、unit=='N' 严格)→ target_type/target_id → time/value 可解析 → ≥2 点且严格递增 → 等步长(容差 `max(|dt|,1)*1e-9`)。全过后写 `agent_wind_nodal_force_n.txt`(`format(value, '.15g')`),组装 evidence(artifactId/sha256/solverInputPath/solverInputSha256/sampleCount/timeStepS/durationS/component/unit/targetSetId/targetNodes),注入 `bridge_model.metadata.wind_girder_load_nodes` 与 `load_case{name,load_type,path,scale=1.0,dt,duration,metadata.agent_standard_load}`。
   - 注意:CSV 行单位严格 'N'(kN 只在 mapping.sourceUnit 层被接受,标准化阶段已换算)。
3. `real_execution/contracts.py`:`solver_scenarios: dict[str, tuple[str, ...]]`(alias `solverScenarios`)+ `supports(solver=..., scenario=...)`(空 dict 回退 `scenarios`)。
4. `real_execution/registry.py`:ANALYSIS 与受控 SOLVER_BATCH 的 `scenarios` 增 WIND,`solverScenarios` 限 ANSYS;reason 更新。
5. `services/agent_evidence.py`:`frozen_action['loadTargetSetId']` 存在时追加 provenance 项(USER_DECISION)。
6. 模板 `docs/examples/templates/ansys_run_wind_baseline_template.json`:undamped(omit_dampers)、include_modal、response_nodes [36, 107]、`interpolate_time_history_tables: true`、`load_case` 无 path/dt/duration、metadata 冻结节点集 + template_note。
7. 测试蓝本 `momo_agent/backend/tests/test_wind_analysis_agent.py`(未提交):标准 CSV 头 `time_s,load_kind,channel_id,application_type,target_type,target_id,component,quantity,value,unit`;用 `PlatformStore.__new__` + monkeypatched `get_artifact` 做绑定单测;能力目录断言 `supports()` 矩阵。

## 合并注意(与风分支)

- 风分支测试 `test_wind_analysis_agent.py` 有两处断言在 TRAFFIC 上线后需更新:`test_capability_catalog_advertises_wind_for_ansys_only` 中 `not supports(ANSYS, TRAFFIC)`;`test_other_load_kinds_remain_closed` 参数化含 `TRAFFIC`(预期 PRODUCTION_GATE)。合并本分支时改为 TRAFFIC 已放行的对应断言。
- `SUBMISSION_MANIFEST.json` 由 `_manifest_rebuild.py` 生成;合并后重跑,不手工合并。

## 会话隔离

- 风工况在 `D:\helloagent` 主工作区(未提交);solver-batch 子任务在 `D:\helloagent-unlock-solver`;本任务在 `D:\helloagent-traffic`。禁止跨树修改。
