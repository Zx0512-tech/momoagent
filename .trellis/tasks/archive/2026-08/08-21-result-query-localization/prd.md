# 结果查询指标中文化与链路核查

## Goal

让结果查询卡片中的 TOPSIS 表格以中文工程名称显示“目标指标”，同时明确当前结果查询的数据获取与 LLM 参与边界，避免把结果误解为 LLM 改写或重新计算。

## Confirmed Facts

- TOPSIS 表格位于 `platform-ui/src/pages/chat/cards/ResultCard.tsx`。其“目标指标”单元格当前通过 `formatKeyValueMap(row.objectives)`直接拼接键名，因而会展示 `earthquake:max_girder_end_displacement` 等内部标识。
- 同一组件已有 `OBJECTIVE_META`，已定义梁端位移、塔底剪力和塔底弯矩等中文显示名；`topsisObjectiveLabel` 也已将决策权重显示为中文。
- 结果查询先由 LLM 通过 `planner.run_harness_turn` 选择只读工具及制品；随后后端直接调用 `InquiryTools` / `ResultInquiryService.topsis`，从已登记且 SHA256 校验通过的优化摘要 JSON 读取 TOPSIS 行，不重新求解或生成查询代码。
- 工具返回的结构化行会立即写入 `resultSummary.inquiryTopsis`，由前端表格直接展示。LLM 仅负责工具选择和可选的文字叙述；工具调用后 LLM 不可用或叙述数字不受证据支持时，系统使用确定性说明保留结构化结果。

## Requirements

1. TOPSIS 表格的“目标指标”列对已知工程指标显示中文名称；带工况前缀的标识也必须正确转换。
2. 指标的数值、TOPSIS 排名、得分和参数展示保持不变；未知指标仍回退展示原标识，避免丢失信息。
3. 为此展示行为补充前端回归测试。
4. 不改变结果查询的 LLM 路由、只读工具查询、证据校验或叙述回退机制；本任务记录并交付其现有行为结论。

## Acceptance Criteria

- [x] 当 TOPSIS 行包含 `earthquake:max_girder_end_displacement`、`earthquake:max_tower_base_shear` 和 `earthquake:max_tower_base_moment` 时，目标指标单元格显示对应中文名称，而非内部英文标识。
- [x] 指标值、参数、排名与得分的显示内容不因中文化而变化。
- [x] 未配置中文名的指标仍能显示其原始键名。
- [x] ResultCard 相关前端测试通过，且新增回归断言覆盖 TOPSIS 表格中文化。
- [x] 交付说明准确描述当前查询链路：LLM 选择工具与生成叙述，确定性后端读取已登记制品并由前端直接渲染结构化结果。

## Out of Scope

- 将所有结果查询改为完全不经 LLM 的意图路由。
- 改变 TOPSIS 计算、优化求解、数值单位或数据源。
- 修改表格的参数列、排名列或得分列。

## Risks and Compatibility

- 指标键可能带 `scenario:` 前缀；显示转换必须先提取实际指标键。
- 仅在前端显示层转换，保持后端结构化契约与审计数据的稳定性。
