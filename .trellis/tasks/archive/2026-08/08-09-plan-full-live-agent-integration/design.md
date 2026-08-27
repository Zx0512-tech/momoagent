# 智能体真实流程完整接入路线图

## 1. 现状能力矩阵

| 能力 | 当前实现 | 主要缺口 | 目标 |
| --- | --- | --- | --- |
| `ANALYSIS` | `REAL_AGENT_ANALYSIS` 可调用 ANSYS/OpenSeesPy 地震模板 | 仅单通道地震；通用 `SOLVER_BATCH` 仍 501 | 共享真实 solver/result 执行器，按 capability matrix 放行工况 |
| `DAMPER_COMPARISON` | `REAL_DAMPER_COMPARISON` 串行执行两个 ANSYS 地震案例 | 求解器/工况受限，阶段仍包在专用分支 | 两案例复用共享 solver/result，比较只做只读归并 |
| `DAMPER_OPTIMIZATION` / `FULL_OPTIMIZATION` | `REAL_BASELINE_OPTIMIZATION` 已调用真实 baseline-first pipeline | 平台分阶段 DOE/surrogate/AL/decision 仍有占位 | 将已验证原子 pipeline 分解为可恢复阶段并保持结果对等 |
| `RESULT_INQUIRY` | 已读取登记 CSV 并受 run 白名单约束 | 依赖上游真实结果目录完整性 | 所有真实执行器统一登记 result catalog 与单位元数据 |
| 独立平台入口 | 三个高风险入口 501；部分其他 Job 返回固定 preview/summary | 无法独立生产执行 | 复用与 Agent 相同的 handler，逐项解除 501 |

仓库已有可复用实现：`pyansys_bridge.batch`、`surrogate`、`active_learning`、`optimization` 和真实求解器适配器。`PlatformStore` 应回归状态/Artifact 持久化职责，不继续承载示例算法。

## 2. 目标架构

```text
Agent Workflow / Standalone API
            |
 typed Job request + approval/budget
            v
 RealJobExecutorRegistry
   load -> DOE -> solver batch -> result extraction
                    -> surrogate -> active learning
                    -> optimization/decision -> FEM review
            |
 Artifact + manifest + provenance + metrics
            v
 Workflow evidence gates / Result inquiry / Frontend
```

- `RealJobExecutorRegistry` 按 `JobType` 绑定类型化 handler；Agent 和专用 API 只构造同一请求模型。
- 每个 handler 只消费登记 Artifact ID 和冻结配置，返回 Artifact ID、SHA、单位/列目录、版本和资源用量。
- Job worker 提供进度、心跳、取消、超时和恢复；Workflow Guard 提供审批、预算、顺序和证据门禁。
- 保留现有 `REAL_*` 原子路径作为迁移对照，直到分阶段链的数值、制品和审计对等测试通过。

## 3. 分阶段实施

### Phase 0：能力契约与基线冻结

- 固化 JobType 请求/输出/Artifact schema、单位目录、solver capability matrix。
- 为现有三个 `REAL_*` 路径保存小型真实基准与输出 manifest，作为迁移金样。
- 将所有 placeholder/PENDING 分支加入禁止清单测试。

验收：Live 能力目录与真实 handler 一一对应；未登记 handler 创建 Job 前返回 501。

### Phase 1：共享求解与结果基座

- 抽取 `SOLVER_BATCH` 真实 handler，复用现有 ANSYS/OpenSeesPy adapter、worker、超时和取消。
- 实现 `RESULT_EXTRACTION`：只从真实 solver output manifest 读取响应，生成带单位的 CSV/result catalog。
- 让 `ANALYSIS` 和 `DAMPER_COMPARISON` 先切换到共享 handler；比较阶段只消费两个结果目录。

验收：同一冻结输入在旧 `REAL_*` 和新分阶段链的关键响应、SHA/来源、成功/取消行为满足对等阈值；随后解除独立 `SOLVER_BATCH` 501。

### Phase 2：真实 DOE 与训练数据集

- 使用现有确定性 DOE 生成器创建设计 Artifact；每个设计映射为受预算约束的 solver Job。
- 从真实结果提取构建 `SurrogateDataset`，拒绝缺列、非有限值、单位不一致和未验证求解结果。
- 删除 `doe-surrogate-model-placeholder`、固定响应 preview 和 `realFemExecution=PENDING_REPLACEMENT`。

验收：设计数、实际求解数、失败样本、dataset SHA 和来源完全一致；任何缺样本不得伪造补齐。

### Phase 3：真实代理模型与主动学习

- 用 `pyansys_bridge.surrogate` 训练已登记模型族，持久化真实模型文件、版本、特征/目标 schema、CV 指标和选择依据。
- 用 `pyansys_bridge.active_learning` 选择 infill 点；每个点回到 Phase 1 执行真实求解，并受批准预算约束。
- 删除假 `.pkl` 字节和固定 R²/RMSE/CV；达到门槛或预算耗尽时结构化终止。

验收：模型可重新加载并复现预测；CV 来自保存的 fold；infill 点无重复且真实求解数不超预算。通过后解除 `SURROGATE_TRAINING`、`ACTIVE_LEARNING` 501。

### Phase 4：真实优化、决策与 FEM 复核

- 接入现有 NSGA-II/Pareto、约束、熵权 TOPSIS 与 explainability。
- 推荐候选必须通过独立真实 FEM validation；误差不达标按既有最多一轮 review 修正。
- `MULTI_OBJECTIVE_OPTIMIZATION`、`ENTROPY_TOPSIS_DECISION`、`OPTIMIZATION_EXPORT` 只消费登记数据/模型/候选制品。

验收：候选来源、约束、权重、排名、FEM 误差和最终推荐全部可由 Artifact 重算；不存在固定 `topsis_result()` 示例。

### Phase 5：全部 Agent 与工况接入

- `ANALYSIS`：地震、风、交通、风车组合分别通过真实载荷施加与结果目录验收。
- `DAMPER_COMPARISON`：黏滞/电涡流/摩擦组合复用相同标定与双案例真实执行；不支持的求解器组合从能力目录隐藏。
- `DAMPER_OPTIMIZATION`：共享 Phase 1–4 的分阶段链。
- `FULL_OPTIMIZATION`：支持已验收场景的联合目标与场景权重，不再依赖硬编码 ANSYS 地震动作。
- `RESULT_INQUIRY`：覆盖统一 result catalog，并保留制品白名单和只读属性。

验收：每个广告的 solver × scenario × damper 组合至少有一个真实 smoke、一个失败关闭测试和一个取消/恢复测试；否则明确标为不支持。

### Phase 6：前端启用与生产发布

- 前端从 capability API 获取可用组合，不在源码中假定可用。
- 逐项启用 solver/surrogate/active-learning 页面；Mock 继续隔离并显著标识。
- 增加运行阶段、预算、失败原因、Artifact 来源和验证状态展示。

验收：Live 页面无法触发未登记能力；端到端浏览器测试完成上传→审批→运行→结果→报告→追问。

## 4. 运行与回滚门

- 每个 Phase 独立 feature flag；失败时回到 501/禁用，不回到占位成功。
- CI 使用确定性 fake solver 验证编排，OpenSeesPy 执行最小真实 smoke；ANSYS 作为具备许可证环境的发布认证门。
- Job 幂等键、预算、取消、worker 心跳、Artifact manifest 和结构化错误是所有阶段的共同完成条件。
- 分阶段链达到与现有原子 `REAL_*` 数值和证据对等后，才移除旧专用分支。
