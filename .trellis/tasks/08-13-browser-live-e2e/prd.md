# 补齐 Live 浏览器端到端

## Goal

增加浏览器端到端测试：上传 → 审批 → 执行 → 结果 → 报告 → `RESULT_INQUIRY`，作为父任务发布验收门。

## Background / Confirmed Facts

- 前端已有 capability API、Live/Mock 标识、Vitest 约 50 条；没有 Playwright 业务 E2E。
- 父任务 AC 要求生产构建和浏览器端到端链路通过。
- 后端已有 NDJSON 流式追问和终态直接查询。

## Requirements

- E2E 走 Live 客户端，打真实本地 API（可用确定性 fake solver / OpenSeesPy 最小模型，禁止 Mock 数据冒充）。
- 覆盖一条已 LIVE 的地震分析或结果追问主路径；审批、执行终态、结果卡结构化指标、跨消息追问。
- 失败时断言结构化错误可见，不出现占位成功或内部校验文案。

## Acceptance Criteria

- [ ] 至少一条完整浏览器路径：上传或内置荷载 → 审批 → 执行完成 → 报告/结果卡 → 追问峰值。
- [ ] Live 页面显示真实执行状态；Mock 路由不在该测试中当作成功。
- [ ] 前端 `tsc -b`、Vitest、oxlint、生产构建与该 E2E 在 CI/本地可重复运行。

## Constraints

- 不依赖 ANSYS 许可证。
- 不在 E2E 里解锁新能力；只测已经 LIVE 的路径。

## Out of Scope

- 独立平台全部 JobType 的 UI 穷举。
- 多浏览器矩阵。
