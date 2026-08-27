# MOMO 项目剩余缺陷审计报告

## 结论摘要

审计确认 **0 个 P0、3 个 P1、4 个 P2**。另记录 5 类 P3 质量与契约漂移问题。

当前归档的 OpenSeesPy 比赛证据及其 414 文件 SHA256 清单可通过现有校验，Harness 面向模型公开的 7 个工具也已满足名称唯一、严格 Schema、描述包含使用时机、`result.compare`/`comparison.compare` 分离和幂等元数据不污染模型描述等要求。本报告没有证据表明已归档的 17 个真实算例被篡改。

高风险问题集中在两条边界：

1. 独立平台页面在生产 `live` 模式下仍运行占位实现，并把伪造的代理指标、FEM 队列或未执行的求解任务呈现为成功工程结果。
2. 审批更新、阶段轨迹和公共 API 对参数的约束不一致，导致资源上限绕过、审计轨迹违反工具契约或输入拼写错误被静默改变语义。

## 已确认缺陷

### P1-1 生产 `live` 页面把占位计算呈现为成功工程结果

**触发条件**

使用生产构建（`platform-ui/.env.production:2` 设置 `VITE_API_MODE=live`），从独立“批量求解”或“代理模型/主动学习”页面提交普通平台 Job。

**证据链**

- `platform-ui/README.md:48` 把 `live` 描述为真实 REST 接口交互模式；根 `README.md:3-9` 也把真实求解和优化证据作为提交包能力。
- `platform-ui/src/pages/solver/SolverBatchPage.tsx:54-68` 提交普通 `SOLVER_BATCH` Job。
- `momo_agent/backend/app/services/platform_store.py:1211-1212` 会把所有 `SOLVER_BATCH` 排队，但普通页面请求不具备真实工作流请求所需的执行上下文。
- `momo_agent/backend/app/services/platform_store.py:349-361` 只要占位制品生成没有抛异常，就把 Job 标记为 `SUCCEEDED` 和 100%。
- `momo_agent/backend/app/services/platform_store.py:1145-1146` 随后又在结果中明确写入 `realSolverExecution: PENDING_REPLACEMENT`。
- `platform-ui/src/pages/solver/SolverBatchPage.tsx:253-269` 只显示成功状态、耗时和制品数，没有披露真实求解尚未执行。
- `momo_agent/backend/app/services/platform_store.py:778-799` 将字面量 `surrogate-model-placeholder` 登记成可下载的 `trained_surrogate_model.pkl`。
- `momo_agent/backend/app/services/platform_store.py:1161-1171` 没有返回 R²、RMSE、MAE 或交叉验证数据。
- `platform-ui/src/pages/surrogate/SurrogatePage.tsx:172-191` 在 Job 成功后自行生成 R²/RMSE/MAE/CV 数值，源码注释明确称其为 mock 结果。
- `platform-ui/src/pages/surrogate/SurrogatePage.tsx:216-230` 同样自行生成三条主动学习队列记录。
- `platform-ui/src/pages/surrogate/SurrogatePage.tsx:504-553` 将这些数值展示为“代理拟合校验矩阵”并提供模型下载；`557-583` 将 mock 队列标为“真实 FEM 样本复核队列”。

**实际行为**

未执行真实求解或训练的请求得到 `SUCCEEDED`；前端再把固定公式生成的指标、固定队列和占位字节显示为真实结果。

**预期行为**

生产 `live` 路径只能在真实执行及证据登记完成后返回成功；未实现的能力必须拒绝请求或明确返回 `NOT_IMPLEMENTED`/`PLACEHOLDER`，前端不得生成或以工程指标名义展示 mock 数据。

**影响**

正常用户可稳定获得错误工程结论，并下载无效模型文件。这不会修改已归档的比赛证据，但会破坏生产页面新运行结果的可信性。

**反证检查**

完整智能体优化请求具有额外的工作流执行上下文，并走独立的真实执行与证据门禁；本发现仅针对独立平台页面及其普通 Job 路径。因此没有把该问题扩大解释为“全部 Harness 真实优化都是假的”。

**最小修复建议**

先在后端对普通 `SOLVER_BATCH`、`SURROGATE_TRAINING` 和 `ACTIVE_LEARNING` 占位路径 fail-closed；删除前端生成指标和队列的代码。若保留演示模式，必须由显式 `mock` 配置控制，并在状态、制品和 UI 上持续标注。

### P1-2 “输入文件”载荷模式没有上传或绑定真实文件

**触发条件**

在载荷页面为地震、风或交通选择“输入文件”，然后提交生成任务。

**证据链**

- `platform-ui/src/pages/loads/LoadsPage.tsx:632-650` 的所谓文件输入只是两个预置文件名的 `<select>`，不是文件上传控件，也没有产生 artifact ID。
- 地震请求在 `platform-ui/src/pages/loads/LoadsPage.tsx:168-189` 只把文件名用于场景元数据；没有发送文件内容、路径或已登记制品。
- 风请求在 `platform-ui/src/pages/loads/LoadsPage.tsx:192-209` 只发送 `fileInput` 元数据。
- 交通请求在 `platform-ui/src/pages/loads/LoadsPage.tsx:212-228` 把文件名直接填入 `existingVehicleDataArtifactId`。
- `momo_agent/backend/app/services/platform_store.py:930-943` 按制品 ID 解析交通输入；隔离复现对 `project_load_timeseries.csv` 返回 HTTP 404：`制品 project_load_timeseries.csv 不存在`。
- `momo_agent/backend/app/services/platform_store.py:910-919` 对风/地震统一登记通用 CSV 制品，没有读取所选文件内容。

**实际行为**

交通文件模式稳定失败；风和地震可成功生成与所选文件内容无关的制品。

**预期行为**

页面应上传文件并获取不可混淆的 `artifactId`，请求必须引用该制品；后端应读取、校验并在结果中记录输入制品哈希。文件不可解析时必须失败。

**影响**

工程载荷可能根本不是用户选择的时程数据，属于正常用户路径上的错误工程输入。

**反证检查**

后端确实存在受 20 MB 限制和文件名校验保护的智能体上传接口（`momo_agent/backend/app/api/v1/agent_router.py:73-94`、`momo_agent/backend/app/services/agent_service.py:160-163`），但该独立载荷页面没有调用或绑定它，因此现有上传能力不能消除本缺陷。

**最小修复建议**

复用现有上传/制品登记能力，让三类载荷请求统一传 `inputArtifactId`，并在服务端校验 kind、SHA256、列结构和单位；移除纯文件名模式。

### P1-3 审批更新后的 DOE 数量绕过冻结工作流上限

**触发条件**

创建 `DAMPER_OPTIMIZATION`，在审批前把 DOE 数量从 15 更新为 16–1000，然后批准执行。

**证据链**

- `momo_agent/backend/app/agents/workflows.py:170-175` 冻结的优化工作流上限为 `doeDesignCount=15`。
- `momo_agent/backend/app/api/v1/agent_schemas.py:147-148` 允许审批更新值达到 1000。
- `momo_agent/backend/app/services/agent_service.py:789-800` 把新值直接冻结到更新后的工程合同。
- 现有测试 `momo_agent/backend/tests/test_agent_approval_update.py:102-116` 明确证明 30 会被接受并写入合同。
- `momo_agent/backend/app/services/agent_harness.py:1607-1622` 调用 Guard 时只为分析任务传 `realSolveCount=1`，优化任务的 `usage` 是空字典。
- 隔离复现结果：空用量时 `empty_usage_allowed=True`；对完全相同的调用显式传 `usage={'doeDesignCount': 30}` 时，Guard 返回 `WORKFLOW_LIMIT_EXCEEDED`，并报告最大值 15。

**实际行为**

工作流 Guard 具备正确的上限逻辑，但授权方没有把已冻结的 DOE 数量传给它，导致 16–1000 的审批值被放行。

**预期行为**

审批可修改范围不得超过工作流冻结上限；授权时必须从最终冻结合同派生实际用量并交给 Guard 校验。

**影响**

一次合法审批可触发远超约定的真实求解数量，造成明显的计算时间、许可证和资源风险。

**反证检查**

这不是审批绕过：用户仍需批准更新后的内容；问题是工作流资源安全边界没有生效。Guard 在收到真实用量时能够拒绝，说明根因位于调用链参数缺失，而不是 Guard 算法。

**最小修复建议**

在更新审批时将 `doeDesignCount` 限制到工作流 snapshot；在 `_authorize_approved_execution` 再从最终冻结合同派生 `doeDesignCount`、候选数、CV 和主动学习次数作为纵深校验。

### P2-1 Python Job 阶段轨迹违反已登记工具 Schema，且缺少参数保真字段

**触发条件**

完整优化 Job 成功后，Harness 为 BASELINE 到 FEM_VALIDATION 的 Python 阶段自动登记工具调用轨迹。

**证据链**

- `momo_agent/backend/app/services/agent_harness.py:63-66` 的 `HarnessRunInput` 只允许 `runId`，并设置 `extra='forbid'`。
- `momo_agent/backend/app/services/agent_harness.py:1784-1799` 取阶段 `allowedTools[0]`，统一以 `{runId, jobId}` 授权。
- `momo_agent/backend/app/services/agent_harness.py:1800-1814` 保存相同参数，但不经过 `_HARNESS_TOOL_SPECS` 的输入模型校验，也没有保存 `argumentsSha256`、`effectiveArguments`、`effectiveArgumentsSha256` 和幂等来源。
- 机器校验对 7 个阶段全部得到 `jobId:extra_forbidden`：`optimization.run_baseline`、`run_doe`、`fit_surrogate`、`active_learning`、`rank_candidates`、`recommend`、`validate_candidates`。
- 同一段代码把所有阶段风险记为 `ARTIFACT_WRITE`，即使 DOE 与最终 FEM 复核实际属于求解执行。

**实际行为**

`agent_tool_calls` 中的阶段参数无法通过该工具自己公开的 Schema；轨迹也无法证明原始参数与实际参数一致。

**预期行为**

所有工具轨迹必须由同一工具 spec 校验，保留原始/实际参数及各自哈希，并准确记录风险；若内部阶段需要 `jobId`，应使用与执行语义一致的独立输入模型。

**影响**

不会直接阻断 Job，但使审计记录与单一事实源矛盾，降低失败恢复、取证和参数保真保证的可信度。

**反证检查**

面向模型公开的 7 个工具本身全部严格，`result.compare` 和 `comparison.compare` 也已分离；本问题仅剩在内部 Python Job 阶段轨迹，不能回归为先前已修复的同名工具问题。

**最小修复建议**

为内部 Job 阶段明确选用 `HarnessJobInput`，或只登记 `runId`；统一通过一个轨迹构造函数执行 Schema 校验、参数哈希和风险登记。

### P2-2 公共预检接口对任意非空路径固定返回 PASS

**触发条件**

调用 `/api/v1/preflights` 并传入任意不存在的非空配置路径。

**证据链**

- `momo_agent/backend/app/services/platform_store.py:552-560` 不访问文件系统，固定返回 `status='PASS'` 和 `exists=True`，甚至仅凭路径中是否含 `ansys` 推断求解器。
- 隔离复现对 `Z:\definitely_missing\not-a-config.json` 返回 `PASS`，且 path check 为 `exists=True`。
- 现有测试只覆盖有效字符串和空请求，没有不存在路径的反例。

**实际行为**

不存在的配置、不可执行的求解器和错误环境均可得到成功预检结果。

**预期行为**

预检必须检查路径存在性、可读性、配置 Schema、求解器/运行时可用性，并对每个检查返回真实结果。

**影响**

平台 API 消费者会基于虚假的 readiness 继续提交任务，错误被推迟且诊断信息丢失。

**反证检查**

Harness 的真实审批路径使用另一套 readiness/预检链，本接口不会直接绕过 Harness 门禁；因此定为 P2 而不是 P1。

**最小修复建议**

删除固定实现并复用平台 readiness 服务；在能力未实现前返回明确的 501，而不是 PASS。

### P2-3 公共 API 会静默忽略或容纳拼写错误参数

**触发条件**

客户端把关键字段拼错，例如把 `taskType` 写成 `taskTyp`，或把 `trafficScale` 写成 `trafficScle`。

**证据链**

- `momo_agent/backend/app/api/v1/agent_schemas.py:7-18` 没有设置 `extra='forbid'`，并在验证前静默把所有 camelCase key 改为 snake_case。
- 隔离复现：`{'content':'run','taskTyp':'ANALYSIS'}` 验证成功，错误字段消失，最终 `task_type='AUTO'`。
- `momo_agent/backend/app/api/v1/schemas.py:49-50` 为全部平台模型设置 `extra='allow'`。
- 隔离复现：`{'trafficScle':2}` 验证成功并保留为额外字段，但真正的 `traffic_scale` 仍为默认值 `1.0`。

**实际行为**

请求没有被拒绝，模型意图被默认值替代；服务层通常只读取正确字段，因此额外字段没有作用。

**预期行为**

工程与智能体控制请求应默认拒绝未知字段。若必须规范化命名，规则需要在接口描述中公开，并在响应/轨迹中披露原始与规范化参数。

**影响**

拼写错误可静默改变任务路由或工程尺度，客户端无法区分“请求已按原意执行”和“错误字段被忽略”。

**反证检查**

Harness 工具输入模型全部 `extra='forbid'`，所以问题不影响模型工具调用入口；它存在于公共 REST 请求 Schema。

**最小修复建议**

为命令/创建/更新类请求使用 `extra='forbid'`；仅在明确需要向前兼容的响应 DTO 上允许 extra。为兼容别名使用显式 `alias`，不要对任意 key 做隐式重写。

### P2-4 提交校验器在清单缺失时跳过全部完整性检查

**触发条件**

`SUBMISSION_MANIFEST.json` 丢失、未打包或被删除后运行 `python verify_submission.py`。

**证据链**

- `verify_submission.py:28-39` 的必需文件列表没有包含清单。
- `verify_submission.py:41-48` 只有在清单存在时才读取并校验 SHA256。
- `verify_submission.py:68-75` 在清单不存在时仍打印包、运行时和 DOE 通过信息，只是不打印 manifest 行。

**实际行为**

完整性根文件缺失会令校验器 fail-open，所有逐文件哈希校验被跳过。

**预期行为**

清单必须是必需文件；缺失、重复路径、文件数不符、清单外文件或哈希不符均应使验证失败。

**影响**

交付流程可能在没有任何全包完整性保证的情况下报告主要检查通过。

**反证检查**

当前清单实际存在，414 个登记文件的 SHA256 检查本次全部通过，所以这是交付门禁缺陷，不代表当前包已被篡改。

**最小修复建议**

先 `require(MANIFEST)`，再校验 `fileCount`、路径唯一性、排序/规范路径，以及是否存在未登记的应提交文件。

## P3 改进项

1. **后端默认 pytest 命令不稳定。** `momo_agent/backend/pytest.ini:1-3` 没有 `testpaths=tests`，也未忽略 `.tmp`。本次 `python -m pytest -q` 在收集 11 个历史 `.tmp/pytest-*` 目录时因权限错误中止；显式执行 `python -m pytest tests -q` 才得到 313 个通过。
2. **Python 静态质量门禁不可直接作为 CI gate。** `ruff check app tests` 报 9 项（主要是未使用 import/local 和一项 E402）；`mypy app` 因 `app/api/schemas.py` 与 `app/api/v1/schemas.py` 被识别为重复顶层模块而立即退出，相关目录缺少包标记或 mypy namespace 配置。
3. **warning 噪声掩盖回归。** 后端测试有 49 个 warning，其中大量是 Pydantic `UnsupportedFieldAttributeWarning`；直接验证别名和 OpenAPI 均正确，所以没有升级为功能缺陷。前端 lint 通过但有 3 个 warning。
4. **前后端制品来源联合类型漂移。** 后端 `momo_agent/backend/app/api/v1/schemas.py:63` 支持 `REAL_SOLVER_RESULT`，前端 `platform-ui/src/api/types.ts:641` 和 `src/api/client.ts:260` 不支持该值，且制品浏览器没有对应筛选项。当前渲染未按该值分支，故暂定 P3。
5. **可观察性仍有空 catch。** 独立求解/代理页面多处吞掉请求或轮询异常；这会降低上述占位路径的可诊断性，但没有单独证明新的结果错误，合并为维护性问题。

## 验证基线

| 检查 | 结果 | 说明 |
|---|---|---|
| 后端 `python -m pytest -q` | 失败 | 收集 `.tmp/pytest-*` 时 11 个 `PermissionError`；属于测试发现配置问题 |
| 后端 `python -m pytest tests -q` | 通过 | 313 passed，49 warnings，26.83s |
| 前端 `npm.cmd test` | 通过 | 8 个文件、34 个测试通过 |
| 前端 `npm.cmd run lint` | 通过并有 warning | 3 个 warning，无 error |
| 前端 `npm.cmd run build` | 通过 | 生产构建成功 |
| 前端 `npm.cmd run check:bundle` | 通过 | 最大 chunk 333,530 bytes，小于 500,000 bytes |
| `python -m compileall app tests` | 通过 | 后端源码和测试可编译 |
| `ruff check app tests` | 失败 | 9 项静态问题 |
| `mypy app` | 失败 | `schemas` 重复模块名，未进入有效类型检查 |
| `python verify_submission.py` | 通过 | 清单 414 文件、USER300、DOE 15 和最终复核均通过 |
| `npm.cmd audit --offline --json` | 通过 | 本地缓存返回 0 个漏洞、149 个依赖；不能替代在线审计 |

## 已排除或已验证正常

- Harness 模型公开目录共有 7 个唯一工具；所有 `inputSchema.additionalProperties=false`，且工作流 `allowedTools` 没有缺失 spec。
- `result.compare` 只接受 `artifactId + columns`；工程阶段的 `comparison.compare` 接受 `runId + jobId`，两个契约已经分离。
- `idempotencyKeySource` 仍作为目录元数据保留，但不再追加到模型描述；现有测试覆盖该行为。
- 正式 Harness 求解授权轨迹会同时记录 `arguments`、`effectiveArguments` 及各自 SHA256；P2-1 只针对后续自动阶段轨迹。
- 服务默认绑定 `127.0.0.1`（`start.ps1:63-67`），CORS 仅接受本机 localhost/127.0.0.1 端口（`momo_agent/backend/app/main.py:43-48`）；在当前本地单用户部署边界内，没有把“缺少登录”误报为远程权限漏洞。
- 文件上传同时检查声明长度和流式累计长度，限制为 20 MB，并拒绝目录型文件名。
- 抽样检查的子进程调用均使用参数列表，没有发现 `shell=True` 或字符串 shell 拼接执行。
- 现有 313 个后端测试覆盖审批、幂等、工作流门禁、状态恢复、取消、制品和真实证据校验；没有发现 P0 级任意代码执行、凭据泄漏、不可恢复删除或已归档工程结论静默篡改。

## 未验证的外部风险与范围限制

- `README.md:88` 记录安装时曾出现 3 个高危依赖在线摘要。本次只获准/能够运行离线审计；离线缓存为 0 不能推翻该历史摘要，需在可联网且明确授权的环境运行在线 `npm audit` 后再分类 CVE。
- 未调用真实外部 LLM、商业 ANSYS 或许可证服务，也未重新运行 60–90 分钟完整优化；这些依赖环境的问题不能据此判定为通过或失败。
- 本次抽样追踪输入、执行、登记和最终证据门禁，但没有重新评审桥梁模型、阻尼参数或优化算法的结构工程学术正确性。

## 建议修复顺序

1. **批次 A（结果可信性）**：P1-1、P1-2。先让所有生产占位能力 fail-closed，再接入真实制品和输入链。
2. **批次 B（资源与工具契约）**：P1-3、P2-1。统一从冻结合同派生用量，并用单一 spec 构造、校验和登记轨迹。
3. **批次 C（接口与交付门禁）**：P2-2、P2-3、P2-4。预检改为真实检查，请求 Schema fail-closed，清单设为强制完整性根。
4. **批次 D（质量门禁）**：修复 pytest 发现范围、mypy 配置、Ruff/lint/warning，并补齐前后端联合类型。

每个修复批次应单独创建任务并先写失败测试；不要在同一提交中混入相邻重构。
