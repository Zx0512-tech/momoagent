# 对齐能力目录与工况矩阵

## Goal

能力目录只广告已通过真实 smoke 的 solver × scenario × damper 组合。实现已有但未登记的路径按验收结果登记；没有物理模型的组合保持隐藏或明确不支持，不得由模型自由承诺。

## Background / Confirmed Facts

- LIVE Agent 目录目前只广告 EARTHQUAKE 受控组合。
- `DAMPER_COMPARISON` 目录 solvers 仅为 `ANSYS`，代码已有 `OPENSEESPY_INPROC` 模板和测试，存在目录漂移。
- 独立 PLATFORM_API 条目仍列出 WIND/TRAFFIC，但 Agent LIVE 未放行这些工况。
- 父任务约束：不承诺没有物理实现的组合上线。

## Requirements

- 目录中的 solvers/scenarios 必须与真实 handler 支持矩阵一致。
- OpenSees 阻尼器对比：有真实 smoke、失败关闭、取消测试则登记 LIVE；否则从实现广告和模型目录中移除，避免承诺。
- WIND、TRAFFIC、组合工况：若无绑定荷载与真实 smoke，不进入 Agent LIVE 目录；独立入口不得把模板工况显示为可用。
- 五类 Agent 每个已广告组合保留至少一个真实 smoke、一个失败关闭和一个取消/恢复（或明确不支持恢复）测试。

## Acceptance Criteria

- [ ] `GET /api/v1/capabilities` 与代码支持矩阵一致，无“目录说没有、实现能跑”或相反。
- [ ] 不支持组合从 Live 目录隐藏或标记 `DISABLED`，前端不显示入口。
- [ ] 已广告组合均有 smoke / 失败关闭 / 取消测试。

## Constraints

- 不新造风/交通物理模型。
- 不把未验收组合标 LIVE 以填满矩阵。

## Out of Scope

- 独立 501 解锁（由前两个子任务负责）。
- 浏览器 E2E。
