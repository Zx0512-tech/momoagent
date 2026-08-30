# MOMO 智能体优化说明书

目标：把当前"表单式槽位 + 布尔审批 + 有限追问"的任务型对话系统，改造成三段式自然语言闭环——对话确定参数、自然语言方案与意图审批、结果全自然语言化并支持按需绘图。

编写日期：2026-08-08。基线代码为本目录当前状态（追问链指标表、跨 run 作用域、审批修改端点已完成）。

---

## 0. 现状定位

系统是**任务型对话系统（TOD）+ 受约束工作流编排**，不是工具挂载型智能体。控制权在确定性 Python，LLM 只在固定点被调用做 NLU 和 NLG，从未见过工具 schema。

这个定位在有限元这类高后果领域是正确的，本说明书**不改变它**。三个阶段都是在这个范式内把 NLU 和 NLG 做厚，控制权仍留在代码里。

已具备的基础，改造时直接复用，不要重写：

| 能力 | 位置 | 状态 |
|---|---|---|
| LLM 输出 schema 锁定 | `EngineeringIntent`（`extra='forbid'`） | 可用 |
| 工程数值来自常量表 | `agent_engineering.py:11-73` | 可用 |
| frozenAction SHA256 冻结 | `agent_service.py:_payload_sha256` | 可用 |
| 数字溯源门禁 | `agent_llm.py:numbers_are_grounded` | 可用，需修（见 1.4） |
| 反造假证据门禁 | `agents/evidence_gates.py` | 可用 |
| CSV 只读查询 + SHA256 校验 | `result_inquiry.py` | 可用 |
| 语义指标表 | `inquiry.py:RESPONSE_METRIC_SPECS` | 可用 |
| **matplotlib 渲染服务** | `agent_figure_service.py` | **可用，仅缺自然语言入口** |
| **审批修改端点** | `agent_router.py:68` + `update_approval` | **已完成** |
| 类型化工具注册表 | `agents/tools.py`、`capabilities.py` | 死代码，本次不启用 |

两处需要先修的回归（见 1.4、1.5），它们是上一轮改动的副作用，优先级高于新功能。

---

## 阶段一：对话式参数确定

### 1.1 问题

`missing_fields` 只检查两个槽位（`agent_llm.py:_finalize_engineering_intent`）：`DAMPER_OPTIMIZATION` 缺 damperType、`DAMPER_COMPARISON` 缺两种 damperTypes。其余参数静默取默认：

- `solver` 默认 `ANSYS`（`agent_engineering.py:82`）——**在只交付 OpenSees 路径的提交版里这个默认值是错的**
- `load_kind` 兜底 `EARTHQUAKE`
- `selected_layout_id` 硬编码 `TWO_PER_TOWER`（`agent_engineering.py:161`）
- `budget`、`response_ids` 从未询问

用户说"帮我做个地震分析"，系统一句不问就冻结方案送审批。这是"对话确定需求"名不副实的根因。

### 1.2 槽位分级

不要把所有槽位都设为必问——那会把澄清拉到四五轮，工程师会烦。分三级：

**必问（REQUIRED）**：无合理默认，答错代价高。
- `solver`：提交版应改默认为 `OPENSEESPY_INPROC`，但仍必问确认
- `damper_type` / `damper_types`：已有，保留

**推荐问（SUGGESTED）**：有合理默认，但首轮应主动提及并允许一次性确认。
- `load_kind`、`selected_layout_id`、`response_ids`

**不问（DEFAULTED）**：默认值即最佳实践，仅在方案里标注。
- `budget`（DOE 预算等）、`budget_profile`

实现：在 `agent_engineering.py` 加槽位规格表，`_finalize_engineering_intent` 按级别决定是否进 `missing_fields`。

```python
SLOT_SPECS: dict[str, dict[str, Any]] = {
    'solver': {
        'level': 'REQUIRED',
        'label': '求解器',
        'options': ENGINEERING_SOLVERS,
        'default': 'OPENSEESPY_INPROC',
    },
    'loadKind': {'level': 'SUGGESTED', 'label': '荷载类型', 'default': 'EARTHQUAKE'},
    'selectedLayoutId': {'level': 'SUGGESTED', 'label': '阻尼器布置', 'default': 'TWO_PER_TOWER'},
    'responseIds': {'level': 'SUGGESTED', 'label': '关注响应量', 'default': None},
    'budget': {'level': 'DEFAULTED', 'label': '计算预算'},
}
```

`EngineeringIntent` 需要新增 `selected_layout_id` 字段（当前不存在，布置只在契约层硬编码），并在 `STBRIDGE_LAYOUTS` 键上加 `Literal` 约束。

### 1.3 一次问全，不要逐槽追问

关键设计决策：`missing_fields` 有多项时，**生成一条包含全部缺失项的提问**，而不是一轮问一个。

LLM 侧新增 `ask_for_slots(missing_slots, prior_intent, goal)`，输出 `{"text": "..."}`，system prompt 要求：把缺失槽位组织成一句自然提问，REQUIRED 项必须明确询问，SUGGESTED 项以"默认用 X，如无异议我就按此执行"的形式给出。

这样典型交互是两轮：用户提需求 → 系统问一次 → 用户答 → 进方案。

### 1.4 【必做·回归】数字护栏加长度门槛

上一轮给 `numbers_are_grounded` 加了舍入容差（`agent_llm.py:152-168`），逻辑本身正确，但与 facts 膨胀叠加后门禁实质失效。

实测：`allowed` 从 7 个值涨到 150 个后，量化到 1 位小数可达 116 个互异值，覆盖 0.2/0.3/…/2.7 连续区间；整数可达 96 个。编造的 `0.5`、`1.6`、`5`、`9`、`12` 全部通过。

（数据来自基于仓库真实 `summary.json` / `timeseries.csv` 构造的 facts，非跑通完整链路，量级可信、具体数字可能有偏差。）

`numbers_are_grounded` 被三处共用（`agent_llm.py:293` / `:347` / `:432`），所以工程结果叙述和审批说明的门禁**同时**被松掉，不只追问链。

两项修复：

1. **容差只对长数字开放**：要求 fact 原值有效数字 ≥ 4 位才允许量化匹配，短值走精确匹配。`0.135040 → 0.135` 能过，`2` 不给 `1.6` 背书。
2. **计数字段不进 allowed**：`_collect_fact_numbers` 按 key 名跳过 `sampleIndex`、`sampleCount`、`physicalCountPerTower`、`totalCheckCount`、`passedCheckCount` 等。

阶段三会让 facts 进一步变大，所以这项必须先做。

### 1.5 【必做·回归】catalog 体积上限

`_create_inquiry_run` 遍历全部可追问 run 且无上限（`agent_conversation.py:257-285`）。实测单 run 条目约 17 KB（`sampleResponses` 占 12.7 KB）：1 run≈16K token，3≈28K，5≈39K，8≈56K。叠加 `priorResults` 线性累积（每轮整份嵌套无截断，第 6 轮 +38K），5 run + 5 轮追问达 70K token 量级。

`max_tokens: 800` 只限补全不限 prompt；`question` 截了 4000 字符，catalog 和 priorResults 完全不截。32K 上下文的模型在第 2 个 run 后即溢出 → `_request` 收 4xx → `LLM_SERVER_ERROR`。而 `plan_inquiry` **没有** try/except 兜底（不像 `explain_inquiry`），`agent_conversation.py:291` 直接向上抛，整条追问链不可用。

四项修复：

1. `catalog['runs']` 限最近 3 条
2. `runs[]` 跳过 source_run——当前 `:261` 只在合并 artifact 时跳过，source run 数据重复了两份
3. `priorResults` 只保留上一轮的 `objectives` + `queries` 摘要，不整份嵌套
4. `plan_inquiry` 加 try/except，与 `explain_inquiry` 对齐

### 1.6 验收

- 单元：REQUIRED 槽位缺失 → `missing_fields` 含该项且 `task_type='CLARIFICATION'`；SUGGESTED 缺失 → 进 `missing_fields` 但标注 default；DEFAULTED 永不进
- 单元：`numbers_are_grounded('位移 0.5 m', facts_with_150_values)` 为 False；`('0.135', {'v': 0.135040})` 为 True
- 单元：8 个可追问 run 时 `len(catalog['runs']) == 3` 且不含 source_run
- 集成：一轮提问后补齐全部槽位 → 直接进 `WAITING_APPROVAL`
- 回归：`test_result_inquiry.py` 全绿

---

## 阶段二：自然语言方案 + 意图审批

### 2.1 现状

审批说明已由 LLM 生成（`describe_pending_action`），但偏简短，且只描述"要执行什么"，不解释为什么这样配、有什么代价。

审批决策是布尔（`ApprovalDecisionRequest.approved`）。修改端点 `PATCH /agent/runs/{run_id}/approval` 已实现，白名单含 `damper_type` / `damper_types` / `selected_layout_id` / `response_ids` / `budget.doe_design_count`。所以本阶段**只需做意图理解**，不必再建修改通道。

### 2.2 方案叙述加厚

扩展 `describe_pending_action` 的 facts，让叙述覆盖四块：做什么、参数从哪来、预计代价、结果边界。

facts 新增（全部来自契约和常量表，不引入新数值）：

- `fieldSources`：每个字段标 `USER_SPECIFIED` / `DEFAULT`，让用户看清哪些是自己说的、哪些是系统定的
- `estimatedRealSolves`：真实求解次数（ANALYSIS=1，OPTIMIZATION=`doeDesignCount`+2，COMPARISON=2）
- `budget` 关键项、`selectedLayout` 的 `physicalCountPerTower`

`fieldSources` 在 `build_engineering_contract` 里生成：

```python
'fieldSources': {
    'solver': 'USER_SPECIFIED' if intent_had_solver else 'DEFAULT',
    'loadKind': ...,
    'selectedLayoutId': ...,
    'budget': 'DEFAULT',
},
```

注意 `narrative_safe()` 仍要剥掉 `nodePairs` 和 sha256。叙述末尾固定追加一句"是否允许执行？"——由**模板拼接**，不靠 LLM 生成，确保永远存在。

### 2.3 审批意图理解

新增 `classify_approval_reply(reply, pending_summary)`，输出四分类：

| 意图 | 含义 | 动作 |
|---|---|---|
| `APPROVE` | 明确同意 | `decide_approval(id, True)` |
| `REJECT` | 明确否决且无修改要求 | `decide_approval(id, False)` |
| `MODIFY` | 要改参数 | 抽取白名单字段 → `update_approval` → 重新叙述 |
| `UNCLEAR` | 无法判断 | 重述方案并再问一次，**不执行** |

Pydantic 模型：

```python
class ApprovalReplyIntent(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra='forbid')

    decision: Literal['APPROVE', 'REJECT', 'MODIFY', 'UNCLEAR']
    confidence: float = Field(ge=0.0, le=1.0, default=1.0)
    modifications: ApprovalModifications | None = None
    reason: str = Field(min_length=1, max_length=200)
```

`modifications` 复用已有的 `ApprovalUpdateRequest` 白名单字段，不要新增可改字段。

### 2.4 三条安全约束

这是本阶段风险最高的地方——误判 `APPROVE` 会直接启动数小时的真实 FEM 计算。三条约束缺一不可：

**约束一：置信度阈值。** `decision == 'APPROVE'` 且 `confidence < 0.9` 时降级为 `UNCLEAR`，再问一次。阈值写成模块常量便于调整。

**约束二：APPROVE 必须有肯定词锚点。** 不能只靠模型判断。回复文本必须包含确定性词表中的词，否则降级 `UNCLEAR`：

```python
_APPROVAL_ANCHORS = (
    '同意', '批准', '可以', '执行', '开始', '好的', '没问题', '确认',
    '行', 'ok', 'OK', 'yes', '通过', '就这样', '按这个',
)
```

同时检查否定前缀——"不同意"、"先别"、"暂时不"含锚点但意思相反，需要在锚点前后 6 字窗口内查否定词，命中则强制 `REJECT` 或 `UNCLEAR`。

**约束三：LLM 不可用时不得回落到"批准"。** 分类失败必须落 `UNCLEAR`，回模板"未能确认您的意见，请回复'同意执行'或提出修改要求。"这与其他叙述链路的模板回落不同——那些回落是降级体验，这里回落错了是启动不该启动的计算。

### 2.5 分发接入

`_dispatch_message` 当前优先级（`agent_conversation.py:37-58`）：pending clarification → inquirable run → LLM 路由。

审批待决要插在**最前面**：

```python
if task_type == 'AUTO':
    pending_approval = repository.find_pending_approval_run(session['sessionId'])
    if pending_approval:
        return self._resolve_approval_reply(...)
    pending_clarification = ...
```

理由：有方案待批时，用户的下一句几乎必然是对方案的回应。放在 inquirable 之后会被追问链截走——因为历史终态 run 一直存在。

`find_pending_approval_run` 需要在 `AgentRepository` 新增，查 `status == 'WAITING_APPROVAL'` 且 `pendingApprovalId` 非空。

### 2.6 MODIFY 的重算纪律

`update_approval` 必须**重跑** `build_engineering_contract` + `prepare_approval` 重算 frozenAction 和 SHA256，不能修补原对象。旧 approval 置 `SUPERSEDED`，新建一条。

幂等键 `{runId}:{prefix}:{frozenActionSha256}`（`agent_service.py:1132`）因 SHA256 变化自然区分，现有设计在这里帮了忙。

修改后要重新叙述新方案并再次询问——**不能沿用旧的批准意图**。这点必须有测试锁定。

### 2.7 验收

- 参数化单元：`APPROVE` 词表逐个通过；"不同意"、"先别执行"、"暂时不要"判为 REJECT/UNCLEAR
- 单元：`confidence=0.85` 的 APPROVE 降级为 UNCLEAR，不创建 Job
- 单元：LLM 抛异常 → UNCLEAR，不创建 Job
- 单元：MODIFY 后 `frozenActionSha256` 变化，旧 approval 为 SUPERSEDED，run 仍在 WAITING_APPROVAL
- 集成：UNCLEAR 时 `platform_store.jobs` 长度不变（这是最关键的一条）

---

## 阶段三：结果自然语言化 + 按需绘图

### 3.1 绘图基础设施已就绪

`AgentFigureService`（`agent_figure_service.py`）已具备：matplotlib 无头渲染、契约 SHA256 去重（`_existing_bundle` 命中则复用不重画）、source artifact 三重校验（kind / 登记 sha256 / 内容重算 sha256）、多格式导出（PNG/SVG/PDF/TIFF）、中文字体处理、`Lock` 串行化。

`FigureContract`（`figure_contracts.py`）是 `extra='forbid'` 的严格模型：`y_fields` 最多 6 且必须互异、字段名有正则约束、`source_artifacts` 恰好 1 个、`panels` 最多 6。

当前唯一入口是 `AnalysisAgent._visualize_tool`（`analysis.py:509`），由 Python 代码调用。**缺的只是自然语言入口。** 前端也还没有图表展示位（`platform-ui` 里搜不到 figure 相关组件）。

这比原先估计的工作量小很多——不需要建渲染管线，只需要建"自然语言 → FigureContract"这一层。

### 3.2 绘图意图与契约生成

新增 `plan_figure(question, catalog, prior_plan)`，让 LLM 输出**受限的绘图请求**，而不是直接输出 `FigureContract`：

```python
class FigureRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra='forbid')

    metrics: list[str] = Field(min_length=1, max_length=6)   # RESPONSE_METRIC_SPECS 的 key
    artifact: str = Field(min_length=1, max_length=128)      # catalog.artifacts 的键
    x_field: str = Field(default='time')
    claim: str = Field(min_length=1, max_length=200)         # 这张图要说明什么
    export_formats: list[Literal['PNG', 'SVG']] = Field(default_factory=lambda: ['PNG'])
```

再由**确定性代码**把 `FigureRequest` 翻译成 `FigureContract`：查 `RESPONSE_METRIC_SPECS` 得到语义列名、单位、中文标签，用 `resolve_column` 落到真实 CSV 列，从 store 取 artifact 的 kind 和 sha256 填 `ArtifactRef`。

这样 LLM 只提"画哪些指标"，列名映射、单位、哈希绑定全由代码完成。与"LLM 不拥有工程参数"的取向一致。

`export_formats` 只开放 PNG 和 SVG——PDF/TIFF 是论文出图需求，对话场景不需要，减少无谓渲染。

### 3.3 意图区分

追问现在只有一种出口（数值问答）。需要在 `plan_inquiry` 之前加一次意图判断，或在 `InquiryPlan` 上加字段区分：

| 用户意图 | 出口 |
|---|---|
| 查数值 | 现有 `_execute_inquiry_plan` |
| 要图 | `plan_figure` → 契约 → `figure_service.render` |
| 既要数又要图 | 两者都走，叙述里引用图 |
| 新计算 | 回落主任务路由（`needs_followup` 已实现） |

建议在 `InquiryPlan` 加 `figure: FigureRequest | None`，一次 LLM 调用同时决定查数和画图，避免多一轮往返。

### 3.4 图表进对话

渲染产物是已登记 artifact，有 `artifactId` 和 `downloadUrl`。三处要接：

1. **run 记录**：新增 `figureArtifactIds` 字段，`_decorate_run` 带出
2. **叙述**：facts 里给 `figures: [{claim, artifactId, metrics}]`，prompt 要求"如果 facts 含 figures，在文字中说明图展示了什么，但不要编造图中未包含的数值"
3. **前端**：`ResultCard` / 追问卡新增图片展示位，走 `downloadUrl`

**注意**：`artifactId` 是形如 `art_xxx` 的字符串，不含数字歧义，但 `figures` 里若带了坐标范围之类的数值会进 `allowed` 集合——所以 facts 里的 figures 只放 claim、artifactId、metrics 名，**不要放任何数值**。

### 3.5 悬空指标

`RESPONSE_METRIC_SPECS` 里 `energy_dissipation` 和 `cost` 在真实数据中不存在——objectives 和 `timeseries.csv` 表头都没有，`resolve_column` 永远返回 None。但用户可以通过 `responseIds` 合法请求它们（中文标签"耗能"、"成本"也收）。

绘图会让这个问题显形：用户说"画一下耗能曲线"，`plan_figure` 会输出 `metrics: ['energy_dissipation']`，翻译时 `resolve_column` 返回 None。

要么补数据源，要么从 specs 摘掉并在 `normalize_response_ids` 里拒绝，不能留着静默失败。

另有三处死别名：`cumulative_displacement` 的别名里 `operationCumulativeDisplacement` 是 targetId 不是 CSV 列名，永不命中；`ground_acceleration` 无 spec 引用；`DECOMPOSITION_ALIASES` 在追问路径完全未用。

还有个单位一致性隐患：`RESPONSE_METRIC_SPECS` 声明剪力单位 `N`（走 CSV 原值，正确），而 `sampleResponses` 同时给 `rawValue`(N) 和 `displayValue`(kN)。同一 facts 内同量两种量纲，叙述时选哪个无约束。建议 facts 里只保留 rawValue，单位由 specs 统一提供。

### 3.6 验收

- 单元：`FigureRequest` → `FigureContract` 翻译正确，列名经 `resolve_column`，sha256 来自 store
- 单元：不存在的 metric → 明确错误，不生成空图
- 单元：同一请求两次 → `_existing_bundle` 命中，不重复渲染
- 单元：facts 含 figures 时不引入任何新数字进 allowed
- 集成：追问"画一下位移时程" → 产出 PNG artifact 且叙述提及
- 回归：`test_agent_figure_service.py` 全绿

---

## 实施顺序

```
第一步  1.4 + 1.5 回归修复          （护栏 + 体积上限，阻塞后续）
第二步  1.2 + 1.3 槽位分级与提问      （阶段一主体）
第三步  2.2 方案叙述加厚 + fieldSources
第四步  2.3-2.6 审批意图理解         （风险最高，安全约束不可省）
第五步  3.5 悬空指标清理            （阻塞绘图）
第六步  3.2-3.4 绘图链路
第七步  前端：图表展示位 + 默认值标记
```

1.4 和 1.5 必须先做：阶段二和三都会让 facts 和 catalog 继续变大，护栏和体积问题会被放大。

第四步单独成段，因为它是唯一"误判会直接启动数小时计算"的改动。建议实现后先用一批人工构造的模糊回复跑一遍，确认 UNCLEAR 的召回率，再接到主链路。

---

## 风险与边界

**误批准。** 阶段二最大风险。三条约束（置信度阈值、肯定词锚点、失败落 UNCLEAR）缺一不可。测试里"UNCLEAR 时 jobs 长度不变"是最关键的一条断言。

**护栏被 facts 稀释。** 每次往 facts 加字段都在扩大 `allowed` 集合。定一条纪律：**facts 只放有限标量，绝不放时程数组**；新增字段时评估是否引入无意义数字（计数、索引、枚举序号）。1.4 的长度门槛是第一道防线，不是唯一防线。

**prompt 体积。** catalog、priorResults、figures 都在增长。建议加一个统一的体积检查：序列化后超过阈值（如 24K token）就按优先级裁剪 runs 和 sampleResponses，并在 facts 里标注已裁剪。

**LLM 不可用。** 理解链路对 LLM 硬依赖，无关键词兜底。阶段二让审批也进入这个依赖范围——LLM 挂了用户无法批准已生成的方案。建议保留前端的显式批准按钮作为逃生通道，走原有布尔端点。这条不要省。

**心跳超时。** 阈值 30s（`platform_dispatcher.py:272`）而 `executionTimeoutS` 默认 7200。OpenSeesPy 是 in-proc 的，单个长时间步可能拖过 30s 导致跑了两小时的任务被判终态失败。**先把阈值调到 300s**，比加重试简单且安全——重试会引入输出目录重名和 artifact 重复登记问题。观察后仍有超时再考虑重试。

**范式边界。** 用户问"c=9000 会怎样"是新计算，不是追问。不能让追问链去插值代理模型给答案——那会绕过审批和证据门禁，架空 `evidence_gates`。正确行为是回落主任务路由（`needs_followup` 已实现）。这条边界要守住。

---

## 不做的事

- **不改控制反转。** 不给 LLM 挂工具、不引入 ReAct 循环。`TypedToolRegistry` 保持死代码状态——它是有意义的预留，但启用它等于交出控制权。
- **不做记忆机制。** 会话历史不进 LLM prompt。历史自由文本会开一个 `numbers_are_grounded` 管不到的口子（它只查 facts）。跨轮传递结构化字段就够了。
- **不放松数字护栏。** 让数字合法的唯一方式是把它算出来放进 facts，不是让门禁宽容。
- **不加统计算子。** 均值、RMS、FFT 等暂不做。绘图能覆盖大部分"想看趋势"的需求，成本更低。
- **`AgentStateMachine` 要么删要么接。** 它定义了完整转换表却零调用（实际是裸字典赋值），留着会让读代码的人误以为状态流转有校验。这个落差比没有更糟。

---

## 待核实事项

以下结论来自读码，未跑通验证，动手前建议确认：

1. `resultSummary` 是否含 `responseComparison`——`reflection` 由 `**outcome.extra` 展开，`caseResults` 在 extra 里所以有，`responseComparison` 在 `job['result']` 里，`build_report` 取了但 reflection 未取。建议打印一条真实 comparison run 的 `resultSummary` 键名。
2. `relativeToFirst` 的实际数值形式（推断是小数 0.032 而非百分数）。
3. 前端 `agentApi.ts` 类型是否需随 catalog / run 字段变化同步改。
4. 多轮澄清的 goal 拼接（`agent_conversation.py:477` `f'{prior_goal}\n{content}'`）每轮追加无截断，长会话后 goal 会持续变长，是否有其他地方兜底未核实。
5. 1.4 的实测数字来自构造 facts，非完整链路，量级可信但具体值可能有偏差。
