# 阻尼器参数扫描的进程并发与进度设计

## Scope

本设计只覆盖受控 Agent 的 `DAMPER_PARAMETER_SWEEP` / `REAL_DAMPER_PARAMETER_SWEEP`。不改变独立平台 API、ANSYS 许可证策略或既有 Job 轮询接口。

## Execution Boundary

```text
Dispatcher worker process
  -> job executor process
    -> ProcessPoolExecutor (OpenSeesPy: one isolated process per active case)
      -> OpenSeesPy in-process domain
```

`PlatformStore._generate_real_damper_parameter_sweep_artifacts` 仍由父进程创建每个 case 的冻结配置、收集 future 的结构化结果、登记 Artifact 和写批次完成数。OpenSeesPy case 的实际求解改由模块级、可序列化的 worker 函数执行；该 worker 只接收配置路径和超时，使用既有 `RealSolverExecutor` + `run_solver_acceptance_case_config` 返回 JSON 可序列化结果。

OpenSeesPy 的 executor 为 `ProcessPoolExecutor(max_workers=min(requestedMaxConcurrentCases, caseCount))`；ANSYS 继续为现有 `ThreadPoolExecutor`，并保持许可证 cap 与重试。`DAMPER_PARAMETER_SWEEP` 未传 `maxConcurrentCases` 时，意图模型、API 请求模型、合同构造与审批更新均使用默认值 4，并继续校验 1–8。输出摘要把 OpenSeesPy 的 `parallelism.mode` 记录为 `process_pool`，并保留 requested/effective 并发数作为审计证据。

## Progress Contract

现有 REST 响应 `jobProgress` 不增删字段：

```ts
{
  phase: string,
  percent?: number,
  completedCases?: number,
  totalCases?: number,
  activeCases?: Array<{ caseId: string, percent: number, ... }>
}
```

- `percent` 始终是整批案例完成率；例如 4 个案例中 1 个已到 90% 时为 22%。
- `activeCases[].percent` 是各案例自己的瞬时进度。
- `activeCases[].caseId` 必须是冻结扫描合同的业务 `caseId`，不是 `build_case_id()` 生成的内部求解 ID。
- 进程池中的每个案例向同一 job progress 目录写自己的原子 JSON 文件；文件名按业务 `caseId` 隔离。完成计数仍只由父进程写入 `_batch.json`。
- 前端在已有完整批次计数时只渲染结构化计数和活动案例，不再渲染包含相同计数的 `message`；非批量 Job 继续显示 `message`。

为保持其他调用方兼容，`OpenSeesPyInProcSolver` 新增可选的 `progress_case_id` 构造参数。未设置时沿用当前内部 case ID；受控参数扫描显式设置时用该值渲染进度文件，不参与 case fingerprint 或数值求解。

## Failure, Timeout, and Cancellation

- 任何 worker future 的非许可证异常仍令批次失败关闭；不把错误案例伪装成完成。
- Job 级墙钟超时和取消不新建第二套控制面：现有 dispatcher 对 executor 进程调用 `terminate_process_tree`，该树会包含 ProcessPoolExecutor 子进程。
- 每 case 仍接收既有 `executionTimeoutS`；Job 级墙钟依旧是总时限的权威兜底。
- 所有子进程仅写自己的 case 目录和自己的进度文件，父进程才登记平台状态/Artifact，避免 SQLite 并发写入。

## Compatibility and Rollback

- `jobProgress` 的 JSON shape、轮询频率、ANSYS 逻辑和无 `progress_case_id` 的 OpenSeesPy 调用保持兼容。
- 回滚仅需将 OpenSeesPy executor 选择恢复为单 worker；不会迁移数据库或制品格式。
- 已运行 Job 不重放；变更仅影响后续提交的扫描任务。

## Risks

| Risk | Mitigation |
| --- | --- |
| Windows spawn 无法 pickle 局部闭包 | 使用模块级 worker 函数，只传 `Path`/标量参数和可序列化结果。 |
| 多案例进度覆盖 | 显式传业务 `progress_case_id`，每案例独立原子 JSON 文件。 |
| 取消留下 solver 子进程 | 复用并验证 dispatcher 的进程树终止路径。 |
| CPU / memory 争用 | 使用审批冻结的最大并发数，且不超过案例数量；不改变 ANSYS license cap。 |
