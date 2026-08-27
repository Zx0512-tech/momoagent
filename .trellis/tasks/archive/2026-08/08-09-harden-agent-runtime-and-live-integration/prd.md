# 强化智能体运行安全并规划真实流程接入

## Goal

消除工程报告中的非标准 JSON 和终态刷新永久重试，使失败运行可观察、可终止；随后基于仓库已有真实求解、批处理、代理模型、主动学习与优化模块，给出覆盖全部智能体能力的真实流程接入路线。

## Background

- `agent_service.py:1011/1135/1349/1729` 的 JSON 制品写入均使用默认 `json.dumps`，未禁止 `NaN`、`Infinity` 和 `-Infinity`。
- `get_run()` 在 `agent_service.py:872` 无保护地调用 `_refresh_agent_run()`；后者的 review、report 和 Artifact 注册异常会让 Job 已终态但 run 继续停留在轮询路径。
- 当前受控真实入口包括 `REAL_AGENT_ANALYSIS`、`REAL_DAMPER_COMPARISON` 和 `REAL_BASELINE_OPTIMIZATION`；独立 `SOLVER_BATCH`、`SURROGATE_TRAINING`、`ACTIVE_LEARNING` 仍失败关闭，其他若干平台阶段仍返回占位内容。
- `pyansys_bridge` 已提供 batch、surrogate、active_learning、NSGA-II、FEM review 和 TOPSIS 等可复用实现。

## Requirements

- R1：所有 `agent_service` 生成的 JSON 报告在预览与文件字节中都不得含非有限浮点字面量；非有限值显式转为 JSON `null`，最终序列化使用 `allow_nan=False` 再次失败关闭。
- R2：工程 Job 终态后的 review/build_report/register_artifact 异常必须把 run 收敛到结构化 `FAILED`，保留已完成步骤且不伪造报告完成；同一 run 后续读取不得重复执行确定性失败阶段。
- R3：服务端日志保留异常堆栈，API/run 只暴露稳定错误码、安全消息、失败阶段和异常类型，不泄漏堆栈或本地路径。
- R4：真实流程计划必须覆盖 `ANALYSIS`、`DAMPER_COMPARISON`、`DAMPER_OPTIMIZATION`、`FULL_OPTIMIZATION` 和 `RESULT_INQUIRY`，并覆盖它们依赖的载荷、DOE、批量求解、结果提取、代理模型、主动学习、优化决策、FEM 复核、报告与图件。
- R5：Agent 与独立平台入口必须复用同一套类型化真实执行器；任何能力只有在制品、证据和回归门齐备后才从 501/禁用态切换为 Live。
- R6：先完成并验证运行安全修复，再交付真实流程路线图；路线图不等同于本轮一次性实现所有数值模块。

## Constraints

- 不把 `NaN`/`Infinity` 转成字符串，也不在 Artifact 存储层悄悄修改所有调用方输出。
- 不改写历史工作流快照，不增加数据库迁移。
- 本轮不执行耗时的真实 ANSYS 全量计算；运行安全使用确定性测试替身与现有集成测试验证。
- 保持当前受控真实地震流程可用，未达到真实验收门的入口继续失败关闭。

## Acceptance Criteria

- [x] 浏览器兼容解析器可读取包含原始 `nan/inf/-inf` FEM 值的最终报告，预览和字节对应位置均为 `null`。
- [x] review、build_report、register_artifact 任一异常均返回已持久化的 `FAILED` run，`workflowGateError.code=REPORT_GENERATION_ERROR`，且第二次 `get_run()` 不再重放失败阶段。
- [x] 后端定向测试、完整 pytest、compileall 和 `verify_submission.py` 通过。
- [x] 真实流程计划包含现状差距、目标架构、阶段顺序、每阶段验收门、回滚策略以及取消 501/前端禁用的条件。
- [x] 计划明确所有已广告能力最终只能有两种状态：真实可执行并有证据，或从 Live 能力目录移除/明确不支持；不得保留伪成功。

## Out of Scope

- 本轮直接实现全部独立平台数值阶段。
- 为无对应物理模型的求解器/工况组合制造模拟替代品。
- 清理与这两个可靠性缺陷无关的既有告警。
