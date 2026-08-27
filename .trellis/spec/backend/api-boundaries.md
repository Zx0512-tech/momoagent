# 平台 API 边界契约

## 场景：工程请求、载荷制品和运行前门禁

### 1. Scope / Trigger

当新增或修改平台命令型 API、通用 `/jobs` 入口、载荷上传/映射、Live 能力门禁或公共 preflight 时，必须遵守本契约。目标是保证客户端提交的参数、服务端实际执行参数和审计记录一致，并让未实现或不可运行的能力失败关闭。

### 2. Signatures

```python
class PlatformRequestModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra='forbid',
        strict=True,
    )

JOB_REQUEST_MODELS: dict[JobType, type[PlatformRequestModel]]

inspect_and_store_load_artifact(
    *, content: bytes, file_name: str, repository: Repository
) -> LoadArtifactResult

preflight_config(config_path: Path) -> PreflightResult
```

HTTP 边界：

- `POST /api/v1/jobs`：按 `jobType` 从 `JOB_REQUEST_MODELS` 选择与专用接口相同的请求模型。
- `POST /api/v1/artifacts/load?fileName=...`：请求体为原始文件字节，最大 20 MB。
- `GET /api/v1/preflight`：调用真实配置预检，不允许硬编码成功。

### 3. Contracts

- 命令型请求接受声明的 camelCase alias 或 Python snake_case 字段；未知字段、字符串代替数字/布尔值及嵌套拼写错误均拒绝，不得裁剪或强制转换。
- 请求 DTO 与响应 DTO 分离。专用创建接口使用 `exclude_unset=True` 保存用户实际提交的字段，不能用响应默认值回填命令。
- 通用 `/jobs` 必须复用专用接口的类型化请求模型，不能提供宽泛 `dict` 绕过验证。
- 载荷上传返回 `artifactId`、`sha256`、`fileName`、列信息、单位检查和编码/分隔符探测信息。执行请求必须引用真实 `inputArtifactId`。
- 编码探测顺序固定为 UTF-8 BOM、UTF-8、GB18030；标准化输出使用 UTF-8 与逗号分隔。探测和标准化前后信息必须进入响应/审计记录。
- `m/s²` 可规范化为 `m/s2`，但响应必须明确报告；其他列名、单位、目标或制品 ID 不得猜测或修改。
- Live 模式的独立 `SOLVER_BATCH`、`SURROGATE_TRAINING`、`ACTIVE_LEARNING` 在创建 Job/Artifact 前返回 501；受控智能体执行链不受此独立入口门禁影响。
- preflight 仅在配置文件、JSON/schema 和求解器可用性全部通过时返回 `PASS`。

### 4. Validation & Error Matrix

| 条件 | HTTP / 错误码 | 副作用 |
| --- | --- | --- |
| 未知字段、错误类型、嵌套拼写错误 | 422 `VALIDATION_ERROR` | 不创建 Job |
| 通用 `/jobs` 的 payload 不符合对应专用模型 | 422 `VALIDATION_ERROR` | 不创建 Job |
| 上传超过 20 MB、列缺失、单位非法或内容不匹配 | 422 | 不创建标准化载荷 |
| 制品不属于当前运行 | 422 | 不创建 Job |
| Live 调用未实现的独立能力 | 501 `CAPABILITY_NOT_IMPLEMENTED` | 不创建 Job/Artifact |
| preflight 配置缺失、JSON/schema 无效或求解器不可用 | 200，`status=FAIL` 且包含结构化原因 | 不暴露堆栈 |

### 5. Good / Base / Bad Cases

- Good：客户端上传 CSV，显式选择时间列、数值列、单位和目标，再用返回的 `artifactId` 创建同一运行内的载荷任务。
- Base：客户端使用 snake_case 提交已声明字段；alias 配置接受它，并保留字段值与类型。
- Bad：前端把本地文件名填入 `inputArtifactId`，或服务端根据文件名猜测列、单位、求解器。

### 6. Tests Required

- camelCase 与 snake_case 合法请求均通过；未知字段、字符串数值和嵌套错拼均断言 422。
- 每个通用 `/jobs` 类型至少验证一次与专用路由相同的失败样例，并断言没有 Job 记录。
- CSV/TXT/XLSX 上传断言真实 SHA、列信息和编码报告；错误单位、错误列及跨运行制品断言 422。
- 三个未实现的 Live 独立类型断言 501，且 Job/Artifact 数量不变；Mock 响应/页面断言模拟标识。
- preflight 覆盖有效配置、缺失文件、无效 JSON/schema 和求解器缺失。

### 7. Wrong vs Correct

```python
# Wrong：宽泛请求模型会裁剪或转换调用方输入
class JobRequest(BaseModel):
    payload: dict[str, Any]

# Correct：通用入口复用专用请求模型并严格校验
request_model = JOB_REQUEST_MODELS[job_type]
validated = request_model.model_validate(payload)
stored_payload = validated.model_dump(by_alias=True, exclude_unset=True)
```

## 项目内置地震荷载契约

### 1. Scope / Trigger

当受控智能体运行未上传载荷、明确使用 `EARTHQUAKE`，且任务属于 `ANALYSIS`、`DAMPER_COMPARISON`、`DAMPER_OPTIMIZATION` 或 `FULL_OPTIMIZATION` 时，使用项目内置地震记录作为默认真实输入。用户上传的载荷始终优先，其他荷载类型不得回退到该记录。

### 2. Signatures

```python
LoadArtifactService.provision_bundled_earthquake(run_id: str) -> dict[str, Any]
```

固定源文件为 `analysis_data/earthquake_inputs/earthquake_acceleration_record.txt`。源数据为 4001 个加速度样本，时间步长 `0.01 s`，方向 `UX`，单位 `m/s2`，施加方式为 `UNIFORM_EXCITATION`。

### 3. Contracts

- 内置记录必须经过与上传载荷相同的 `LoadImportService` 标准化，不允许求解器直接读取未登记的工作区文件。
- 每个 Agent run 单独登记标准 CSV 和转换报告 Artifact，并冻结 `loadDatasetArtifactId`、`loadDatasetSha256` 和完整 `loadMapping`。
- `loadMapping.source` 和输入来源必须记录为 `BUNDLED_PROJECT_DATA`，不得表示为用户上传或用户明确指定。
- 标准化报告必须保留源相对路径、源 SHA-256、编码、样本数、单位和转换信息；求解 Job 必须消费冻结的 Artifact ID 和 SHA。
- 用户上传 Artifact 的优先级高于内置记录；存在上传时不得额外创建或替换内置载荷 Artifact。

### 4. Validation & Error Matrix

| 条件 | HTTP / 错误码 | 副作用 |
| --- | --- | --- |
| 内置源文件缺失或不可读取 | 422 `BUNDLED_EARTHQUAKE_NOT_AVAILABLE` | 不创建审批或 Job |
| 内置内容不能通过载荷标准化 | 422，由 `LoadImportService` 返回结构化错误 | 不创建标准 Artifact |
| 非地震任务或仍有待澄清字段 | 不自动配置内置记录 | 保持原澄清/映射流程 |
| 已提供用户上传载荷 | 使用上传 Artifact | 不创建内置载荷 Artifact |

### 5. Good / Base / Bad Cases

- Good：用户要求“用 OpenSeesPy 计算内置地震记录”，审批显示项目内置数据，Job 请求引用当前 run 的标准 CSV Artifact。
- Base：用户未上传文件但明确地震分析，系统使用内置记录并披露来源、方向、单位和时长。
- Bad：只把模板中的本地文件路径交给求解器，审批中的 `loadDatasetArtifactId` 为空，导致实际输入无法按 Artifact 和 SHA 审计。

### 6. Tests Required

- 无上传的分析和完整优化必须冻结内置 Artifact，并断言 4001 行、`0..40 s`、`m/s2`、`UX` 与源 SHA。
- 审批事实必须区分 `BUNDLED_PROJECT_DATA` 与 `UPLOADED_FILE`，且 `usesUploadedLoad=false`。
- 审批后创建的 Job 必须携带同一 `loadDatasetArtifactId` 和 `loadMapping`。
- 已上传载荷的既有测试必须继续断言来源为 `FILE_DERIVED`，证明上传优先级未改变。

### 7. Wrong vs Correct

```python
# Wrong：模板知道文件路径，但审批、Job 和证据链没有输入制品
frozen_action = {'loadDatasetArtifactId': None}

# Correct：先标准化并登记，再冻结同一运行拥有的制品
bundled = load_artifact_service.provision_bundled_earthquake(run['runId'])
frozen_action = {
    'loadDatasetArtifactId': bundled['standardArtifactId'],
    'loadDatasetSha256': bundled['standardSha256'],
    'loadMapping': bundled['mapping'],
}
```
