from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, StringConstraints, model_validator

from .analysis import (
    AnalysisDispatchInput,
    AnalysisDispatchOutput,
    AnalysisReviewInput,
    AnalysisReviewOutput,
)
from .figure_contracts import (
    ArtifactRef,
    FigureContract,
    FigurePanel,
    FigureRenderInput,
    FigureRenderOutput,
    StrictToolModel,
)
from .tools import ToolDescriptor, ToolRisk, TypedAgentTool, TypedToolRegistry


SolverName = Literal['ANSYS', 'OPENSEESPY_INPROC']
TargetSetId = Literal[
    'STBRIDGE_UNIFORM_BASE_ACCELERATION',
    'STBRIDGE_WIND_DECK_NODES',
    'STBRIDGE_TRAFFIC_CENTERLINE',
]
ChannelName = Annotated[str, StringConstraints(pattern=r'^[A-Za-z_][A-Za-z0-9_]{0,127}$')]
ResponseId = Literal[
    'max_girder_end_displacement',
    'max_acceleration',
    'max_tower_base_shear',
    'max_tower_base_moment',
    'max_damper_force',
    'max_damper_stroke',
    'dissipated_energy',
    'cumulative_displacement',
]
CommandRole = Literal[
    'model',
    'damper',
    'gravity',
    'modal',
    'earthquake',
    'wind',
    'traffic',
    'postprocess',
]


class LoadInspectInput(StrictToolModel):
    artifact: ArtifactRef


class LoadInspectOutput(StrictToolModel):
    artifact: ArtifactRef
    valid: bool
    checks: dict[str, bool]


class LoadMapTargetsInput(StrictToolModel):
    artifact: ArtifactRef
    load_kind: Literal['EARTHQUAKE', 'WIND', 'TRAFFIC']
    target_set_id: TargetSetId
    channels: tuple[ChannelName, ...] = Field(min_length=1, max_length=6)

    @model_validator(mode='after')
    def validate_target_set(self) -> 'LoadMapTargetsInput':
        expected = {
            'EARTHQUAKE': 'STBRIDGE_UNIFORM_BASE_ACCELERATION',
            'WIND': 'STBRIDGE_WIND_DECK_NODES',
            'TRAFFIC': 'STBRIDGE_TRAFFIC_CENTERLINE',
        }[self.load_kind]
        if self.target_set_id != expected:
            raise ValueError('load_kind 与 target_set_id 不匹配')
        return self


class LoadMappingContract(StrictToolModel):
    load_kind: Literal['EARTHQUAKE', 'WIND', 'TRAFFIC']
    target_set_id: TargetSetId
    channels: tuple[ChannelName, ...] = Field(min_length=1, max_length=6)

    @model_validator(mode='after')
    def validate_target_set(self) -> 'LoadMappingContract':
        expected = {
            'EARTHQUAKE': 'STBRIDGE_UNIFORM_BASE_ACCELERATION',
            'WIND': 'STBRIDGE_WIND_DECK_NODES',
            'TRAFFIC': 'STBRIDGE_TRAFFIC_CENTERLINE',
        }[self.load_kind]
        if self.target_set_id != expected:
            raise ValueError('load_kind 与 target_set_id 不匹配')
        return self


class LoadMapTargetsOutput(StrictToolModel):
    mapping: LoadMappingContract
    fingerprint: str = Field(pattern=r'^[0-9a-f]{64}$')


class CommandAssembleInput(StrictToolModel):
    solver: SolverName
    template_id: Literal['ANSYS_EARTHQUAKE_BASELINE', 'OPENSEESPY_EARTHQUAKE_BASELINE']
    case_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,128}$')
    load_artifact: ArtifactRef
    load_mapping: LoadMappingContract
    response_ids: tuple[ResponseId, ...] = Field(min_length=1, max_length=9)

    @model_validator(mode='after')
    def validate_template_solver(self) -> 'CommandAssembleInput':
        expected = {
            'ANSYS': 'ANSYS_EARTHQUAKE_BASELINE',
            'OPENSEESPY_INPROC': 'OPENSEESPY_EARTHQUAKE_BASELINE',
        }[self.solver]
        if self.template_id != expected:
            raise ValueError('solver 与 template_id 不匹配')
        return self


class CommandAssembleOutput(StrictToolModel):
    command_stream_artifact: ArtifactRef
    roles: tuple[CommandRole, ...] = Field(min_length=1, max_length=8)
    command_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')


class CommandValidateInput(StrictToolModel):
    solver: SolverName
    command_stream_artifact: ArtifactRef
    expected_roles: tuple[CommandRole, ...] = Field(min_length=1, max_length=8)


class CommandValidateOutput(StrictToolModel):
    passed: bool
    checks: dict[str, bool]


class SolverCapabilitiesInput(StrictToolModel):
    solver: SolverName


class SolverCapabilitiesOutput(StrictToolModel):
    solver: SolverName
    capabilities: dict[str, Any]


class ResultExtractInput(StrictToolModel):
    job_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,128}$')
    raw_artifacts: tuple[ArtifactRef, ...] = Field(min_length=1, max_length=32)


class ResultExtractOutput(StrictToolModel):
    summary_artifact: ArtifactRef
    timeseries_artifact: ArtifactRef
    objectives: dict[str, float]


class AnalysisCapabilityTools:
    """ANALYSIS 内部原子能力目录；高层 Agent 不直接获得任意命令执行权。"""

    REQUIRED_HANDLER_NAMES = (
        'load.inspect',
        'load.map_targets',
        'command_stream.assemble',
        'command_stream.validate',
        'solver.capabilities',
        'solver.execute',
        'result.extract',
        'evidence.verify',
        'figure.render',
    )

    def __init__(self, handlers: Mapping[str, Callable[[BaseModel], BaseModel | dict[str, Any]]]) -> None:
        missing = sorted(set(self.REQUIRED_HANDLER_NAMES) - set(handlers))
        if missing:
            raise ValueError(f'缺少 ANALYSIS 原子工具 handler: {missing}')
        self.registry = TypedToolRegistry()
        self._register(
            handlers,
            name='load.inspect',
            description='当工作流准备使用登记荷载制品、需要先核对类型和哈希时使用；不修改制品。',
            input_model=LoadInspectInput,
            output_model=LoadInspectOutput,
            risk=ToolRisk.READ_ONLY,
            artifact_kinds=('STANDARD_LOAD',),
        )
        self._register(
            handlers,
            name='load.map_targets',
            description='当荷载制品已通过检查、需要映射到受控模型目标集时使用；不执行求解。',
            input_model=LoadMapTargetsInput,
            output_model=LoadMapTargetsOutput,
            risk=ToolRisk.READ_ONLY,
            artifact_kinds=('STANDARD_LOAD',),
        )
        self._register(
            handlers,
            name='command_stream.assemble',
            description='当模型目标和登记模板均已确定、需要组装受控求解命令流时使用；不直接执行命令。',
            input_model=CommandAssembleInput,
            output_model=CommandAssembleOutput,
            risk=ToolRisk.ARTIFACT_WRITE,
            artifact_kinds=('COMMAND_STREAM',),
        )
        self._register(
            handlers,
            name='command_stream.validate',
            description='当命令流已组装、需要在执行前核对哈希、角色和求解器合同时使用；不修改命令。',
            input_model=CommandValidateInput,
            output_model=CommandValidateOutput,
            risk=ToolRisk.READ_ONLY,
            artifact_kinds=('COMMAND_STREAM',),
        )
        self._register(
            handlers,
            name='solver.capabilities',
            description='当内部工作流需要读取已登记求解器能力和环境状态时使用；不执行求解。',
            input_model=SolverCapabilitiesInput,
            output_model=SolverCapabilitiesOutput,
            risk=ToolRisk.READ_ONLY,
        )
        self._register(
            handlers,
            name='solver.execute',
            description='当冻结动作已获审批且幂等键已生成、需要执行唯一真实分析 Job 时使用；不得用于试算。',
            input_model=AnalysisDispatchInput,
            output_model=AnalysisDispatchOutput,
            risk=ToolRisk.SOLVER_EXECUTION,
            requires_approval=True,
            timeout_seconds=7200,
        )
        self._register(
            handlers,
            name='result.extract',
            description='当真实 Job 已成功并登记原始输出、需要提取标准结果制品时使用；不推断缺失结果。',
            input_model=ResultExtractInput,
            output_model=ResultExtractOutput,
            risk=ToolRisk.ARTIFACT_WRITE,
            artifact_kinds=('RESULT_SUMMARY', 'RESULT_TIMESERIES'),
        )
        self._register(
            handlers,
            name='evidence.verify',
            description='当结果制品已登记、需要按确定性真实 FEM 门槛核验证据时使用；不生成新结论。',
            input_model=AnalysisReviewInput,
            output_model=AnalysisReviewOutput,
            risk=ToolRisk.READ_ONLY,
        )
        self._register(
            handlers,
            name='figure.render',
            description='当结论已有登记证据、需要按 figure contract 生成可审计图件包时使用；不补造数据。',
            input_model=FigureRenderInput,
            output_model=FigureRenderOutput,
            risk=ToolRisk.ARTIFACT_WRITE,
            artifact_kinds=('FIGURE_BUNDLE', 'SOURCE_DATA'),
        )

    def _register(
        self,
        handlers: Mapping[str, Callable[[BaseModel], BaseModel | dict[str, Any]]],
        *,
        name: str,
        description: str,
        input_model: type[BaseModel],
        output_model: type[BaseModel],
        risk: ToolRisk,
        requires_approval: bool = False,
        timeout_seconds: int | None = None,
        artifact_kinds: tuple[str, ...] = (),
    ) -> None:
        self.registry.register(TypedAgentTool(
            name=name,
            description=description,
            input_model=input_model,
            output_model=output_model,
            risk=risk,
            requires_approval=requires_approval,
            timeout_seconds=timeout_seconds,
            artifact_kinds=artifact_kinds,
            handler=handlers[name],
        ))

    def list_tools(self) -> list[ToolDescriptor]:
        return self.registry.list_tools()

    def execute(
        self,
        name: str,
        payload: BaseModel | dict[str, Any],
        *,
        approved: bool = False,
        idempotency_key: str | None = None,
    ) -> BaseModel:
        return self.registry.execute(
            name,
            payload,
            approved=approved,
            idempotency_key=idempotency_key,
        )
