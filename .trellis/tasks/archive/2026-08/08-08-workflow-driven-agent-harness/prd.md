# 流程驱动型工程智能体 Tool Harness 改造

## 目标

把当前“LLM 分类、Python 固定分支执行”的系统改造成流程驱动的工具智能体：LLM 负责理解、澄清、当前步骤内的工具选择和结果解释；Python 类型化工作流、Harness、审批和证据门禁负责顺序、权限与真实性。

## 范围

- 在 Git 根目录使用 Trellis 管理本次改造。
- 建立 Python `WorkflowDefinition`、冻结快照、`WorkflowGuard` 和结构化流程状态。
- 为模型请求提供稳定系统提示词、固定顺序工具定义和追加式工作流状态。
- 持久化工具调用、缓存命中统计和可恢复的 Job 状态。
- 逐步接入结果追问、单次分析、阻尼器对比和阻尼优化工作流。
- 前端显示流程阶段、工具执行、审批和等待 Job 状态。
- 迁移期保留 `LEGACY` 与 `WORKFLOW_HARNESS` 开关。

## 强制约束

- Python 工作流定义是唯一事实源；不维护 YAML 流程副本。
- 模型不得越步、扩大审批范围、重复创建 Solver Job 或绕过证据验证。
- 无阻尼分析固定一次真实求解，不含 DOE 或候选点预算。
- 系统提示词和工具 Schema 顺序保持稳定，动态状态仅追加到上下文末尾。
- 工具输入和输出均通过 Pydantic 校验；副作用工具要求审批和幂等键。
- LLM 不可用时返回明确失败，不进入关键字语义兜底。

## 不在范围

- 不向模型开放 Shell、任意文件路径、任意节点或原始求解器命令。
- 不修改 DOE、代理模型、Pareto/TOPSIS 或 FEM 算法本身。
- 不以提示词代替 Python 运行时授权。

## 验收标准

- [x] 工作流快照具有稳定版本和 SHA256，运行中不随代码更新漂移。
- [x] 越步工具调用返回 `WORKFLOW_STEP_VIOLATION`，不会执行 handler。
- [x] 相同无进展调用两次后返回 `HARNESS_LOOP_DETECTED`。
- [x] 模型原生 `tool_calls` 能被解析，工具列表和系统提示词保持稳定。
- [x] `agent_tool_calls` 可保存、更新、查询，重启后不会重复副作用。
- [x] 无阻尼分析预算为 `realSolveCount=1`，不存在 DOE 和候选字段。
- [x] 优化流程严格遵守 baseline、DOE、surrogate、active learning、candidate、validation、review 顺序与既有上限。
- [x] 结果追问只查询已登记制品。
- [x] 前端可见当前步骤、完成进度、工具状态和审批/Job 等待状态。
- [x] 后端全量测试、前端测试/lint/build/bundle 和提交清单验证全部通过。
