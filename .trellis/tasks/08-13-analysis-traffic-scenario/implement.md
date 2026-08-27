# 实施计划与验收门

工作目录:`D:\helloagent-traffic`(worktree,分支 `feat/08-13-analysis-traffic-scenario`)。
测试解释器复用主仓 venv:`D:\helloagent\momo_competition_submission\.venv\Scripts\python.exe`,cwd 设为 worktree 的 `momo_competition_submission`。

## Ordered Checklist

### 0. 基线冒烟

- [x] 在 worktree 跑 master 基线定向测试(`test_agent_analysis_api.py` 或含 AnalysisAgent 的现有测试文件),确认 worktree + 主仓 venv 组合可用。

### 1. 红:先写测试

- [x] 新增 `momo_agent/backend/tests/test_traffic_analysis_agent.py`,镜像风工况测试蓝本(`D:\helloagent` 主工作区的 `test_wind_analysis_agent.py`,未提交,只读参考):
  - 审批门:合法组合冻结模板/目标集/制品 SHA;缺制品、OpenSees、通道契约不符(applicationType/quantity/sourceUnit)、多通道、缺通道、非法 targetType/targetId 逐项失败关闭;GENERIC_NODAL 保持 PRODUCTION_GATE;EARTHQUAKE 路径不回归。
  - `AnalysisDispatchInput`:接受登记 TRAFFIC 组合;拒绝 OpenSees/错配模板/未登记 loadKind/错误或缺失 targetSet;EARTHQUAKE 动作携带 traffic 目标集被拒。
  - 模板:JSON 断言(solver=ansys、run、load_type=traffic、无捆绑 path/dt/duration、metadata.traffic_load_nodes 与 `DEFAULT_STBRIDGE_TRAFFIC_LOAD_NODES` 一致、source_path 存在)+ `preflight_config` 报告 undamped_baseline/ansys/traffic/run。
  - PlatformStore:合法绑定(load_case path/dt/duration、metadata 节点、证据字段、双 SHA)+ ≥10 个失败关闭参数化用例 + 多通道拒绝;失败时 run 目录无残留文件。
  - 能力目录:ANALYSIS/受控 SOLVER_BATCH `supports(ANSYS,TRAFFIC)` 为真、OpenSees 为假;DAMPER_* 仍 EARTHQUAKE-only。
- [x] 若标准化管道对 TRAFFIC 有缺口(检查 mapping 校验与标准化产出),补交通标准化测试。现有 version-2 mapping 已接受 TRAFFIC,无需改标准化管道。

### 2. 绿:实现

- [x] `agents/analysis.py`:`ANALYSIS_TRAFFIC_WORKFLOW_PATHS` / `ANALYSIS_WORKFLOW_PATHS_BY_LOAD_KIND` / `ANALYSIS_TRAFFIC_TARGET_SET_ID='STBRIDGE_TRAFFIC_DECK_NODES'` / `ANALYSIS_TRAFFIC_FORCE_UNITS={'N','kN'}`;`_load_kind_gate`(EARTHQUAKE 保持原行为 + TRAFFIC 分支);preflight 按 loadKind 选模板并校验 `load_type`;frozen_action 冻结 `loadTargetSetId`;`AnalysisPreflightInput.load_kind` 增加 `TRAFFIC`;`ANALYSIS_FROZEN_ACTION_FIELDS` 增加 `loadTargetSetId`。
- [x] 新模板 `docs/examples/templates/ansys_run_traffic_baseline_template.json`(蓝本:风模板结构;summary_path 用 `ansys_undamped_traffic_summary.json`)。
- [x] `services/platform_store.py`:`AGENT_TRAFFIC_ANALYSIS_CONFIGS` / `AGENT_ANALYSIS_CONFIGS_BY_LOAD_KIND` / `AGENT_LOAD_TARGET_SETS`(导入 `DEFAULT_STBRIDGE_TRAFFIC_LOAD_NODES`);`_apply_agent_standard_traffic_load` 全量校验后写 `agent_traffic_nodal_force_n.txt` 并注入 `load_case` + `bridge_model.metadata.traffic_load_nodes`;错误码见 design.md。
- [x] `real_execution/contracts.py`:`solverScenarios` 字段 + `supports()`(与风分支实现一致)。
- [x] `real_execution/registry.py`:ANALYSIS 与受控 SOLVER_BATCH 增 TRAFFIC(仅 ANSYS),reason 更新。
- [x] `services/agent_evidence.py`:`loadTargetSetId` 写入 provenance。
- [x] 若标准化管道有缺口,最小修复。现有 mapping 已覆盖 TRAFFIC。

### 3. 质量门

- [x] 定向:`python -m pytest momo_agent/backend/tests/test_traffic_analysis_agent.py -q`。38 passed。
- [x] 回归:worktree 全量 654 passed / 18 failed。失败项均为隔离 worktree 环境(模板 CRLF SHA、USER300 校准哈希、OpenSees `NOT_INSTALLED`),不是 TRAFFIC 逻辑回归。合并回主工作区后应再跑全量。
- [x] `python -m compileall -q momo_agent/backend/app pyansys_bridge`。
- [x] 提交清单在保留原 dist 条目的前提下追加交通模板与测试,fileCount 458→461(基线 458 + 交通模板/测试/基线清单共 3 个文件)。worktree 无 `platform-ui/dist`,未跑通 `verify_submission.py`。
- [x] 前端不改动,跳过前端门(能力 API 数据驱动)。

### 4. 收尾

- [x] 分支提交:单笔 `66ebd54`(信息已说明 TRAFFIC 仅放行 ANSYS 单通道节点力时程与失败关闭边界)。
- [x] 更新本文件勾选状态。
- [ ] 更新父任务 implement.md §5 的 ANALYSIS 行(注明 TRAFFIC 已在分支完成):当前 §5 的 ANALYSIS 行仍未勾选、"本轮边界"仍写 TRAFFIC 不进入 LIVE 目录,待本次合并落地后一并改写。
- [ ] journal 记录会话;`trellis-check` 通过后归档留给合并后。
- [ ] 合并后在主工作区重跑全量测试与 `verify_submission.py`(§3 的 18 项失败与未跑通的清单校验都要在合并后复核,本分支未验证)。

合并说明(`feat/08-13-analysis-traffic-scenario` → 已含风工况四条链的 master,worktree `D:\helloagent-traffic-full`):§3 的清单行原先在两侧各有一份数字(HEAD 侧写 458→460,本分支写 461)。核对后保留 461:合并基线 `cbf2dd4` 的 `fileCount` 为 458,本分支只新增 3 个文件(交通模板、交通测试、基线清单示例),与本分支清单实际条目数一致;HEAD 侧的 460 来自另一条同期分支 `feat/08-13-unlock-standalone-surrogate-learning`(`c834379`,其清单不含任何交通文件),对本子任务不成立。风工况链把清单推到 480,合并后需重建清单,届时以 `_manifest_rebuild.py` 的结果为准。

## Validation Commands

```powershell
# 在 D:\helloagent-traffic\momo_competition_submission 下
D:\helloagent\momo_competition_submission\.venv\Scripts\python.exe -m pytest momo_agent/backend/tests/test_traffic_analysis_agent.py -q -p no:cacheprovider --basetemp D:\helloagent-traffic\.pytest-traffic-target
D:\helloagent\momo_competition_submission\.venv\Scripts\python.exe -m pytest momo_agent/backend/tests -q --tb=short -p no:cacheprovider --basetemp D:\helloagent-traffic\.pytest-traffic
D:\helloagent\momo_competition_submission\.venv\Scripts\python.exe -m compileall -q momo_agent/backend/app pyansys_bridge
D:\helloagent\momo_competition_submission\.venv\Scripts\python.exe _manifest_rebuild.py
D:\helloagent\momo_competition_submission\.venv\Scripts\python.exe verify_submission.py
```

## Risky Files / Rollback Points

- 高风险:`platform_store.py`(与风分支和 solver-batch 子任务三方都在改)、`registry.py`、`analysis.py`。
- 回滚:revert 分支提交即可;registry 条目是能力开关,EARTHQUAKE 行为不受影响。
- 禁止:修改 `D:\helloagent` 主工作区任何文件;把 WIND 功能带进本分支;放宽既有校验。
