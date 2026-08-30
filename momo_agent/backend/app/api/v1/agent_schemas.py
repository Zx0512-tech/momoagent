from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

from app.core.engineering_limits import DOE_INITIAL_MAX, DOE_INITIAL_MIN


class AgentModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra='forbid',
        strict=True,
    )


class AgentSessionCreateRequest(AgentModel):
    title: str = Field(default='新建工程智能体会话', min_length=1, max_length=120)


class AgentMessageCreateRequest(AgentModel):
    content: str = Field(min_length=1, max_length=4000)
    file_id: Optional[str] = None
    task_type: Literal[
        'AUTO',
        'ANALYSIS',
        'DAMPER_OPTIMIZATION',
        'DAMPER_COMPARISON',
        'DAMPER_PARAMETER_SWEEP',
        'LOAD_IMPORT',
        'FULL_OPTIMIZATION',
    ] = 'AUTO'


class LoadTimeMapping(AgentModel):
    column: Optional[str] = None
    step_s: Optional[float] = Field(default=None, gt=0)
    unit: Literal['s', 'ms'] = 's'


class LoadChannelMapping(AgentModel):
    value_column: str = Field(min_length=1)
    application_type: Literal['UNIFORM_EXCITATION', 'NODAL_FORCE', 'NODAL_FORCE_MATRIX']
    target_type: Optional[Literal['NODE', 'NODE_GROUP']] = None
    target_id: Optional[str] = None
    component: Literal['UX', 'UY', 'UZ']
    quantity: Literal['FORCE', 'ACCELERATION']
    source_unit: Literal['N', 'kN', 'g', 'm/s2', 'cm/s2', 'mm/s2']
    scale: float = 1.0

    @model_validator(mode='after')
    def validate_application(self) -> 'LoadChannelMapping':
        allowed = {'FORCE': {'N', 'kN'}, 'ACCELERATION': {'g', 'm/s2', 'cm/s2', 'mm/s2'}}
        if self.source_unit not in allowed[self.quantity]:
            raise ValueError(f'{self.source_unit} 与 {self.quantity} 不兼容')
        if self.application_type == 'UNIFORM_EXCITATION' and self.quantity != 'ACCELERATION':
            raise ValueError('UNIFORM_EXCITATION 只接受加速度通道')
        if self.application_type in {'NODAL_FORCE', 'NODAL_FORCE_MATRIX'} and (
            not self.target_type or not self.target_id
        ):
            raise ValueError(f'{self.application_type} 必须提供 targetType 和 targetId')
        # 车流矩阵是逐节点力时程的稠密矩阵，不存在加速度口径：只有地震走
        # UNIFORM_EXCITATION 加速度，风与车流的输入文件都是力。
        if self.application_type == 'NODAL_FORCE_MATRIX' and self.quantity != 'FORCE':
            raise ValueError('NODAL_FORCE_MATRIX 只接受力通道')
        return self

    def to_mapping(self) -> dict[str, Any]:
        return {
            'valueColumn': self.value_column,
            'applicationType': self.application_type,
            'targetType': self.target_type,
            'targetId': self.target_id,
            'component': self.component,
            'quantity': self.quantity,
            'sourceUnit': self.source_unit,
            'scale': self.scale,
        }


class LoadMappingRequest(AgentModel):
    run_id: str = Field(min_length=1)
    load_kind: Optional[Literal['EARTHQUAKE', 'WIND', 'TRAFFIC', 'GENERIC_NODAL']] = None
    time: Optional[LoadTimeMapping] = None
    channels: Optional[list[LoadChannelMapping]] = Field(default=None, min_length=1, max_length=64)
    time_column: Optional[str] = None
    value_column: Optional[str] = None
    time_step_s: Optional[float] = Field(default=None, gt=0)
    time_unit: Literal['s', 'ms'] = 's'
    target_type: Optional[Literal['NODE', 'NODE_GROUP']] = 'NODE'
    target_id: Optional[str] = None
    component: Optional[Literal['UX', 'UY', 'UZ']] = None
    quantity: Optional[Literal['FORCE', 'ACCELERATION']] = None
    source_unit: Optional[Literal['N', 'kN', 'g', 'm/s2', 'cm/s2', 'mm/s2']] = None
    solver: Literal['ANSYS', 'OPENSEESPY_INPROC'] = 'ANSYS'

    @model_validator(mode='after')
    def validate_quantity_unit(self) -> 'LoadMappingRequest':
        if self.channels:
            if self.load_kind is None:
                raise ValueError('多通道映射必须提供 loadKind')
            if self.time is None:
                raise ValueError('多通道映射必须提供 time')
            if self.time.column is None and self.time.step_s is None:
                raise ValueError('无时间列时必须提供 time.stepS')
            return self
        if not all((self.value_column, self.target_id, self.component, self.quantity, self.source_unit)):
            raise ValueError('单通道映射缺少必要字段')
        allowed = {
            'FORCE': {'N', 'kN'},
            'ACCELERATION': {'g', 'm/s2', 'cm/s2', 'mm/s2'},
        }
        if self.source_unit not in allowed[self.quantity]:
            raise ValueError(f'{self.source_unit} 与 {self.quantity} 不兼容')
        return self

    def to_mapping(self) -> dict[str, Any]:
        if self.channels and self.time:
            return {
                'runId': self.run_id,
                'version': 2,
                'loadKind': self.load_kind,
                'time': {
                    'column': self.time.column,
                    'stepS': self.time.step_s,
                    'unit': self.time.unit,
                },
                'channels': [channel.to_mapping() for channel in self.channels],
                'solver': self.solver,
            }
        return {
            'runId': self.run_id,
            'timeColumn': self.time_column,
            'valueColumn': self.value_column,
            'timeStepS': self.time_step_s,
            'timeUnit': self.time_unit,
            'targetType': self.target_type,
            'targetId': self.target_id,
            'component': self.component,
            'quantity': self.quantity,
            'sourceUnit': self.source_unit,
            'solver': self.solver,
        }


class ApprovalDecisionRequest(AgentModel):
    approved: bool


class ApprovalBudgetUpdate(AgentModel):
    doe_design_count: Optional[int] = Field(
        default=None,
        ge=DOE_INITIAL_MIN,
        le=DOE_INITIAL_MAX,
    )


class ApprovalUpdateRequest(AgentModel):
    """审批前允许用户修改的白名单工程字段。"""

    damper_type: Optional[Literal['VISCOUS', 'FRICTION', 'EDDY_CURRENT']] = None
    damper_kind: Optional[Literal['VISCOUS', 'FRICTION', 'EDDY_CURRENT']] = None
    damper_types: Optional[list[Literal['VISCOUS', 'FRICTION', 'EDDY_CURRENT']]] = Field(
        default=None,
        min_length=2,
        max_length=2,
    )
    selected_layout_id: Optional[str] = Field(default=None, min_length=1, max_length=64)
    response_ids: Optional[list[str]] = Field(default=None, min_length=1, max_length=16)
    budget: Optional[ApprovalBudgetUpdate] = None
