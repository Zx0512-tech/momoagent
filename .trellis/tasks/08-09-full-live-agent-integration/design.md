# 智能体真实流程接入技术设计

## 1. 设计边界与迁移原则

当前 `PlatformStore` 同时承担 Job 状态、Artifact 持久化、真实 `REAL_*` 编排和占位分支。迁移采用“共享执行器先行、原子路径保留对照、能力逐项放行”的方式：

1. 新增真实执行器注册表和类型化上下文，不让 Agent 或 API 直接调用 `PlatformStore` 内部数值分支。
2. 将已有 `REAL_AGENT_ANALYSIS`、`REAL_DAMPER_COMPARISON`、`REAL_BASELINE_OPTIMIZATION` 作为数值/证据金样，迁移后的分阶段链必须与其关键响应和来源 manifest 对等。
3. 每个阶段通过 feature flag/capability 状态开启；未通过门禁时返回 501 或在 Live 能力目录隐藏，不能回退到占位成功。
4. 不改变历史冻结工作流快照；新执行器和工作流版本使用兼容扩展字段。

## 2. 目标模块与职责

```text
Agent workflow / dedicated API / Live UI
                 |
                 v
       typed request model + approval/budget
                 |
       RealJobExecutorRegistry
  load -> DOE -> solver batch -> result extraction
             -> surrogate -> active learning
             -> optimization/decision -> FEM review
                 |
          Job state + Artifact manifest
                 |
       Workflow evidence gates / Result inquiry / UI
```

建议新增 `app/services/real_execution/`：

- `contracts.py`：请求、上下文、结果、能力状态和 manifest 的 Pydantic 模型，所有命令型字段 `extra='forbid'`。
- `registry.py`：`JobType`、solver、scenario、mode 到 handler 的唯一注册表；未注册组合返回 `CAPABILITY_NOT_IMPLEMENTED`。
- `base.py`：`execute`, `cancel`, `resume`, `preflight` 生命周期和结构化错误协议。
- `solver_executor.py`：复用 `pyansys_bridge.batch`、现有 ANSYS/OpenSeesPy adapter 和 worker，负责真实求解批次、心跳、超时、取消和恢复。
- `result_executor.py`：只读取 solver output manifest，生成带单位的 CSV/result catalog 和来源关系。
- `doe_executor.py`：复用确定性 DOE 生成器，产出设计集、预算和设计集 SHA-256。
- `surrogate_executor.py`：复用 `pyansys_bridge.surrogate`，持久化模型字节、版本、特征/目标 schema、fold 和 CV 指标。
- `active_learning_executor.py`：复用 `pyansys_bridge.active_learning`，验证新增点不重复且回到 solver executor。
- `optimization_executor.py`：复用 NSGA-II/Pareto、熵权 TOPSIS、约束和 FEM review。

`PlatformStore` 只保留 Job/Artifact 状态和兼容 facade；AgentService、v1 router 和前端只通过 registry 访问真实 handler。

## 3. 核心数据契约

### 3.1 RealJobRequest

```json
{
  "jobType": "SOLVER_BATCH",
  "runId": "optional-agent-run",
  "source": "AGENT|PLATFORM_API",
  "solver": "ANSYS|OPENSEESPY_INPROC",
  "scenario": "EARTHQUAKE|WIND|TRAFFIC|GENERIC_NODAL",
  "inputArtifactIds": ["artifact-load", "artifact-model"],
  "frozenConfig": {},
  "budget": {"realSolveCount": 15},
  "idempotencyKey": "sha256(...)"
}
```

输入只接受登记 Artifact ID 和审批冻结配置，不根据文件名猜测来源，不静默删除未知字段。

### 3.2 RealJobResult / output manifest

```json
{
  "status": "SUCCEEDED|FAILED|CANCELLED",
  "artifactIds": ["artifact-result-catalog", "artifact-output-manifest"],
  "usage": {"realSolveCount": 15, "durationS": 12.3},
  "manifest": {
    "inputArtifactIds": [],
    "outputArtifactIds": [],
    "solver": "OPENSEESPY_INPROC",
    "solverVersion": "...",
    "designSetSha256": "..."
  },
  "error": null
}
```

每个输出 Artifact 必须记录 `runId/jobId/sourceArtifactIds/sha256/mimeType/units/columns/producerVersion`。结果提取拒绝未完成、非有限或缺少 manifest 的求解输出。

### 3.3 CapabilityStatus

能力目录至少返回 `jobType`、solver、scenario、mode、`status`（`LIVE`/`MOCK_ONLY`/`DISABLED`）、输入要求、输出制品、预算上限、取消/恢复支持和解除 501 的验收版本。Live UI 只显示 `LIVE`。

## 4. 数据流和审批边界

1. Agent 规划阶段构造 typed request，并将荷载、模型、冻结参数和预算写入审批快照。
2. `preflight` 校验文件、schema、solver 可用性、单位、能力矩阵和预算，不创建 Job。
3. 审批通过后由 registry 创建 Job；幂等键由冻结输入和审批版本派生，模型不得改写。
4. Worker 通过 handler 产生真实 Artifact；每个阶段完成后保存 Job 状态、manifest 和 usage。
5. WorkflowGuard 只推进已满足的 gate；证据审查读取真实 manifest/result catalog，报告只引用登记 Artifact。
6. Result inquiry 只读当前运行白名单内的 result catalog，不读取工作目录任意文件。

## 5. 分阶段迁移与放行门

### Phase 0：能力契约与基线

冻结 schema、单位目录、solver/scenario/damper capability matrix，登记三条 `REAL_*` 金样，加入 placeholder 禁止清单和能力 API。验收后只有有 handler 的组合进入 `LIVE`。

### Phase 1：共享 solver/result 基座

先迁移 `ANALYSIS` 和 `DAMPER_COMPARISON` 到 solver/result executor；比较仅消费两个真实结果目录。取消、超时、恢复、manifest 和来源 SHA 必须与原子路径对等。通过后解除受控 `SOLVER_BATCH` 之外的对应 501。

### Phase 2：真实 DOE 与数据集

将 DOE 设计、真实求解批次、结果提取和训练数据集连接起来。删除固定响应 preview、`PENDING_REPLACEMENT` 和不真实的 DOE surrogate seed。设计数量、求解总数、失败样本和 dataset SHA 必须一致。

### Phase 3：真实 surrogate 与 active learning

接入模型训练、fold/CV、模型文件和 infill 选择。每个 infill 点必须重新调用 Phase 1 solver，并受初次审批预算限制。通过可重载、可复现和不超预算门后解除 `SURROGATE_TRAINING`、`ACTIVE_LEARNING` 501。

### Phase 4：真实优化、决策、FEM review

将候选排序、约束、NSGA-II/Pareto、TOPSIS、解释性和独立 FEM 验证接入登记制品。推荐必须可重算，review 失败走结构化回退，不能写固定 R²/RMSE/FEM 数值。

### Phase 5：五类 Agent 与场景矩阵

逐项接入 `ANALYSIS`、`DAMPER_COMPARISON`、`DAMPER_OPTIMIZATION`、`FULL_OPTIMIZATION` 和 `RESULT_INQUIRY`，覆盖地震、风、交通、风车组合和已支持的 ANSYS/OpenSeesPy 路径。每个组合需要真实 smoke、失败关闭、取消/恢复测试；没有物理模型的组合下架。

### Phase 6：Live 前端和发布

前端从 capability API 动态生成入口，Live 页面显示进度、预算、来源 SHA、失败原因和验证状态；Mock 页面保留但显示模拟标识。端到端覆盖上传→审批→执行→结果→报告→追问。

## 6. 兼容、回滚和运行控制

- 保留 `REAL_*` 旧路径和旧报告字段，新增 provenance/usage/capability 字段采用可选扩展。
- 每个 Phase 有独立 feature flag；任何真实 smoke 或证据回归失败时回到 `DISABLED`/501，而不是占位成功。
- ANSYS 仅在具备许可证的发布环境通过认证门；CI 使用 fake solver 和 OpenSeesPy 最小 smoke。
- Job 幂等、预算、取消、心跳、恢复和 Artifact manifest 是所有阶段的共同完成条件。
