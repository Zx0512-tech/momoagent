from __future__ import annotations

from copy import deepcopy
import math
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.engineering_limits import (
    DOE_ACTIVE_LEARNING_MAX_ADDITIONAL,
    DOE_ACTIVE_LEARNING_MAX_ITERATIONS,
    DOE_FIXED_REAL_SOLVE_OVERHEAD,
    DOE_INITIAL_DEFAULT,
)


ENGINEERING_TASK_TYPES = (
    'ANALYSIS',
    'DAMPER_OPTIMIZATION',
    'DAMPER_COMPARISON',
    'DAMPER_PARAMETER_SWEEP',
)
ENGINEERING_SOLVERS = ('ANSYS', 'OPENSEESPY_INPROC')
# 批量参数计算按荷载类型声明放行的求解器。两种工况都放行 ANSYS 与 OpenSeesPy：
# 二者都有已登记的批量模板（见 damper_parameter_sweep.PARAMETER_SWEEP_WIND_WORKFLOW_PATHS
# 与 ANALYSIS 的 ANALYSIS_WIND_WORKFLOW_PATHS），能力目录 solverScenarios 同口径。
PARAMETER_SWEEP_LOAD_KINDS: dict[str, tuple[str, ...]] = {
    'EARTHQUAKE': ('ANSYS', 'OPENSEESPY_INPROC'),
    'WIND': ('ANSYS', 'OPENSEESPY_INPROC'),
    # 车流批量复用车流 ANALYSIS 基线模板：两个求解器都有已登记模板，
    # 163 列稠密矩阵由 ANSYS 的逐节点 TABLE 和 traffic.pyfrag 分别消费。
    'TRAFFIC': ('ANSYS', 'OPENSEESPY_INPROC'),
}
# 方案比选按荷载类型声明放行的求解器。三种工况都放行 ANSYS 与 OpenSeesPy：
# 比选的 damperCalibrations 门要求逐型 USER300 标定，两个求解器现在各有自己的
# 已登记标定目录（见 agent_evidence._DAMPER_CALIBRATION_CATALOGS），证据不混用。
# 集中声明使新增工况只需改这一处，而不是散在 prepare_approval 的分支里。
COMPARISON_LOAD_KINDS: dict[str, tuple[str, ...]] = {
    'EARTHQUAKE': ('ANSYS', 'OPENSEESPY_INPROC'),
    'WIND': ('ANSYS', 'OPENSEESPY_INPROC'),
    # 车流比选与风同口径：等峰值反算 C 只依赖各求解器自己的 USER300 标定证据。
    'TRAFFIC': ('ANSYS', 'OPENSEESPY_INPROC'),
}
STANDARD_BUDGET = {
    'doeDesignCount': DOE_INITIAL_DEFAULT,
    'surrogateCv': 10,
    'maxActiveLearningIterations': DOE_ACTIVE_LEARNING_MAX_ITERATIONS,
    'candidateCount': 728,
    'maxReviewIterations': 1,
    'maxFemRelativeError': 0.05,
}
COMPARISON_PROFILE = {
    'comparisonBasis': 'EQUAL_PEAK_FORCE',
    'forceCapN': 4_000_000.0,
    'forceCapScope': 'PER_PHYSICAL_DAMPER',
    'designVelocityMps': 0.2,
    'viscousAlpha': 0.3,
    'viscousVfloorMps': 1.0e-6,
    'parameterSource': 'VERIFIED_TEMPLATE',
}

DAMPER_TYPES: dict[str, dict[str, Any]] = {
    'VISCOUS': {
        'parameterNames': ['c', 'alpha', 'vfloor'],
        'solverModules': {'ANSYS': 'damper_user300_viscous', 'OPENSEESPY_INPROC': 'damper_viscous'},
        'productionReady': {'ANSYS': True, 'OPENSEESPY_INPROC': True},
        'optimizationReady': {'ANSYS': True, 'OPENSEESPY_INPROC': True},
    },
    'FRICTION': {
        'parameterNames': ['fc', 'vs'],
        'solverModules': {'ANSYS': 'damper_friction', 'OPENSEESPY_INPROC': 'damper_friction'},
        'productionReady': {'ANSYS': True, 'OPENSEESPY_INPROC': True},
        'optimizationReady': {'ANSYS': False, 'OPENSEESPY_INPROC': False},
    },
    'EDDY_CURRENT': {
        'parameterNames': ['fmax', 'vcr'],
        'solverModules': {'ANSYS': 'damper_eddy_current', 'OPENSEESPY_INPROC': 'damper_eddy_current'},
        'productionReady': {'ANSYS': True, 'OPENSEESPY_INPROC': True},
        'optimizationReady': {'ANSYS': False, 'OPENSEESPY_INPROC': False},
    },
}

STBRIDGE_LAYOUTS: dict[str, dict[str, Any]] = {
    'ONE_PER_TOWER': {
        'nodePairs': [[36, 518], [107, 521]],
        'direction': 'X',
        'physicalCountPerTower': 1,
    },
    'TWO_PER_TOWER': {
        'nodePairs': [[36, 517], [36, 518], [107, 520], [107, 521]],
        'direction': 'X',
        'physicalCountPerTower': 2,
    },
}

SLOT_SPECS: dict[str, dict[str, Any]] = {
    'solver': {
        'level': 'REQUIRED',
        'label': '求解器',
        'options': ENGINEERING_SOLVERS,
        'default': 'OPENSEESPY_INPROC',
    },
    'loadKind': {
        'level': 'SUGGESTED',
        'label': '荷载类型',
        'default': 'EARTHQUAKE',
    },
    'selectedLayoutId': {
        'level': 'SUGGESTED',
        'label': '阻尼器布置',
        'default': 'TWO_PER_TOWER',
    },
    'responseIds': {
        'level': 'SUGGESTED',
        'label': '关注响应量',
        'default': None,
    },
    'budget': {
        'level': 'DEFAULTED',
        'label': '计算预算',
    },
}

RESPONSE_CATALOG = {
    'max_girder_end_displacement',
    'max_acceleration',
    'max_tower_base_shear',
    'max_tower_base_moment',
    'max_damper_force',
    'max_damper_stroke',
    'cumulative_displacement',
}
ResponseId = Literal[
    'max_girder_end_displacement',
    'max_acceleration',
    'max_tower_base_shear',
    'max_tower_base_moment',
    'max_damper_force',
    'max_damper_stroke',
    'cumulative_displacement',
]


class EngineeringIntent(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra='forbid', strict=True)

    task_type: Literal[
        'ANALYSIS', 'DAMPER_OPTIMIZATION', 'DAMPER_COMPARISON',
        'DAMPER_PARAMETER_SWEEP', 'CLARIFICATION', 'UNSUPPORTED'
    ] = Field(alias='taskType')
    solver: Literal['ANSYS', 'OPENSEESPY_INPROC'] | None = Field(
        default=None,
        description='用户所说 OpenSees 对应 OPENSEESPY_INPROC；ANSYS 对应 ANSYS。',
    )
    damper_type: Literal['VISCOUS', 'FRICTION', 'EDDY_CURRENT'] | None = Field(default=None, alias='damperType')
    damper_types: list[Literal['VISCOUS', 'FRICTION', 'EDDY_CURRENT']] = Field(
        default_factory=list, alias='damperTypes', max_length=3
    )
    load_kind: Literal['EARTHQUAKE', 'WIND', 'TRAFFIC', 'GENERIC_NODAL'] | None = Field(
        default=None,
        alias='loadKind',
        description='按用户描述的地震、风、交通或通用节点荷载选择，不根据文件名猜测。',
    )
    selected_layout_id: Literal['ONE_PER_TOWER', 'TWO_PER_TOWER'] | None = Field(
        default=None,
        alias='selectedLayoutId',
    )
    response_ids: list[ResponseId] = Field(
        default_factory=list,
        alias='responseIds',
        description=(
            '按用户关注的工程响应选择：梁端位移=max_girder_end_displacement；'
            '塔底内力同时包含 max_tower_base_shear 和 max_tower_base_moment；'
            '加速度=max_acceleration；阻尼器力=max_damper_force；'
            '阻尼器行程=max_damper_stroke；累积位移=cumulative_displacement。'
        ),
    )
    cases: list[DamperParameterSweepCase] = Field(default_factory=list, max_length=64)
    max_concurrent_cases: int = Field(default=4, ge=1, le=8, alias='maxConcurrentCases')
    model_artifact_id: str | None = Field(
        default=None,
        alias='modelArtifactId',
        pattern=r'^[A-Za-z0-9_-]{1,128}$',
        description=(
            '仅当用户明确要求使用自己上传的有限元模型并给出制品 ID（如 art_xxx）时填写；'
            '未提及自定义模型保持 null，将使用默认 STbridge 模型。'
        ),
    )
    response_nodes: list[int] = Field(
        default_factory=list,
        alias='responseNodes',
        max_length=16,
        description='用户点名要输出响应时程的模型节点编号（如“输出节点 36 和 107 的位移”）；未点名保持空。',
    )
    response_element_ids: list[int] = Field(
        default_factory=list,
        alias='responseElementIds',
        max_length=16,
        description='用户点名要输出内力时程的单元编号；未点名保持空。',
    )
    response_direction: Literal['X', 'Y', 'Z'] | None = Field(
        default=None,
        alias='responseDirection',
        description='节点响应输出方向；用户未指定保持 null（默认 X 向）。',
    )
    budget_profile: Literal['STANDARD'] = Field(default='STANDARD', alias='budgetProfile')
    requires_real_fem: bool = Field(default=True, alias='requiresRealFem')
    missing_fields: list[str] = Field(default_factory=list, alias='missingFields')
    summary: str = Field(min_length=1, max_length=500)

    @model_validator(mode='after')
    def validate_comparison_types(self) -> 'EngineeringIntent':
        if len(set(self.damper_types)) != len(self.damper_types):
            raise ValueError('damperTypes must be distinct')
        if any(node <= 0 for node in self.response_nodes):
            raise ValueError('responseNodes must be positive integers')
        if len(set(self.response_nodes)) != len(self.response_nodes):
            raise ValueError('responseNodes must be distinct')
        if any(element <= 0 for element in self.response_element_ids):
            raise ValueError('responseElementIds must be positive integers')
        if len(set(self.response_element_ids)) != len(self.response_element_ids):
            raise ValueError('responseElementIds must be distinct')
        return self


class DamperParameterSweepCase(BaseModel):
    """一个不参与优化的显式阻尼器参数算例。"""

    model_config = ConfigDict(populate_by_name=True, extra='forbid', strict=True)

    case_id: str = Field(alias='caseId', min_length=1, max_length=64)
    damper_type: Literal['VISCOUS', 'FRICTION', 'EDDY_CURRENT'] = Field(alias='damperType')
    parameters: dict[str, float] = Field(min_length=1, max_length=8)
    solver_module: str | None = Field(default=None, alias='solverModule')

    @model_validator(mode='after')
    def validate_parameters(self) -> 'DamperParameterSweepCase':
        spec = DAMPER_TYPES[self.damper_type]
        expected = set(spec['parameterNames'])
        actual = set(self.parameters)
        if actual != expected:
            raise ValueError(
                f'{self.damper_type} parameters must exactly match {sorted(expected)}'
            )
        if any(not isinstance(value, (int, float)) or not math.isfinite(float(value)) for value in self.parameters.values()):
            raise ValueError('damper parameters must be finite numbers')
        if any(float(value) <= 0 for value in self.parameters.values()):
            raise ValueError('damper parameters must be positive')
        return self


class DamperParameterSweepIntent(BaseModel):
    """批量正向计算意图；不包含 DOE、代理或优化字段。"""

    model_config = ConfigDict(populate_by_name=True, extra='forbid', strict=True)

    task_type: Literal['DAMPER_PARAMETER_SWEEP'] = Field(alias='taskType')
    solver: Literal['ANSYS', 'OPENSEESPY_INPROC']
    scenario: Literal['EARTHQUAKE', 'WIND'] = 'EARTHQUAKE'
    selected_layout_id: Literal['ONE_PER_TOWER', 'TWO_PER_TOWER'] = Field(
        default='TWO_PER_TOWER', alias='selectedLayoutId',
    )
    cases: list[DamperParameterSweepCase] = Field(min_length=1, max_length=64)
    response_ids: list[ResponseId] = Field(alias='responseIds', min_length=1, max_length=7)
    max_concurrent_cases: int = Field(default=4, ge=1, le=8, alias='maxConcurrentCases')
    requires_real_fem: Literal[True] = Field(alias='requiresRealFem')
    summary: str = Field(min_length=1, max_length=500)

    @model_validator(mode='after')
    def validate_cases(self) -> 'DamperParameterSweepIntent':
        case_ids = [case.case_id for case in self.cases]
        if len(set(case_ids)) != len(case_ids):
            raise ValueError('caseId values must be unique')
        if self.solver not in PARAMETER_SWEEP_LOAD_KINDS[self.scenario]:
            raise ValueError(f'{self.scenario} parameter sweep does not support solver {self.solver}')
        return self

RESPONSE_DIRECTION_COMPONENTS = {'X': 0, 'Y': 1, 'Z': 2}


def build_engineering_contract(
    *,
    task_type: str,
    solver: str,
    damper_type: str | None,
    response_ids: list[str],
    selected_layout_id: str | None = 'TWO_PER_TOWER',
    load_kind: str = 'EARTHQUAKE',
    field_sources: dict[str, str] | None = None,
    load_artifact_id: str | None = None,
    load_sha256: str | None = None,
    model_artifact_id: str | None = None,
    response_nodes: list[int] | None = None,
    response_element_ids: list[int] | None = None,
    response_direction: str | None = None,
) -> dict[str, Any]:
    if task_type not in ENGINEERING_TASK_TYPES:
        raise ValueError(f'Unsupported engineering task: {task_type}')
    if solver not in ENGINEERING_SOLVERS:
        raise ValueError(f'Unsupported engineering solver: {solver}')
    if selected_layout_id is not None and selected_layout_id not in STBRIDGE_LAYOUTS:
        raise ValueError(f'Unsupported engineering layout: {selected_layout_id}')
    if load_kind not in {'EARTHQUAKE', 'WIND', 'TRAFFIC', 'GENERIC_NODAL'}:
        raise ValueError(f'Unsupported load kind: {load_kind}')
    unknown_responses = set(response_ids) - RESPONSE_CATALOG
    if unknown_responses:
        raise ValueError(f'Unsupported responses: {sorted(unknown_responses)}')
    response_nodes = [int(node) for node in (response_nodes or [])]
    response_element_ids = [int(element) for element in (response_element_ids or [])]
    if model_artifact_id or response_nodes or response_element_ids:
        if task_type != 'ANALYSIS':
            raise ValueError('Custom model and node/element outputs are only supported for ANALYSIS')
    if model_artifact_id and solver != 'ANSYS':
        raise ValueError('User-uploaded FEM models are only supported with the ANSYS solver')
    if model_artifact_id and not response_nodes:
        raise ValueError('Custom model analysis requires explicit responseNodes')
    if response_direction is not None and response_direction not in RESPONSE_DIRECTION_COMPONENTS:
        raise ValueError(f'Unsupported response direction: {response_direction}')
    damper = None
    if damper_type is not None:
        try:
            registered = DAMPER_TYPES[damper_type]
        except KeyError as exc:
            raise ValueError(f'Unsupported damper type: {damper_type}') from exc
        damper = {
            'type': damper_type,
            'parameterNames': list(registered['parameterNames']),
            'solverModule': registered['solverModules'][solver],
            'productionReady': registered['productionReady'][solver],
            'optimizationReady': registered['optimizationReady'][solver],
        }
    selected_layout = deepcopy(STBRIDGE_LAYOUTS[selected_layout_id]) if selected_layout_id else None
    if task_type == 'ANALYSIS':
        budget = {'realSolveCount': 1, 'executionTimeoutS': 7200}
        execution_estimate = {'mode': 'SINGLE_ANALYSIS', 'realSolveCount': 1}
    else:
        budget = dict(STANDARD_BUDGET)
        minimum_real_solves = int(STANDARD_BUDGET['doeDesignCount']) + DOE_FIXED_REAL_SOLVE_OVERHEAD
        maximum_real_solves = minimum_real_solves + DOE_ACTIVE_LEARNING_MAX_ADDITIONAL
        execution_estimate = {
            'mode': 'PARAMETER_OPTIMIZATION',
            'realSolveCount': maximum_real_solves,
            'estimatedRealSolvesMin': minimum_real_solves,
            'estimatedRealSolvesMax': maximum_real_solves,
            'doeDesignCount': STANDARD_BUDGET['doeDesignCount'],
            'candidateCount': STANDARD_BUDGET['candidateCount'],
        }
    field_source_defaults = {
        'solver': 'USER_SPECIFIED',
        'loadKind': 'DEFAULT',
        'responseIds': 'USER_SPECIFIED' if response_ids else 'DEFAULT',
        'budget': 'DEFAULT',
    }
    if selected_layout_id is not None:
        field_source_defaults['selectedLayoutId'] = 'DEFAULT'
    return {
        'version': 1,
        'model': f'USER_FEM:{model_artifact_id}' if model_artifact_id else 'STbridge',
        'taskType': task_type,
        'solver': solver,
        'loadKind': load_kind,
        'damper': damper,
        'layoutCandidates': deepcopy(STBRIDGE_LAYOUTS),
        'selectedLayoutId': selected_layout_id,
        'selectedLayout': selected_layout,
        'responseIds': list(response_ids),
        'modelArtifactId': model_artifact_id,
        'responseNodes': response_nodes,
        'responseElementIds': response_element_ids,
        'responseComponent': RESPONSE_DIRECTION_COMPONENTS.get(response_direction or 'X', 0),
        'budget': budget,
        'executionEstimate': execution_estimate,
        'loadArtifactId': load_artifact_id,
        'loadSha256': load_sha256,
        'fieldSources': dict(field_sources or field_source_defaults),
    }


def design_equal_peak_force_cases(damper_types: list[str]) -> list[dict[str, Any]]:
    if not damper_types or len(set(damper_types)) != len(damper_types):
        raise ValueError('Damper types must be non-empty and distinct')
    force_cap = float(COMPARISON_PROFILE['forceCapN'])
    design_velocity = float(COMPARISON_PROFILE['designVelocityMps'])
    cases: list[dict[str, Any]] = []
    for damper_type in damper_types:
        if damper_type == 'VISCOUS':
            alpha = float(COMPARISON_PROFILE['viscousAlpha'])
            parameters = {
                'c': force_cap / design_velocity**alpha,
                'alpha': alpha,
                'vfloor': float(COMPARISON_PROFILE['viscousVfloorMps']),
            }
        elif damper_type == 'EDDY_CURRENT':
            parameters = {'fmax': force_cap, 'vcr': design_velocity}
        elif damper_type == 'FRICTION':
            parameters = {'fc': force_cap, 'vs': design_velocity}
        else:
            raise ValueError(f'Unsupported damper type: {damper_type}')
        cases.append({
            'caseId': damper_type.lower(),
            'damperType': damper_type,
            'parameters': parameters,
            'theoreticalPeakForceN': force_cap,
            'designVelocityMps': design_velocity,
            'parameterSource': COMPARISON_PROFILE['parameterSource'],
        })
    return cases


def build_damper_comparison_contract(
    *,
    solver: str,
    damper_types: list[str],
    response_ids: list[str],
    selected_layout_id: str = 'TWO_PER_TOWER',
    load_kind: str = 'EARTHQUAKE',
    field_sources: dict[str, str] | None = None,
) -> dict[str, Any]:
    if solver not in ENGINEERING_SOLVERS:
        raise ValueError(f'Unsupported engineering solver: {solver}')
    if load_kind not in COMPARISON_LOAD_KINDS:
        raise ValueError(f'Unsupported damper comparison load kind: {load_kind}')
    if selected_layout_id not in STBRIDGE_LAYOUTS:
        raise ValueError(f'Unsupported engineering layout: {selected_layout_id}')
    if len(damper_types) not in {2, 3} or len(set(damper_types)) != len(damper_types):
        raise ValueError('Damper comparison requires two or three distinct types')
    unknown_responses = set(response_ids) - RESPONSE_CATALOG
    if unknown_responses:
        raise ValueError(f'Unsupported responses: {sorted(unknown_responses)}')
    cases = design_equal_peak_force_cases(damper_types)
    for case in cases:
        registered = DAMPER_TYPES[case['damperType']]
        case['solverModule'] = registered['solverModules'][solver]
        case['productionReady'] = registered['productionReady'][solver]
    return {
        'version': 1,
        'model': 'STbridge',
        'taskType': 'DAMPER_COMPARISON',
        'solver': solver,
        'loadKind': load_kind,
        'scenario': load_kind,
        'comparisonBasis': COMPARISON_PROFILE['comparisonBasis'],
        'forceCapN': COMPARISON_PROFILE['forceCapN'],
        'forceCapScope': COMPARISON_PROFILE['forceCapScope'],
        'designVelocityMps': COMPARISON_PROFILE['designVelocityMps'],
        'cases': cases,
        'selectedLayoutId': selected_layout_id,
        'selectedLayout': deepcopy(STBRIDGE_LAYOUTS[selected_layout_id]),
        'responseIds': list(response_ids),
        'budget': {
            'caseCount': len(cases),
            'maxConcurrentCases': 1,
            'executionTimeoutS': 7200,
        },
        'executionEstimate': {'mode': 'DAMPER_COMPARISON', 'realSolveCount': len(cases)},
        'fieldSources': dict(field_sources or {
            'solver': 'USER_SPECIFIED',
            'loadKind': 'DEFAULT',
            'selectedLayoutId': 'DEFAULT',
            'responseIds': 'USER_SPECIFIED' if response_ids else 'DEFAULT',
            'budget': 'DEFAULT',
        }),
    }


def build_parameter_sweep_contract(
    *,
    solver: str,
    cases: list[dict[str, Any]],
    response_ids: list[str],
    selected_layout_id: str = 'TWO_PER_TOWER',
    load_kind: str = 'EARTHQUAKE',
    max_concurrent_cases: int = 4,
    field_sources: dict[str, str] | None = None,
) -> dict[str, Any]:
    """构造只做批量正向求解的契约，不引入 DOE、代理模型或优化约束。"""
    if solver not in ENGINEERING_SOLVERS:
        raise ValueError(f'Unsupported engineering solver: {solver}')
    if load_kind not in PARAMETER_SWEEP_LOAD_KINDS:
        raise ValueError(f'Unsupported parameter sweep load kind: {load_kind}')
    # 求解器与荷载类型的组合不在这里拒绝：规划器的默认求解器是 OPENSEESPY_INPROC，
    # 用户只说"风"时会合法地产出 WIND + OPENSEESPY_INPROC。这类组合由
    # prepare_approval 返回结构化 UNSUPPORTED，与 ANALYSIS 风工况口径一致；
    # 在契约层抛异常会让规划请求变成 HTTP 500。
    if selected_layout_id not in STBRIDGE_LAYOUTS:
        raise ValueError(f'Unsupported engineering layout: {selected_layout_id}')
    if not 1 <= len(cases) <= 64:
        raise ValueError('Parameter sweep requires between one and 64 cases')
    if not 1 <= max_concurrent_cases <= 8:
        raise ValueError('max_concurrent_cases must be between one and eight')
    unknown_responses = set(response_ids) - RESPONSE_CATALOG
    if unknown_responses:
        raise ValueError(f'Unsupported responses: {sorted(unknown_responses)}')

    normalized_cases: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for raw_case in cases:
        case = DamperParameterSweepCase.model_validate(raw_case)
        if case.case_id in seen_ids:
            raise ValueError(f'Duplicate parameter sweep case id: {case.case_id}')
        seen_ids.add(case.case_id)
        registered = DAMPER_TYPES[case.damper_type]
        normalized_cases.append({
            'caseId': case.case_id,
            'damperType': case.damper_type,
            'parameters': dict(case.parameters),
            'solverModule': registered['solverModules'][solver],
            'productionReady': registered['productionReady'][solver],
        })

    return {
        'version': 1,
        'model': 'STbridge',
        'taskType': 'DAMPER_PARAMETER_SWEEP',
        'solver': solver,
        'loadKind': load_kind,
        'scenario': load_kind,
        'cases': normalized_cases,
        'selectedLayoutId': selected_layout_id,
        'selectedLayout': deepcopy(STBRIDGE_LAYOUTS[selected_layout_id]),
        'responseIds': list(response_ids),
        'budget': {
            'caseCount': len(normalized_cases),
            'maxConcurrentCases': min(max_concurrent_cases, len(normalized_cases)),
            'executionTimeoutS': 7200,
        },
        'executionEstimate': {
            'mode': 'DAMPER_PARAMETER_SWEEP',
            'realSolveCount': len(normalized_cases),
        },
        'fieldSources': dict(field_sources or {
            'solver': 'USER_SPECIFIED',
            'loadKind': 'DEFAULT',
            'selectedLayoutId': 'DEFAULT',
            'responseIds': 'USER_SPECIFIED' if response_ids else 'DEFAULT',
            'budget': 'USER_SPECIFIED',
        }),
    }
