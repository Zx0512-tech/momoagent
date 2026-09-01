from __future__ import annotations

from typing import Any

from pydantic import ConfigDict, Field

from .figure_contracts import StrictToolModel
from .tools import ToolRisk, TypedAgentTool, TypedToolRegistry


RESPONSE_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    'girder_displacement': ('displacement', 'girder_displacement', 'absolute_displacement'),
    'tower_base_shear': ('tower_base_shear',),
    'tower_base_moment': ('tower_base_moment',),
    'damper_force': ('damper_force',),
    'damper_stroke': ('damper_stroke',),
    'acceleration': ('acceleration',),
    'cumulative_displacement': ('cumulative_displacement',),
}
# 工程响应 ID、中文语义、单位和 CSV 语义列的唯一事实表。
# responseIds 使用 max_* 命名，而 CSV 表头使用短列名，因此必须显式桥接。
RESPONSE_METRIC_SPECS: dict[str, dict[str, Any]] = {
    'max_girder_end_displacement': {
        'label': '梁端位移',
        'unit': 'm',
        'semantic': 'girder_displacement',
    },
    'max_acceleration': {
        'label': '加速度响应',
        'unit': 'm/s²',
        'semantic': 'acceleration',
    },
    'max_tower_base_shear': {
        'label': '塔底剪力',
        'unit': 'N',
        'semantic': 'tower_base_shear',
    },
    'max_tower_base_moment': {
        'label': '塔底弯矩',
        'unit': 'N·m',
        'semantic': 'tower_base_moment',
    },
    'max_damper_force': {
        'label': '阻尼器力',
        'unit': 'N',
        'semantic': 'damper_force',
    },
    'max_damper_stroke': {
        'label': '阻尼器行程',
        'unit': 'm',
        'semantic': 'damper_stroke',
    },
    'cumulative_displacement': {
        'label': '累计位移',
        'unit': 'm',
        'semantic': 'cumulative_displacement',
    },
}


def resolve_column(
    columns: list[str],
    semantic: str,
    table: dict[str, tuple[str, ...]],
) -> str | None:
    for candidate in table.get(semantic, ()):
        if candidate in columns:
            return candidate
    return None


class ResultColumnsInput(StrictToolModel):
    model_config = ConfigDict(extra='forbid', populate_by_name=True)

    artifact_id: str = Field(alias='artifactId', pattern=r'^[A-Za-z0-9_-]{1,128}$')


class ResultColumnsOutput(StrictToolModel):
    columns: list[str]


class ResultPeakInput(StrictToolModel):
    model_config = ConfigDict(extra='forbid', populate_by_name=True)

    artifact_id: str = Field(alias='artifactId', pattern=r'^[A-Za-z0-9_-]{1,128}$')
    column: str = Field(min_length=1, max_length=128)
    time_column: str = Field(default='time', alias='timeColumn', min_length=1, max_length=128)


class ResultPeakOutput(StrictToolModel):
    model_config = ConfigDict(extra='forbid', populate_by_name=True)

    column: str
    peak_absolute: float = Field(alias='peakAbsolute')
    peak_signed: float = Field(alias='peakSigned')
    peak_time: float | None = Field(default=None, alias='peakTime')
    sample_count: int = Field(alias='sampleCount')


class ResultTopsisInput(StrictToolModel):
    model_config = ConfigDict(extra='forbid', populate_by_name=True)

    artifact_id: str = Field(alias='artifactId', pattern=r'^[A-Za-z0-9_-]{1,128}$')
    limit: int = Field(default=10, ge=1, le=50)


class ResultTopsisOutput(StrictToolModel):
    model_config = ConfigDict(extra='forbid', populate_by_name=True)

    objective_names: list[str] = Field(alias='objectiveNames')
    rows: list[dict[str, Any]]
    available_count: int = Field(alias='availableCount')
    weights: list[float] = Field(default_factory=list)


class ResultSweepCasesInput(StrictToolModel):
    model_config = ConfigDict(extra='forbid', populate_by_name=True)

    artifact_id: str = Field(alias='artifactId', pattern=r'^[A-Za-z0-9_-]{1,128}$')
    metric: str | None = Field(default=None, min_length=1, max_length=128)
    order: str = Field(default='asc', pattern=r'^(asc|desc)$')
    limit: int = Field(default=64, ge=1, le=64)


class ResultSweepCasesOutput(StrictToolModel):
    model_config = ConfigDict(extra='forbid', populate_by_name=True)

    metric: str
    order: str
    rows: list[dict[str, Any]]
    case_count: int = Field(alias='caseCount')
    available_metrics: list[str] = Field(alias='availableMetrics')
    sensitivity: list[dict[str, Any]]
    interpretation_limit: str = Field(alias='interpretationLimit')


class ResultAtTimeInput(StrictToolModel):
    model_config = ConfigDict(extra='forbid', populate_by_name=True)

    artifact_id: str = Field(alias='artifactId', pattern=r'^[A-Za-z0-9_-]{1,128}$')
    columns: list[str] = Field(min_length=1, max_length=12)
    target_time: float = Field(alias='targetTime')
    time_column: str = Field(default='time', alias='timeColumn', min_length=1, max_length=128)


class ResultAtTimeOutput(StrictToolModel):
    model_config = ConfigDict(extra='forbid', populate_by_name=True)

    requested_time: float = Field(alias='requestedTime')
    matched_time: float = Field(alias='matchedTime')
    values: dict[str, float | None]


class ResultCorrelateInput(StrictToolModel):
    model_config = ConfigDict(extra='forbid', populate_by_name=True)

    artifact_id: str = Field(alias='artifactId', pattern=r'^[A-Za-z0-9_-]{1,128}$')
    column_a: str = Field(alias='columnA', min_length=1, max_length=128)
    column_b: str = Field(alias='columnB', min_length=1, max_length=128)


class ResultCorrelateOutput(StrictToolModel):
    model_config = ConfigDict(extra='forbid', populate_by_name=True)

    column_a: str = Field(alias='columnA')
    column_b: str = Field(alias='columnB')
    pearson: float
    sample_count: int = Field(alias='sampleCount')
    interpretation_limit: str = Field(alias='interpretationLimit')


class ResultDerivedInput(StrictToolModel):
    model_config = ConfigDict(extra='forbid', populate_by_name=True)

    artifact_id: str = Field(alias='artifactId', pattern=r'^[A-Za-z0-9_-]{1,128}$')
    columns: list[str] = Field(min_length=2, max_length=2)


class ResultDerivedOutput(StrictToolModel):
    model_config = ConfigDict(extra='forbid', populate_by_name=True)

    column_a: str = Field(alias='columnA')
    column_b: str = Field(alias='columnB')
    left_peak_absolute: float = Field(alias='leftPeakAbsolute')
    right_peak_absolute: float = Field(alias='rightPeakAbsolute')
    difference: float
    relative_change: float | None = Field(alias='relativeChange')
    relative_change_percent: float | None = Field(alias='relativeChangePercent')
    ratio: float | None
    sample_count: int = Field(alias='sampleCount')
    interpretation_limit: str = Field(alias='interpretationLimit')


class InquiryTools:
    """追问阶段的只读结果查询工具；不含任何求解或写入能力。"""

    def __init__(self, *, service: Any) -> None:
        artifact_kinds = ('CSV_TIMESERIES', 'CSV_TABLE')
        self.registry = TypedToolRegistry()
        self.registry.register(TypedAgentTool(
            name='result.columns',
            description='当需要先确认已登记结果 CSV 可查询的列名时使用；不读取未登记文件。',
            input_model=ResultColumnsInput,
            output_model=ResultColumnsOutput,
            risk=ToolRisk.READ_ONLY,
            requires_approval=False,
            artifact_kinds=artifact_kinds,
            handler=lambda payload: {'columns': service.columns(payload.artifact_id)},
        ))
        self.registry.register(TypedAgentTool(
            name='result.topsis',
            description='当用户询问已完成优化的 TOPSIS 排名或前 N 个候选时使用；只读取登记的优化摘要，不重新求解。',
            input_model=ResultTopsisInput,
            output_model=ResultTopsisOutput,
            risk=ToolRisk.READ_ONLY,
            requires_approval=False,
            artifact_kinds=('JSON_SUMMARY', 'OPTIMIZATION_REPORT'),
            handler=lambda payload: service.topsis(payload.artifact_id, limit=payload.limit),
        ))
        self.registry.register(TypedAgentTool(
            name='result.sweep_cases',
            description=(
                '当用户询问批量参数计算或阻尼器对比的跨算例结果（按指标排序、参数敏感性）时使用；'
                '只读取登记的批量/对比汇总 JSON，不重新求解。'
            ),
            input_model=ResultSweepCasesInput,
            output_model=ResultSweepCasesOutput,
            risk=ToolRisk.READ_ONLY,
            requires_approval=False,
            artifact_kinds=('JSON_SUMMARY', 'OPTIMIZATION_REPORT'),
            handler=lambda payload: service.sweep_cases(
                payload.artifact_id,
                metric=payload.metric,
                order=payload.order,
                limit=payload.limit,
            ),
        ))
        self.registry.register(TypedAgentTool(
            name='result.peak',
            description='当用户询问已登记 CSV 某一列的绝对峰值及发生时刻时使用；不比较多列。',
            input_model=ResultPeakInput,
            output_model=ResultPeakOutput,
            risk=ToolRisk.READ_ONLY,
            requires_approval=False,
            artifact_kinds=artifact_kinds,
            handler=lambda payload: service.peak(
                payload.artifact_id,
                column=payload.column,
                time_column=payload.time_column,
            ),
        ))
        self.registry.register(TypedAgentTool(
            name='result.at_time',
            description='当用户询问已登记 CSV 在指定时刻的一列或多列取值时使用；不做插值。',
            input_model=ResultAtTimeInput,
            output_model=ResultAtTimeOutput,
            risk=ToolRisk.READ_ONLY,
            requires_approval=False,
            artifact_kinds=artifact_kinds,
            handler=lambda payload: service.at_time(
                payload.artifact_id,
                columns=list(payload.columns),
                target_time=payload.target_time,
                time_column=payload.time_column,
            ),
        ))
        self.registry.register(TypedAgentTool(
            name='result.correlate',
            description='当用户询问已登记 CSV 两列之间的 Pearson 相关性时使用；不解释因果关系。',
            input_model=ResultCorrelateInput,
            output_model=ResultCorrelateOutput,
            risk=ToolRisk.READ_ONLY,
            requires_approval=False,
            artifact_kinds=artifact_kinds,
            handler=lambda payload: service.correlate(
                payload.artifact_id,
                column_a=payload.column_a,
                column_b=payload.column_b,
            ),
        ))
        self.registry.register(TypedAgentTool(
            name='result.compare',
            description=(
                '当用户需要比较同一已登记 CSV 中两列绝对峰值时使用；'
                '一次返回差值、比值和相对变化，columns[0] 为基准列，columns[1] 为对比列。'
            ),
            input_model=ResultDerivedInput,
            output_model=ResultDerivedOutput,
            risk=ToolRisk.READ_ONLY,
            requires_approval=False,
            artifact_kinds=artifact_kinds,
            handler=lambda payload: service.compare(
                payload.artifact_id,
                column_a=payload.columns[0],
                column_b=payload.columns[1],
            ),
        ))

    def call(self, name: str, payload: dict[str, Any]) -> Any:
        return self.registry.execute(name, payload)
