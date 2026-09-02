from __future__ import annotations

import json
import os
from copy import deepcopy
from hashlib import sha256
from threading import Lock
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.agents.tools import (
    ToolExecutionError,
    ToolRisk,
)
from app.core.exceptions import LLMUnavailableError
from app.core.engineering_limits import DOE_INITIAL_MAX, DOE_INITIAL_MIN
from app.core.logging_config import get_platform_logger
from app.agents.inquiry import (
    InquiryTools,
    RESPONSE_COLUMN_ALIASES,
    RESPONSE_METRIC_SPECS,
    ResultAtTimeInput,
    ResultColumnsInput,
    ResultCorrelateInput,
    ResultCompareRunsInput,
    ResultCompareRunsOutput,
    ResultDerivedInput,
    ResultPeakInput,
    ResultSweepCasesInput,
    ResultTopsisInput,
)
from app.agents.task_registry import engineering_task_spec
from app.agents.workflows import (
    WorkflowDefinition,
    WorkflowGuard,
    WorkflowStep,
    WorkflowToolCall,
    freeze_workflow,
    workflow_definition,
)
from app.services.agent_repository import AgentRepository
from app.services.agent_project_context import engineering_project_context_service
from app.services.agent_engineering import EngineeringIntent
from app.services.agent_llm import HarnessToolCall, numbers_are_grounded, record_message_event
from app.services.platform_store import gen_id, platform_store, utc_now
from app.services.result_inquiry import ResultInquiryService
from app.services.agent_run_comparison import RunComparisonError, cross_run_comparison_service
from app.capabilities.runtime import (
    CapabilityDispatcher,
    CapabilityRegistry,
    EngineeringCapability,
    EvidencePolicy,
)
from app.capabilities.context import build_runtime_turn_payload
from app.capabilities.retention import build_compression_state_anchor


logger = get_platform_logger('agent_harness')



class WorkflowStartInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra='forbid', strict=True)

    task_type: Literal[
        'ANALYSIS',
        'DAMPER_COMPARISON',
        'DAMPER_PARAMETER_SWEEP',
        'DAMPER_OPTIMIZATION',
        'RESULT_INQUIRY',
    ] = Field(alias='taskType')
    engineering_intent: EngineeringIntent | None = Field(default=None, alias='engineeringIntent')


class HarnessRunInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra='forbid', strict=True)

    run_id: str = Field(alias='runId', pattern=r'^[A-Za-z0-9_-]{1,128}$')


class HarnessJobInput(HarnessRunInput):
    job_id: str = Field(alias='jobId', pattern=r'^[A-Za-z0-9_-]{1,128}$')


class HarnessArtifactInput(HarnessRunInput):
    artifact_id: str = Field(alias='artifactId', pattern=r'^[A-Za-z0-9_-]{1,128}$')


class HarnessApprovalDecisionInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra='forbid', strict=True)

    decision: Literal['APPROVE', 'REJECT']


class HarnessNoInput(BaseModel):
    model_config = ConfigDict(extra='forbid')


def _explicit_solver_from_user_content(content: str | None) -> str | None:
    """提取用户明确点名的唯一求解器，用于拒绝模型改写。"""

    normalized = str(content or '').casefold()
    mentioned: set[str] = set()
    if 'opensees' in normalized or '开放体系' in normalized:
        mentioned.add('OPENSEESPY_INPROC')
    if 'ansys' in normalized or 'mapdl' in normalized:
        mentioned.add('ANSYS')
    return next(iter(mentioned)) if len(mentioned) == 1 else None


class HarnessStageEvidenceOutput(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra='forbid', strict=True)

    source: Literal['platform_job']
    stage: str = Field(min_length=1)
    artifact_ids: list[str] = Field(alias='artifactIds')


class HarnessReviewOutput(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra='forbid', strict=True)

    accepted: bool
    run_status: str = Field(alias='runStatus')
    evidence_mode: str = Field(alias='evidenceMode')
    checks: dict[str, bool]
    message: str
    extra: dict[str, Any]


_WORKFLOW_OBSERVATION_TOOLS: frozenset[str] = frozenset({'workflow.observe'})


_CAPABILITY_REGISTRY = CapabilityRegistry()
for _capability in (
    EngineeringCapability(
        capability_id='analysis.plan',
        description='当 ANALYSIS 工作流处于需求确定步骤且用户意图完整时使用；不执行计算。',
        input_model=HarnessRunInput,
        risk=ToolRisk.READ_ONLY,
        requires_approval=False,
        prerequisites=('INTENT_RESOLVED',),
        evidence_policy=EvidencePolicy.NONE,
        idempotency_key_source=None,
    ),
    EngineeringCapability(
        capability_id='analysis.prepare',
        description='当单次分析已有登记荷载映射、需要检查环境和模型合同后再审批时使用。',
        input_model=HarnessRunInput,
        risk=ToolRisk.READ_ONLY,
        requires_approval=False,
        prerequisites=('MODEL_READY', 'LOAD_READY'),
        evidence_policy=EvidencePolicy.NONE,
        idempotency_key_source=None,
    ),
    EngineeringCapability(
        capability_id='analysis.review',
        description='当单次分析 Job 已结束并完成结果提取、需要按分析专用门槛审查时使用。',
        input_model=HarnessRunInput,
        risk=ToolRisk.READ_ONLY,
        requires_approval=False,
        prerequisites=('REAL_FEM_RESULT', 'REGISTERED_RESULT'),
        evidence_policy=EvidencePolicy.REAL_FEM_REQUIRED,
        idempotency_key_source=None,
    ),
    EngineeringCapability(
        capability_id='analysis.run',
        description='当单次分析已审批且预检通过、需要创建唯一真实求解 Job 时使用；参数来自审批冻结动作。',
        input_model=HarnessRunInput,
        risk=ToolRisk.SOLVER_EXECUTION,
        requires_approval=True,
        prerequisites=('FROZEN_CONTRACT', 'PREFLIGHT_PASSED', 'APPROVAL_GRANTED'),
        evidence_policy=EvidencePolicy.REAL_FEM_REQUIRED,
        idempotency_key_source='SERVER_DERIVED',
    ),
    EngineeringCapability(
        capability_id='analysis.visualize',
        description='当分析结果已通过证据审查且用户需要可审计报告图件时使用；不得改变工程结论。',
        input_model=HarnessRunInput,
        risk=ToolRisk.ARTIFACT_WRITE,
        requires_approval=False,
        prerequisites=('EVIDENCE_REVIEW_PASSED', 'REGISTERED_RESULT'),
        evidence_policy=EvidencePolicy.REGISTERED_ARTIFACT_ONLY,
        idempotency_key_source='SERVER_DERIVED',
    ),
    EngineeringCapability(
        capability_id='approval.decide',
        description='当当前步骤正在等待审批且用户明确批准或拒绝时使用；不接受范围修改。',
        input_model=HarnessApprovalDecisionInput,
        risk=ToolRisk.MUTATING,
        requires_approval=False,
        prerequisites=('PENDING_APPROVAL',),
        evidence_policy=EvidencePolicy.NONE,
        idempotency_key_source='SERVER_DERIVED',
    ),
    EngineeringCapability(
        capability_id='approval.request',
        description='当预检通过且需要向用户展示不可变冻结参数时使用；此工具不启动求解。',
        input_model=HarnessRunInput,
        risk=ToolRisk.ARTIFACT_WRITE,
        requires_approval=False,
        prerequisites=('FROZEN_CONTRACT', 'PREFLIGHT_PASSED'),
        evidence_policy=EvidencePolicy.NONE,
        idempotency_key_source='SERVER_DERIVED',
    ),
    EngineeringCapability(
        capability_id='comparison.calibrate',
        description='当两种阻尼器已确定且需要按相同最大出力基准生成可比参数时使用。',
        input_model=HarnessRunInput,
        risk=ToolRisk.ARTIFACT_WRITE,
        requires_approval=False,
        prerequisites=('COMPARISON_CASES_FROZEN',),
        evidence_policy=EvidencePolicy.NONE,
        idempotency_key_source='SERVER_DERIVED',
    ),
    EngineeringCapability(
        capability_id='comparison.compare',
        description='当两个阻尼器真实案例 Job 均已完成、需要按同一工程基准登记阶段对比结果时使用；不用于临时 CSV 追问。',
        input_model=HarnessJobInput,
        risk=ToolRisk.ARTIFACT_WRITE,
        requires_approval=False,
        prerequisites=('REAL_FEM_CASES_SUCCEEDED', 'REGISTERED_RESULT'),
        evidence_policy=EvidencePolicy.REAL_FEM_REQUIRED,
        idempotency_key_source='SERVER_DERIVED',
    ),
    EngineeringCapability(
        capability_id='comparison.plan',
        description='当阻尼器对比处于需求确定步骤且已明确两种不同阻尼器时使用；不执行求解。',
        input_model=HarnessRunInput,
        risk=ToolRisk.READ_ONLY,
        requires_approval=False,
        prerequisites=('INTENT_RESOLVED',),
        evidence_policy=EvidencePolicy.NONE,
        idempotency_key_source=None,
    ),
    EngineeringCapability(
        capability_id='comparison.prepare',
        description='当对比参数已标定且需要检查同一荷载、模型与真实求解环境时使用。',
        input_model=HarnessRunInput,
        risk=ToolRisk.READ_ONLY,
        requires_approval=False,
        prerequisites=('CALIBRATION_READY', 'MODEL_READY', 'LOAD_READY'),
        evidence_policy=EvidencePolicy.NONE,
        idempotency_key_source=None,
    ),
    EngineeringCapability(
        capability_id='comparison.review',
        description='当两个对比案例均结束且需要核对同一基准、标定和真实证据时使用。',
        input_model=HarnessRunInput,
        risk=ToolRisk.READ_ONLY,
        requires_approval=False,
        prerequisites=('COMPARISON_RESULT_REGISTERED',),
        evidence_policy=EvidencePolicy.REAL_FEM_REQUIRED,
        idempotency_key_source=None,
    ),
    EngineeringCapability(
        capability_id='comparison.run',
        description='当对比计划已审批且预检通过、需要串行创建两个真实案例时使用；不得并行求解。',
        input_model=HarnessRunInput,
        risk=ToolRisk.SOLVER_EXECUTION,
        requires_approval=True,
        prerequisites=('FROZEN_CONTRACT', 'PREFLIGHT_PASSED', 'APPROVAL_GRANTED'),
        evidence_policy=EvidencePolicy.REAL_FEM_REQUIRED,
        idempotency_key_source='SERVER_DERIVED',
    ),
    EngineeringCapability(
        capability_id='evidence.verify',
        description='当 RESULT_INQUIRY 工作流已完成只读查询、需要核对回答所引用证据时使用；工程工作流改用各自的 review 工具。',
        input_model=HarnessRunInput,
        risk=ToolRisk.READ_ONLY,
        requires_approval=False,
        prerequisites=('REGISTERED_RESULT',),
        evidence_policy=EvidencePolicy.REGISTERED_ARTIFACT_ONLY,
        idempotency_key_source=None,
    ),
    EngineeringCapability(
        capability_id='load.inspect',
        description='当需要读取当前 run 已登记荷载制品的结构、类型和哈希时使用；不修改源文件。',
        input_model=HarnessArtifactInput,
        risk=ToolRisk.READ_ONLY,
        requires_approval=False,
        prerequisites=('REGISTERED_LOAD_ARTIFACT',),
        evidence_policy=EvidencePolicy.REGISTERED_ARTIFACT_ONLY,
        idempotency_key_source=None,
    ),
    EngineeringCapability(
        capability_id='load.map_targets',
        description='当荷载制品检查通过且需要映射到登记工程目标集时使用；不得指定任意节点。',
        input_model=HarnessArtifactInput,
        risk=ToolRisk.ARTIFACT_WRITE,
        requires_approval=False,
        prerequisites=('REGISTERED_LOAD_ARTIFACT', 'MODEL_TARGETS_REGISTERED'),
        evidence_policy=EvidencePolicy.REGISTERED_ARTIFACT_ONLY,
        idempotency_key_source='SERVER_DERIVED',
    ),
    EngineeringCapability(
        capability_id='optimization.active_learning',
        description='当代理模型验证完成且 Python 策略要求补点时使用；最多执行冻结工作流允许的轮数。',
        input_model=HarnessRunInput,
        risk=ToolRisk.SOLVER_EXECUTION,
        requires_approval=True,
        prerequisites=('SURROGATE_VALIDATED', 'PREFLIGHT_PASSED', 'APPROVAL_GRANTED'),
        evidence_policy=EvidencePolicy.REAL_FEM_REQUIRED,
        idempotency_key_source='SERVER_DERIVED',
    ),
    EngineeringCapability(
        capability_id='optimization.fit_surrogate',
        description='当 DOE 结果制品完整且需要拟合并交叉验证代理模型时使用；不运行新 FEM。',
        input_model=HarnessRunInput,
        risk=ToolRisk.ARTIFACT_WRITE,
        requires_approval=False,
        prerequisites=('DOE_RESULTS_REGISTERED',),
        evidence_policy=EvidencePolicy.REGISTERED_ARTIFACT_ONLY,
        idempotency_key_source='SERVER_DERIVED',
    ),
    EngineeringCapability(
        capability_id='optimization.preflight',
        description='当优化方案已冻结且需要检查真实求解环境、模型和荷载后再审批时使用。',
        input_model=HarnessRunInput,
        risk=ToolRisk.READ_ONLY,
        requires_approval=False,
        prerequisites=('OPTIMIZATION_PLAN_FROZEN', 'MODEL_READY', 'LOAD_READY'),
        evidence_policy=EvidencePolicy.NONE,
        idempotency_key_source=None,
    ),
    EngineeringCapability(
        capability_id='optimization.prepare_plan',
        description='当优化需求字段完整且需要冻结 Python 受控方案和资源上限时使用；不得提前运行 DOE。',
        input_model=HarnessRunInput,
        risk=ToolRisk.READ_ONLY,
        requires_approval=False,
        prerequisites=('INTENT_RESOLVED',),
        evidence_policy=EvidencePolicy.NONE,
        idempotency_key_source=None,
    ),
    EngineeringCapability(
        capability_id='optimization.rank_candidates',
        description='当主动学习结束且需要在固定候选空间内枚举排序时使用；不得扩大候选空间。',
        input_model=HarnessRunInput,
        risk=ToolRisk.ARTIFACT_WRITE,
        requires_approval=False,
        prerequisites=('SURROGATE_VALIDATED', 'CANDIDATE_SPACE_FROZEN'),
        evidence_policy=EvidencePolicy.REGISTERED_ARTIFACT_ONLY,
        idempotency_key_source='SERVER_DERIVED',
    ),
    EngineeringCapability(
        capability_id='optimization.recommend',
        description='当候选排序完成且需要依据登记的 Pareto/TOPSIS 结果形成推荐时使用；不重算权重。',
        input_model=HarnessRunInput,
        risk=ToolRisk.ARTIFACT_WRITE,
        requires_approval=False,
        prerequisites=('CANDIDATE_RANKING_REGISTERED',),
        evidence_policy=EvidencePolicy.REGISTERED_ARTIFACT_ONLY,
        idempotency_key_source='SERVER_DERIVED',
    ),
    EngineeringCapability(
        capability_id='optimization.review',
        description='当候选 FEM 复核完成且需要执行优化专用最终证据审查时使用；最多一次受控修正。',
        input_model=HarnessRunInput,
        risk=ToolRisk.READ_ONLY,
        requires_approval=False,
        prerequisites=('CANDIDATE_VALIDATION_REGISTERED',),
        evidence_policy=EvidencePolicy.REAL_FEM_REQUIRED,
        idempotency_key_source=None,
    ),
    EngineeringCapability(
        capability_id='optimization.run_baseline',
        description='当优化已审批且预检通过、需要启动统一求解阶段时使用；服务器会将无控基线与 DOE 并行执行。',
        input_model=HarnessRunInput,
        risk=ToolRisk.SOLVER_EXECUTION,
        requires_approval=True,
        prerequisites=('FROZEN_CONTRACT', 'PREFLIGHT_PASSED', 'APPROVAL_GRANTED'),
        evidence_policy=EvidencePolicy.REAL_FEM_REQUIRED,
        idempotency_key_source='SERVER_DERIVED',
    ),
    EngineeringCapability(
        capability_id='optimization.run_doe',
        description='统一求解阶段的 DOE 子任务由服务器与无控基线并行执行；模型不得单独重启或修改样本数。',
        input_model=HarnessRunInput,
        risk=ToolRisk.SOLVER_EXECUTION,
        requires_approval=True,
        prerequisites=('FROZEN_CONTRACT', 'PREFLIGHT_PASSED', 'APPROVAL_GRANTED'),
        evidence_policy=EvidencePolicy.REAL_FEM_REQUIRED,
        idempotency_key_source='SERVER_DERIVED',
    ),
    EngineeringCapability(
        capability_id='optimization.validate_candidates',
        description='当推荐候选已生成且需要执行独立真实 FEM 复核时使用；只验证已登记候选。',
        input_model=HarnessRunInput,
        risk=ToolRisk.SOLVER_EXECUTION,
        requires_approval=True,
        prerequisites=('RECOMMENDATION_REGISTERED', 'PREFLIGHT_PASSED', 'APPROVAL_GRANTED'),
        evidence_policy=EvidencePolicy.REAL_FEM_REQUIRED,
        idempotency_key_source='SERVER_DERIVED',
    ),
    EngineeringCapability(
        capability_id='result.at_time',
        description='当用户询问指定时刻附近一个或多个响应值时使用；返回最近采样时刻并保持列名不变。',
        input_model=ResultAtTimeInput,
        risk=ToolRisk.READ_ONLY,
        requires_approval=False,
        prerequisites=('REGISTERED_RESULT',),
        evidence_policy=EvidencePolicy.REGISTERED_ARTIFACT_ONLY,
        idempotency_key_source=None,
    ),
    EngineeringCapability(
        capability_id='result.columns',
        description='当需要先确认已登记 CSV 可查询列名时使用；不得访问来源 run 白名单外的制品。',
        input_model=ResultColumnsInput,
        risk=ToolRisk.READ_ONLY,
        requires_approval=False,
        prerequisites=('REGISTERED_RESULT',),
        evidence_policy=EvidencePolicy.REGISTERED_ARTIFACT_ONLY,
        idempotency_key_source=None,
    ),
    EngineeringCapability(
        capability_id='result.compare',
        description='当用户需要比较同一已登记 CSV 两列绝对峰值时使用；一次返回差值、比值和相对变化，columns[0] 为基准。',
        input_model=ResultDerivedInput,
        risk=ToolRisk.READ_ONLY,
        requires_approval=False,
        prerequisites=('REGISTERED_RESULT',),
        evidence_policy=EvidencePolicy.REGISTERED_ARTIFACT_ONLY,
        idempotency_key_source=None,
    ),
    EngineeringCapability(
        capability_id='result.compare_runs',
        description='当用户需要比较 2–8 个同一 Project 的 SUCCEEDED + REAL_FEM 历史 Run 时使用；服务端先校验模型/荷载身份和单位，再计算基线差值、相对变化与允许的排名。跨求解器只用于一致性验证，不把差异解释为方案优劣。',
        input_model=ResultCompareRunsInput,
        risk=ToolRisk.READ_ONLY,
        requires_approval=False,
        prerequisites=('PROJECT_BOUND', 'VERIFIED_RESULT_AVAILABLE'),
        evidence_policy=EvidencePolicy.REGISTERED_ARTIFACT_ONLY,
        idempotency_key_source=None,
    ),
    EngineeringCapability(
        capability_id='result.correlate',
        description='当用户询问同一已登记 CSV 两列的线性同步关系时使用；Pearson 相关不表示因果。',
        input_model=ResultCorrelateInput,
        risk=ToolRisk.READ_ONLY,
        requires_approval=False,
        prerequisites=('REGISTERED_RESULT',),
        evidence_policy=EvidencePolicy.REGISTERED_ARTIFACT_ONLY,
        idempotency_key_source=None,
    ),
    EngineeringCapability(
        capability_id='result.extract',
        description='当真实 Job 成功结束且需要把原始输出登记为标准结果制品时使用；不得伪造数值。',
        input_model=HarnessRunInput,
        risk=ToolRisk.ARTIFACT_WRITE,
        requires_approval=False,
        prerequisites=('REAL_FEM_JOB_SUCCEEDED',),
        evidence_policy=EvidencePolicy.REAL_FEM_REQUIRED,
        idempotency_key_source='SERVER_DERIVED',
    ),
    EngineeringCapability(
        capability_id='result.find_recent',
        description='当结果追问开始且需要定位当前会话最近的可追问终态 run 时使用；不接受路径或制品 ID。',
        input_model=HarnessNoInput,
        risk=ToolRisk.READ_ONLY,
        requires_approval=False,
        prerequisites=('TERMINAL_RUN_AVAILABLE',),
        evidence_policy=EvidencePolicy.NONE,
        idempotency_key_source=None,
    ),
    EngineeringCapability(
        capability_id='result.peak',
        description='当用户询问某列绝对峰值、带符号峰值及发生时刻时使用；只读取一个已登记 CSV。',
        input_model=ResultPeakInput,
        risk=ToolRisk.READ_ONLY,
        requires_approval=False,
        prerequisites=('REGISTERED_RESULT',),
        evidence_policy=EvidencePolicy.REGISTERED_ARTIFACT_ONLY,
        idempotency_key_source=None,
    ),
    EngineeringCapability(
        capability_id='result.sweep_cases',
        description='当用户询问批量参数计算或阻尼器对比的跨算例聚合（按指标排序、参数敏感性表）时使用；只读取登记的批量/对比汇总 JSON（caseResults），不重新求解，不外推未计算参数。',
        input_model=ResultSweepCasesInput,
        risk=ToolRisk.READ_ONLY,
        requires_approval=False,
        prerequisites=('REGISTERED_RESULT',),
        evidence_policy=EvidencePolicy.REGISTERED_ARTIFACT_ONLY,
        idempotency_key_source=None,
    ),
    EngineeringCapability(
        capability_id='result.topsis',
        description='当用户询问已完成优化的 TOPSIS 排名或前 N 个候选时使用；只读取登记的优化摘要。多个优化结果并存时，必须根据 availableResults 与 catalogsByRunId 中的工况、模型、阻尼器、更新时间和 runId 匹配用户语义，不能默认选最近结果。',
        input_model=ResultTopsisInput,
        risk=ToolRisk.READ_ONLY,
        requires_approval=False,
        prerequisites=('REGISTERED_RESULT',),
        evidence_policy=EvidencePolicy.REGISTERED_ARTIFACT_ONLY,
        idempotency_key_source=None,
    ),
    EngineeringCapability(
        capability_id='sweep.plan',
        description='当阻尼器参数批量计算处于需求确定步骤且已明确一组显式参数案例时使用；不执行优化。',
        input_model=HarnessRunInput,
        risk=ToolRisk.READ_ONLY,
        requires_approval=False,
        prerequisites=('INTENT_RESOLVED',),
        evidence_policy=EvidencePolicy.NONE,
        idempotency_key_source=None,
    ),
    EngineeringCapability(
        capability_id='sweep.prepare',
        description='当批量参数案例已冻结且需要检查同一荷载、模型与真实求解环境时使用。',
        input_model=HarnessRunInput,
        risk=ToolRisk.READ_ONLY,
        requires_approval=False,
        prerequisites=('SWEEP_CASES_FROZEN', 'MODEL_READY', 'LOAD_READY'),
        evidence_policy=EvidencePolicy.NONE,
        idempotency_key_source=None,
    ),
    EngineeringCapability(
        capability_id='sweep.review',
        description='当批量参数案例均结束且需要核对每个案例的真实结果证据时使用。',
        input_model=HarnessRunInput,
        risk=ToolRisk.READ_ONLY,
        requires_approval=False,
        prerequisites=('REAL_FEM_CASES_SUCCEEDED', 'REGISTERED_RESULT'),
        evidence_policy=EvidencePolicy.REAL_FEM_REQUIRED,
        idempotency_key_source=None,
    ),
    EngineeringCapability(
        capability_id='sweep.run',
        description='当批量参数计算已审批且预检通过、需要并发创建真实案例时使用；不得添加优化约束或无控基线。',
        input_model=HarnessRunInput,
        risk=ToolRisk.SOLVER_EXECUTION,
        requires_approval=True,
        prerequisites=('FROZEN_CONTRACT', 'PREFLIGHT_PASSED', 'APPROVAL_GRANTED'),
        evidence_policy=EvidencePolicy.REAL_FEM_REQUIRED,
        idempotency_key_source='SERVER_DERIVED',
    ),
    EngineeringCapability(
        capability_id='workflow.complete',
        description='当当前工作流的证据审查和报告门禁均已通过时使用；不得提前声明完成。',
        input_model=HarnessRunInput,
        risk=ToolRisk.ARTIFACT_WRITE,
        requires_approval=False,
        prerequisites=('WORKFLOW_GATES_PASSED', 'REPORT_READY'),
        evidence_policy=EvidencePolicy.REGISTERED_ARTIFACT_ONLY,
        idempotency_key_source='SERVER_DERIVED',
    ),
    EngineeringCapability(
        capability_id='workflow.observe',
        description='当会话已有活动工程运行且用户询问进度、完成状态或结果是否可用时使用；只读取当前绑定 run，不启动、重启或修改任务。',
        input_model=HarnessNoInput,
        risk=ToolRisk.READ_ONLY,
        requires_approval=False,
        prerequisites=('ACTIVE_RUN',),
        evidence_policy=EvidencePolicy.NONE,
        idempotency_key_source=None,
    ),
    EngineeringCapability(
        capability_id='workflow.start',
        description='当尚未进入工程工作流且用户意图字段完整时使用；模型必须结合完整对话语义选择 Schema 中的目录 ID，执行层不会把错误标签静默翻译成 ID。',
        input_model=WorkflowStartInput,
        risk=ToolRisk.ARTIFACT_WRITE,
        requires_approval=False,
        prerequisites=('SESSION_ACTIVE', 'INTENT_RESOLVED'),
        evidence_policy=EvidencePolicy.NONE,
        idempotency_key_source='SERVER_DERIVED',
    ),
):
    _CAPABILITY_REGISTRY.register(_capability)

_CAPABILITY_DISPATCHER = CapabilityDispatcher(_CAPABILITY_REGISTRY)


def harness_capability_registry() -> CapabilityRegistry:
    return _CAPABILITY_REGISTRY


def _workflow_capability_ids() -> set[str]:
    expected = {'workflow.start'} | set(_WORKFLOW_OBSERVATION_TOOLS)
    for task_type in (
        'ANALYSIS',
        'DAMPER_COMPARISON',
        'DAMPER_PARAMETER_SWEEP',
        'DAMPER_OPTIMIZATION',
        'RESULT_INQUIRY',
    ):
        for step in workflow_definition(task_type).steps:
            expected.update(step.allowed_tools)
    return expected


def _validate_capability_registry() -> None:
    expected = _workflow_capability_ids()
    registered = set(_CAPABILITY_REGISTRY.list_ids())
    missing = sorted(expected - registered)
    extra = sorted(registered - expected)
    if missing or extra:
        raise RuntimeError(
            f'Capability Registry 与冻结 Workflow 定义不一致: missing={missing}, extra={extra}',
        )


_validate_capability_registry()


# Tool Schema 只是 Capability 面向模型 API 的阶段投影。缓存只保存当前阶段投影，
# 不再维护任何全局 tool union 或第二份 schema 真源。
_TOOL_CATALOG_CACHE: dict[Any, list[dict[str, Any]]] = {}
_TOOL_CATALOG_LOCK = Lock()


def harness_step_tool_catalog(allowed_tools: list[str] | tuple[str, ...]) -> list[dict[str, Any]]:
    """只向模型暴露冻结步骤当前允许的工具。"""
    key = ('STEP', tuple(sorted(set(allowed_tools))))
    with _TOOL_CATALOG_LOCK:
        cached = _TOOL_CATALOG_CACHE.get(key)
        if cached is None:
            cached = _build_harness_step_tool_catalog(allowed_tools)
            _TOOL_CATALOG_CACHE[key] = cached
        return deepcopy(cached)


def _build_harness_step_tool_catalog(
    allowed_tools: list[str] | tuple[str, ...],
) -> list[dict[str, Any]]:
    unknown = sorted(set(allowed_tools) - set(_CAPABILITY_REGISTRY.list_ids()))
    if unknown:
        raise ToolExecutionError(
            'TOOL_NOT_REGISTERED',
            '冻结工作流引用了未登记工具。',
            details={'tools': unknown},
        )
    return _CAPABILITY_REGISTRY.tool_schemas(tuple(dict.fromkeys(allowed_tools)))


def _bootstrap_definition() -> WorkflowDefinition:
    return WorkflowDefinition(
        workflowId='bootstrap',
        version='1.0.0',
        initialStep='ROUTING',
        terminalSteps=('STARTED', 'FAILED'),
        steps=(
            WorkflowStep(
                stepId='ROUTING',
                title='选择已登记工作流',
                allowedTools=('workflow.start',),
                prerequisites=(),
                successGate='workflow.selected == true',
                onSuccess='STARTED',
                failureRoutes={},
                retryPolicy={'maxAttempts': 2},
            ),
            WorkflowStep(
                stepId='STARTED',
                title='工作流已启动',
                allowedTools=(),
                prerequisites=('ROUTING',),
                successGate='terminal',
                onSuccess=None,
                failureRoutes={},
                retryPolicy={'maxAttempts': 1},
            ),
            WorkflowStep(
                stepId='FAILED',
                title='启动失败',
                allowedTools=(),
                prerequisites=(),
                successGate='terminal',
                onSuccess=None,
                failureRoutes={},
                retryPolicy={'maxAttempts': 1},
            ),
        ),
    )


def bootstrap_workflow_state(
    *,
    inquirable_run_id: str | None,
    requested_task: str | None = None,
) -> dict[str, Any]:
    return {
        'workflowId': 'bootstrap',
        'currentStep': 'ROUTING',
        'completedSteps': [],
        'allowedTools': ['workflow.start'],
        'requiredGate': 'workflow.selected == true',
        'remainingRetries': 2,
        'inquirableRunId': inquirable_run_id,
        'requestedTask': requested_task,
    }


def _initial_runtime_cursor(
    run: dict[str, Any],
    snapshot: dict[str, Any],
    task_type: str,
) -> tuple[str, list[str]]:
    """仅在首次挂载时建立游标；后续状态读取不得再次从 legacy status 反推。"""
    steps = {item['stepId']: item for item in snapshot.get('steps', [])}
    current = str(run.get('currentStep') or '')
    if current in steps:
        return current, [item for item in run.get('completedSteps', []) if item in steps]

    normalized_task = 'RESULT_INQUIRY' if task_type == 'INQUIRY' else task_type
    status = str(run.get('status') or '')
    if status == 'NEEDS_CLARIFICATION':
        target = 'REQUIREMENTS'
    elif status == 'WAITING_MAPPING':
        target = 'LOAD_PREPARATION' if 'LOAD_PREPARATION' in steps else 'REQUIREMENTS'
    elif status == 'WAITING_APPROVAL':
        target = 'WAITING_APPROVAL'
    elif status == 'WAITING_JOB':
        target = 'BASELINE' if normalized_task in {'DAMPER_OPTIMIZATION', 'FULL_OPTIMIZATION'} else 'EXECUTION'  # legacy FULL snapshots only
    elif status == 'REVIEWING':
        target = 'REVIEW' if normalized_task in {'DAMPER_OPTIMIZATION', 'FULL_OPTIMIZATION'} else 'EVIDENCE_REVIEW'  # legacy FULL snapshots only
    elif status in {'SUCCEEDED', 'COMPLETED_DIAGNOSTIC'}:
        target = 'COMPLETED'
    elif status == 'CANCELLED':
        target = 'CANCELLED'
    elif status == 'FAILED':
        target = 'FAILED'
    else:
        target = snapshot.get('initialStep', 'REQUIREMENTS')
    if target not in steps:
        target = snapshot.get('initialStep', 'REQUIREMENTS')

    completed: list[str] = []
    if target == 'COMPLETED':
        completed = [item['stepId'] for item in snapshot.get('steps', []) if item['stepId'] not in snapshot.get('terminalSteps', [])]
    else:
        completed.extend(steps[target].get('prerequisites', []))
    return target, list(dict.fromkeys(completed))


def _status_from_workflow_cursor(run: dict[str, Any]) -> str | None:
    """从受保护游标单向派生展示状态，不修改 currentStep/completedSteps。"""
    current = str(run.get('currentStep') or '')
    if current == 'COMPLETED':
        if run.get('status') == 'COMPLETED_DIAGNOSTIC':
            return 'COMPLETED_DIAGNOSTIC'
        if run.get('status') == 'SUCCEEDED':
            return 'SUCCEEDED'
        return None
    if current == 'FAILED':
        return 'FAILED'
    if current == 'CANCELLED':
        return 'CANCELLED'
    if current == 'WAITING_APPROVAL':
        return 'WAITING_APPROVAL'
    if current in {'EXECUTION', 'BASELINE'}:
        return 'WAITING_JOB'
    if current in {
        'RESULT_EXTRACTION', 'COMPARISON', 'DOE', 'SURROGATE',
        'ACTIVE_LEARNING', 'CANDIDATES', 'RECOMMENDATION', 'FEM_VALIDATION',
    }:
        if run.get('status') == 'WAITING_JOB':
            return 'WAITING_JOB'
        if run.get('status') == 'REVIEWING':
            return 'REVIEWING'
        return None
    if current in {'EVIDENCE_REVIEW', 'REVIEW', 'REPORT'}:
        return 'REVIEWING'
    if current == 'REQUIREMENTS' and run.get('status') == 'NEEDS_CLARIFICATION':
        return 'NEEDS_CLARIFICATION'
    if current == 'LOAD_PREPARATION' and run.get('status') == 'WAITING_MAPPING':
        return 'WAITING_MAPPING'
    if current == 'PREFLIGHT' and run.get('status') in {'PLANNING', 'PREFLIGHT'}:
        return 'PREFLIGHT'
    if current == 'REQUIREMENTS' and run.get('status') == 'PLANNING':
        return 'PLANNING'
    return None


class WorkflowHarnessMixin:
    # 无压缩可用时（LLM 未配置/压缩失败）的离线回退截断预算。
    _HARNESS_HISTORY_CHAR_BUDGET = 24000
    _HARNESS_TOOL_RESULT_CHAR_LIMIT = 4000
    _PERSISTENT_LOOP_MAX_MODEL_FAILURES = 3
    # 上下文感知压缩：窗口按 token 计（默认 200k，可用环境变量覆盖）。
    # 历史估算超过窗口的 TRIGGER 比例时触发压缩；压缩后逐字保留最近
    # KEEP_TAIL 比例的消息块，更早的块折叠成定向摘要。
    _CONTEXT_WINDOW_TOKENS_DEFAULT = 200_000
    _COMPRESSION_TRIGGER_RATIO = 0.5
    _COMPRESSION_KEEP_TAIL_RATIO = 0.25
    # 单次压缩调用的输入上限；超出时分片迭代折叠（摘要随片滚动更新）。
    _COMPRESSION_CHUNK_CHARS = 60_000
    _COMPRESSION_TARGET_CHARS = 2_000
    """原生 tool_calls 入口；真实工程步骤继续由现有 Python 服务执行。"""

    @staticmethod
    def _context_window_tokens() -> int:
        raw = os.getenv('MOMO_AGENT_CONTEXT_WINDOW_TOKENS', '')
        try:
            parsed = int(raw)
        except ValueError:
            parsed = WorkflowHarnessMixin._CONTEXT_WINDOW_TOKENS_DEFAULT
        if parsed <= 0:
            parsed = WorkflowHarnessMixin._CONTEXT_WINDOW_TOKENS_DEFAULT
        return parsed

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """粗略 token 估算：CJK 每字约 1 token，其余字符约 4 字符 1 token。"""
        cjk = sum(1 for char in text if '\u4e00' <= char <= '\u9fff')
        return cjk + max(0, (len(text) - cjk)) // 4 + 1

    @staticmethod
    def _runtime_mode() -> str:
        return str(os.getenv('MOMO_AGENT_RUNTIME', 'WORKFLOW_HARNESS')).strip().upper()

    @staticmethod
    def _persistent_loop_enabled() -> bool:
        """进程级默认值；只在 run 创建时读取一次并冻结进 run。"""
        return str(os.getenv('MOMO_AGENT_PERSISTENT_LOOP', 'false')).strip().lower() in {
            '1', 'true', 'yes', 'on',
        }

    @staticmethod
    def _run_persistent_loop(run: dict[str, Any]) -> bool:
        """按 run 冻结值判定完成路径；中途翻转环境变量不得改变既有 run 的行为。

        旧 run（冻结前创建）没有该字段，回退到进程级环境变量以保持兼容。
        """
        frozen = run.get('persistentLoop')
        if isinstance(frozen, bool):
            return frozen
        return WorkflowHarnessMixin._persistent_loop_enabled()

    @staticmethod
    def _set_harness_loop_state(
        run: dict[str, Any],
        *,
        status: Literal['READY', 'RUNNING', 'WAITING_EXTERNAL', 'COMPLETED', 'FAILED'],
        wake_reason: str,
        external_job_id: str | None = None,
        last_tool_call_id: str | None = None,
        error: dict[str, Any] | None = None,
        model_failure_count: int = 0,
    ) -> None:
        """把循环恢复点保存在 run 中，避免依赖进程内状态。"""
        previous = run.get('harnessLoop') if isinstance(run.get('harnessLoop'), dict) else {}
        loop = {
            'version': 1,
            'status': status,
            'revision': int(previous.get('revision') or 0) + 1,
            'wakeReason': wake_reason,
            'currentStep': run.get('currentStep'),
            'externalJobId': external_job_id,
            'lastToolCallId': last_tool_call_id,
            'modelFailureCount': max(int(model_failure_count), 0),
            'maxModelFailures': WorkflowHarnessMixin._PERSISTENT_LOOP_MAX_MODEL_FAILURES,
            'updatedAt': utc_now(),
        }
        if error is not None:
            loop['error'] = error
        run['harnessLoop'] = loop

    def _history_for_harness_turn(
        self,
        repository: AgentRepository,
        session_id: str,
        workflow_state: dict[str, Any],
        content: str,
    ) -> list[dict[str, Any]]:
        stored_messages = repository.list_messages(session_id)
        if stored_messages and str(stored_messages[-1].get('role') or '').upper() == 'USER':
            stored_messages[-1]['harnessContent'] = self._harness_user_content(workflow_state, content)
            save_message = getattr(repository, 'save_message', None)
            if callable(save_message):
                save_message(stored_messages[-1])
        return self._contextual_harness_history(
            repository,
            session_id,
            stored_messages[:-1],
            workflow_state=workflow_state,
            query=content,
        )

    def _contextual_harness_history(
        self,
        repository: AgentRepository,
        session_id: str,
        items: list[dict[str, Any]],
        *,
        workflow_state: dict[str, Any] | None,
        query: str,
    ) -> list[dict[str, Any]]:
        """带上下文感知压缩的历史构建。

        历史估算未超窗口预算时，直接返回既有摘要 + 逐字尾部；超过时把
        较早的消息块连同旧摘要一起折叠成以当前查询为导向的新摘要，
        摘要与覆盖位置持久化在会话上，跨请求复用且不重复压缩——两次
        压缩之间请求前缀保持字节稳定，对 KV cache 友好。
        压缩不可用（LLM 未配置/调用失败/消息缺 messageId）时回退到
        原有的字符预算截断，行为与压缩引入前一致。
        """
        planner_compress = getattr(getattr(self, 'planner', None), 'compress_context', None)
        if not callable(planner_compress):
            return self._bounded_harness_history(items)

        get_session = getattr(repository, 'get_session', None)
        session = get_session(session_id) if callable(get_session) else None
        state = session.get('harnessCompression') if isinstance(session, dict) else None
        if not isinstance(state, dict) or not state.get('summary'):
            state = None

        uncovered = items
        if state is not None:
            covered_id = str(state.get('coveredThroughMessageId') or '')
            cut = next(
                (index + 1 for index in range(len(items) - 1, -1, -1)
                 if str(items[index].get('messageId') or '') == covered_id),
                None,
            )
            if cut is None:
                # 覆盖点已不在历史里（消息被清理），摘要作废重新计算。
                state = None
            else:
                uncovered = items[cut:]

        summary_messages = [self._compression_summary_message(state)] if state else []
        window = self._context_window_tokens()
        trigger_tokens = int(window * self._COMPRESSION_TRIGGER_RATIO)
        projected = [self._native_history_message(item) for item in uncovered]
        block_sizes: list[tuple[int, int, int]] = []  # (start, end, tokens)
        cursor = 0
        for block in self._native_history_blocks(projected):
            start = cursor
            cursor += len(block)
            tokens = sum(
                self._estimate_tokens(json.dumps(message, ensure_ascii=False, separators=(',', ':')))
                for message in block
            )
            block_sizes.append((start, cursor, tokens))
        total_tokens = sum(tokens for _, _, tokens in block_sizes)
        if summary_messages:
            total_tokens += self._estimate_tokens(str(summary_messages[0].get('content') or ''))

        ceiling_chars = window * 4
        if total_tokens <= trigger_tokens:
            return summary_messages + self._bounded_harness_history(uncovered, char_budget=ceiling_chars)

        # 从尾部逐字保留 KEEP_TAIL 预算内的块，其余折叠进摘要。
        keep_tail_tokens = int(window * self._COMPRESSION_KEEP_TAIL_RATIO)
        kept_tokens = 0
        fold_until = len(block_sizes)
        for index in range(len(block_sizes) - 1, -1, -1):
            tokens = block_sizes[index][2]
            if kept_tokens + tokens > keep_tail_tokens:
                break
            kept_tokens += tokens
            fold_until = index
        if fold_until >= len(block_sizes):
            fold_until = len(block_sizes) - 1  # 至少折叠最老的一个块
        if fold_until <= 0:
            # 没有可折叠的完整块（尾部单块过大），退回硬截断。
            return summary_messages + self._bounded_harness_history(uncovered, char_budget=ceiling_chars)

        folded_item_end = block_sizes[fold_until - 1][1]
        covered_message_id = str(uncovered[folded_item_end - 1].get('messageId') or '')
        if not covered_message_id:
            return summary_messages + self._bounded_harness_history(uncovered, char_budget=ceiling_chars)

        folded_text = '\n'.join(
            json.dumps(message, ensure_ascii=False, separators=(',', ':'))
            for message in projected[:folded_item_end]
        )
        try:
            summary = self._compress_folded_context(
                previous_summary=str(state.get('summary') or '') if state else '',
                folded_text=folded_text,
                workflow_state=workflow_state,
                query=query,
            )
        except LLMUnavailableError as exc:
            logger.warning('上下文压缩不可用，回退历史截断: %s', exc)
            return summary_messages + self._bounded_harness_history(uncovered)

        previous_epoch = int(state.get('cacheEpoch') or 0) if state else 0
        cache_epoch = previous_epoch + 1
        active_run = None
        if workflow_state and workflow_state.get('runId'):
            active_run = repository.get_run(str(workflow_state['runId']))
        state_anchor = build_compression_state_anchor(
            workflow_state=workflow_state,
            run=active_run,
        )
        record_message_event('CONTEXT_COMPRESSION', {
            'foldedMessageCount': folded_item_end,
            'foldedChars': len(folded_text),
            'summaryChars': len(summary),
            'cacheEpoch': cache_epoch,
        })
        previous_covered = int(state.get('coveredMessageCount') or 0) if state else 0
        previous_source_chars = int(state.get('sourceChars') or 0) if state else 0
        new_state = {
            'version': 2,
            'cacheEpoch': cache_epoch,
            'stateAnchor': state_anchor,
            'summary': summary,
            'coveredThroughMessageId': covered_message_id,
            'coveredMessageCount': previous_covered + folded_item_end,
            'sourceChars': previous_source_chars + len(folded_text),
            'updatedAt': utc_now(),
        }
        if isinstance(session, dict):
            session['harnessCompression'] = new_state
            save_session = getattr(repository, 'save_session', None)
            if callable(save_session):
                save_session(session)
        tail_items = uncovered[folded_item_end:]
        return [self._compression_summary_message(new_state)] + self._bounded_harness_history(
            tail_items, char_budget=ceiling_chars,
        )

    def _compress_folded_context(
        self,
        *,
        previous_summary: str,
        folded_text: str,
        workflow_state: dict[str, Any] | None,
        query: str,
    ) -> str:
        """分片迭代折叠：摘要随片滚动更新，单次调用输入受 CHUNK 上限约束。"""
        summary = previous_summary
        chunk_chars = self._COMPRESSION_CHUNK_CHARS
        for offset in range(0, len(folded_text), chunk_chars):
            chunk = folded_text[offset:offset + chunk_chars]
            context = (f'[既有摘要]\n{summary}\n\n[待折叠历史]\n{chunk}') if summary else chunk
            summary = self.planner.compress_context(
                query=query,
                workflow_state=workflow_state,
                context=context,
                target_chars=self._COMPRESSION_TARGET_CHARS,
            )
        return summary

    @staticmethod
    def _compression_summary_message(state: dict[str, Any]) -> dict[str, Any]:
        return {
            'role': 'user',
            'content': json.dumps({
                'contextCompression': {
                    'note': '以下是较早对话历史的定向摘要，原始消息已按上下文预算折叠；如需精确数字请重新查询已登记制品。',
                    'summary': str(state.get('summary') or ''),
                    'stateAnchor': state.get('stateAnchor') or {},
                    'cacheEpoch': state.get('cacheEpoch') or 1,
                    'coveredMessageCount': state.get('coveredMessageCount'),
                },
            }, ensure_ascii=False, separators=(',', ':')),
        }

    @staticmethod
    def _active_workflow_state(run: dict[str, Any]) -> dict[str, Any]:
        """把冻结步骤状态投影为只允许观察的会话状态。"""
        state = WorkflowHarnessMixin._workflow_state_from_run(run)
        step_action_tools = list(state.get('allowedTools') or [])
        state.update({
            'runId': run.get('runId'),
            'taskType': run.get('taskType'),
            'runStatus': run.get('status'),
            'jobId': run.get('jobId'),
            'allowedTools': sorted(_WORKFLOW_OBSERVATION_TOOLS),
            'stepActionTools': step_action_tools,
            'executionOwner': (
                'LLM_PERSISTENT_LOOP'
                if WorkflowHarnessMixin._run_persistent_loop(run)
                else 'PYTHON_JOB'
            ),
            'harnessLoop': run.get('harnessLoop'),
        })
        return state

    @staticmethod
    def _workflow_observation(run: dict[str, Any]) -> dict[str, Any]:
        """返回足以回答进度问题的紧凑事实，不复制大型结果。"""
        return {
            'runId': run.get('runId'),
            'taskType': run.get('taskType'),
            'runStatus': run.get('status'),
            'currentStage': run.get('currentStage'),
            'currentStep': run.get('currentStep'),
            'completedSteps': list(run.get('completedSteps') or []),
            'jobId': run.get('jobId'),
            'reportReady': bool(run.get('reportArtifactId')),
            'reportArtifactId': run.get('reportArtifactId'),
            'artifactIds': list(run.get('artifactIds') or []),
            'workflowGateError': run.get('workflowGateError'),
            'harnessLoop': run.get('harnessLoop'),
        }

    @staticmethod
    def _active_status_reply(
        status: str,
        *,
        current_step: str | None = None,
        harness_loop: dict[str, Any] | None = None,
    ) -> str:
        if (
            status == 'WAITING_JOB'
            and isinstance(harness_loop, dict)
            and harness_loop.get('status') in {'READY', 'RUNNING'}
            and current_step not in {'EXECUTION', 'BASELINE'}
        ):
            return f'真实求解已经结束，当前正在按顺序处理 {current_step or "内部"} 阶段，最终报告尚未生成。'
        return {
            'WAITING_JOB': '任务已在执行队列中，当前仍在真实求解，最终报告尚未生成。',
            'REVIEWING': '真实求解已经结束，当前正在验收证据并生成报告。',
            'SUCCEEDED': '计算已经完成，结果报告已生成，可以继续查询具体响应。',
            'COMPLETED_DIAGNOSTIC': '计算已经结束，但证据验收未完全通过，请查看诊断报告。',
            'FAILED': '任务执行失败，运行记录已保留，请查看结构化失败原因。',
            'CANCELLED': '任务已经取消，没有继续执行。',
        }.get(status, f'任务当前状态为 {status}。')

    def _reply_on_active_run(
        self,
        repository: AgentRepository,
        session: dict[str, Any],
        run: dict[str, Any],
        reply: str,
    ) -> dict[str, Any]:
        repository.add_message({
            'messageId': gen_id('msg'),
            'sessionId': session['sessionId'],
            'role': 'ASSISTANT',
            'content': reply,
            'runId': run['runId'],
            'createdAt': utc_now(),
        })
        session['updatedAt'] = utc_now()
        repository.save_session(session)
        return self._decorate_run(run)

    def _dispatch_harness_active_run_message(
        self,
        repository: AgentRepository,
        session: dict[str, Any],
        run: dict[str, Any],
        content: str,
        now: str,
        load_import: dict[str, Any] | None,
        event_sink: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """续接审批后的活动 run，通过观察工具回答而不是重新启动工作流。"""
        run = self._refresh_agent_run(repository, run)
        if run.get('status') in {'SUCCEEDED', 'COMPLETED_DIAGNOSTIC'} and run.get('reportArtifactId'):
            return self._create_native_inquiry_run(
                repository,
                session,
                run,
                content,
                now,
                prior_messages=None,
                event_sink=event_sink,
            )
        if run.get('status') in {'FAILED', 'CANCELLED'}:
            return self._reply_on_active_run(
                repository,
                session,
                run,
                self._active_status_reply(str(run.get('status'))),
            )

        workflow_state = self._active_workflow_state(run)
        base_history = self._history_for_harness_turn(
            repository,
            session['sessionId'],
            workflow_state,
            content,
        )
        capability_tools = harness_step_tool_catalog(['workflow.observe'])
        self._persist_harness_runtime_snapshot(
            repository, session['sessionId'], workflow_state=workflow_state, content=content,
            tools=capability_tools, turn_context=None,
        )
        turn_history = list(base_history)
        turn = None
        call = None
        for attempt in range(3):
            turn = self.planner.run_harness_turn(
                messages=turn_history,
                user_content=content,
                workflow_state=workflow_state,
                tools=capability_tools,
            )
            if len(turn.tool_calls) == 1 and turn.tool_calls[0].name == 'workflow.observe':
                call = turn.tool_calls[0]
                break
            if attempt >= 2:
                break
            turn_history.append({
                'role': 'user',
                'content': json.dumps({
                    'formatCorrection': {
                        'code': 'ACTIVE_RUN_OBSERVATION_REQUIRED',
                        'instruction': '当前已有活动运行。先调用 workflow.observe，不得调用 workflow.start 或重新询问已冻结需求。',
                    },
                }, ensure_ascii=False, separators=(',', ':')),
            })

        if call is None or turn is None:
            status = str(run.get('status') or 'UNKNOWN')
            return self._reply_on_active_run(
                repository,
                session,
                run,
                self._active_status_reply(
                    status,
                    current_step=str(run.get('currentStep') or '') or None,
                    harness_loop=run.get('harnessLoop'),
                ),
            )

        try:
            effective_arguments = _CAPABILITY_DISPATCHER.authorize_and_validate(
                'workflow.observe',
                call.arguments,
                allowed_capabilities=['workflow.observe'],
            ).model_dump(by_alias=True, mode='json')
            WorkflowGuard().authorize(
                workflow_snapshot=run['workflowSnapshot'],
                current_step=str(run.get('currentStep') or ''),
                completed_steps=run.get('completedSteps') or [],
                step_attempt=int(run.get('stepAttempt') or 1),
                observational_tools=_WORKFLOW_OBSERVATION_TOOLS,
                tool_call=WorkflowToolCall(
                    name=call.name,
                    arguments=call.arguments,
                    risk=ToolRisk.READ_ONLY,
                ),
            )
        except (ValidationError, ToolExecutionError) as exc:
            message = str(getattr(exc, 'message', exc))
            return self._reply_on_active_run(
                repository,
                session,
                run,
                f'无法读取当前任务状态：{message}',
            )

        run = self._refresh_agent_run(repository, run)
        observation = self._workflow_observation(run)
        current_state = self._active_workflow_state(run)
        self._record_read_only_tool_call(
            repository,
            run_id=run['runId'],
            call_id=call.tool_call_id,
            name=call.name,
            arguments=call.arguments,
            effective_arguments=effective_arguments,
            output=observation,
            cached_tokens=turn.cached_tokens,
            step_id=str(run.get('currentStep') or 'OBSERVE'),
        )
        tool_result = {
            'effectiveArguments': effective_arguments,
            'result': observation,
            'workflowState': current_state,
        }
        self._persist_native_tool_exchange(
            repository,
            session_id=session['sessionId'],
            run_id=run['runId'],
            call=call,
            assistant_content=turn.content,
            tool_result=tool_result,
        )
        transcript = [
            *base_history,
            {
                'role': 'user',
                'content': self._harness_user_content(workflow_state, content, tools=capability_tools),
            },
            {
                'role': 'assistant',
                'content': turn.content,
                'tool_calls': [{
                    'id': call.tool_call_id,
                    'type': 'function',
                    'function': {
                        'name': call.name,
                        'arguments': json.dumps(call.arguments, ensure_ascii=False),
                    },
                }],
            },
            {
                'role': 'tool',
                'tool_call_id': call.tool_call_id,
                'name': call.name,
                'content': json.dumps(tool_result, ensure_ascii=False),
            },
        ]
        final_turn = self.planner.run_harness_turn(
            messages=transcript,
            user_content='请直接根据刚才的观察结果回答最初问题；不得重新启动任务或重复询问已冻结参数。',
            workflow_state=current_state,
            tools=capability_tools,
        )
        reply = str(final_turn.content or '').strip()
        if final_turn.tool_calls or not reply:
            status = str(observation.get('runStatus') or 'UNKNOWN')
            reply = self._active_status_reply(
                status,
                current_step=str(observation.get('currentStep') or '') or None,
                harness_loop=observation.get('harnessLoop'),
            )
        return self._reply_on_active_run(repository, session, run, reply)

    def _dispatch_harness_approval_reply(
        self,
        repository: AgentRepository,
        session: dict[str, Any],
        run: dict[str, Any],
        content: str,
        now: str,
    ) -> dict[str, Any]:
        """在冻结审批步骤内用原生 tool call 消费批准/拒绝回复。"""
        if run.get('runtimeMode') != 'WORKFLOW_HARNESS' or not run.get('workflowSnapshot'):
            # STANDARDIZE_LOAD 等旧审批没有工作流快照，必须回到兼容审批处理。
            return self._resolve_approval_reply(repository, session, run, content, now)
        workflow_state = self._workflow_state_from_run(run)
        history = self._history_for_harness_turn(
            repository,
            session['sessionId'],
            workflow_state,
            content,
        )
        capability_tools = harness_step_tool_catalog(['approval.decide'])
        self._persist_harness_runtime_snapshot(
            repository, session['sessionId'], workflow_state=workflow_state, content=content,
            tools=capability_tools, turn_context=None,
        )
        turn = self.planner.run_harness_turn(
            messages=history,
            user_content=content,
            workflow_state=workflow_state,
            tools=capability_tools,
        )
        call = turn.tool_calls[0] if turn.tool_calls else None
        if call is None or call.name != 'approval.decide':
            reply = '请明确回复“批准执行”或“拒绝执行”。'
            repository.add_message({
                'messageId': gen_id('msg'),
                'sessionId': session['sessionId'],
                'role': 'ASSISTANT',
                'content': reply,
                'runId': run['runId'],
                'createdAt': utc_now(),
            })
            session['updatedAt'] = utc_now()
            repository.save_session(session)
            return self._decorate_run(run)
        try:
            approval_input = _CAPABILITY_DISPATCHER.authorize_and_validate(
                'approval.decide',
                call.arguments,
                allowed_capabilities=['approval.decide'],
                approved=True,
                idempotency_key=f'{run["runId"]}:approval:{call.tool_call_id}',
            )
        except ToolExecutionError as exc:
            return self._create_harness_failure_run(
                repository,
                session,
                content,
                now,
                code=exc.code,
                message=exc.message,
                details=exc.details,
            )
        decision = approval_input.decision
        effective_arguments = approval_input.model_dump(by_alias=True, mode='json')
        try:
            WorkflowGuard().authorize(
                workflow_snapshot=run['workflowSnapshot'],
                current_step=str(run.get('currentStep') or ''),
                completed_steps=run.get('completedSteps') or [],
                tool_call=WorkflowToolCall(
                    name=call.name,
                    arguments=call.arguments,
                    risk=ToolRisk.MUTATING,
                    approved=True,
                    idempotencyKey=f'{run["runId"]}:approval:{call.tool_call_id}',
                ),
            )
        except ToolExecutionError as exc:
            return self._create_harness_failure_run(
                repository,
                session,
                content,
                now,
                code=exc.code,
                message=exc.message,
                details=exc.details,
            )
        approval_id = str(run.get('pendingApprovalId') or '')
        result = self.decide_approval(approval_id, decision == 'APPROVE')
        updated_run = result['run']
        self._record_harness_tool_call(
            repository,
            run_id=run['runId'],
            call_id=call.tool_call_id,
            name=call.name,
            arguments=call.arguments,
            effective_arguments=effective_arguments,
            cached_tokens=turn.cached_tokens,
        )
        self._persist_native_tool_exchange(
            repository,
            session_id=session['sessionId'],
            run_id=updated_run['runId'],
            call=call,
            assistant_content=turn.content,
            tool_result={
                'decision': decision,
                'approvalId': approval_id,
                'effectiveArguments': effective_arguments,
                'workflowState': self._workflow_state_from_run(updated_run),
            },
        )
        repository.add_message({
            'messageId': gen_id('msg'),
            'sessionId': session['sessionId'],
            'role': 'ASSISTANT',
            'content': '已批准，开始执行审批冻结的工程任务。' if decision == 'APPROVE' else '已拒绝本次执行，未启动工程求解。',
            'runId': updated_run['runId'],
            'createdAt': utc_now(),
        })
        session['updatedAt'] = utc_now()
        repository.save_session(session)
        return self._decorate_run(updated_run)

    def _dispatch_harness_clarification_reply(
        self,
        repository: AgentRepository,
        session: dict[str, Any],
        run: dict[str, Any],
        content: str,
        now: str,
        load_import: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """用 workflow.start 的类型化意图合并澄清，不重新走旧分类器。"""
        history = self._history_for_harness_turn(
            repository,
            session['sessionId'],
            self._workflow_state_from_run(run),
            content,
        )
        workflow_state = self._workflow_state_from_run(run)
        project_context = engineering_project_context_service.build(
            repository=repository,
            session_id=session['sessionId'],
            owner=str(session.get('ownerId') or 'local'),
            query=content,
            requested_task=str(run.get('taskType') or ''),
        )
        turn_context = (
            {'engineeringProjectContext': project_context} if project_context else None
        )
        capability_tools = harness_step_tool_catalog(['workflow.start'])
        self._persist_harness_runtime_snapshot(
            repository, session['sessionId'], workflow_state=workflow_state, content=content,
            tools=capability_tools, turn_context=turn_context,
        )
        memory_field_sources: dict[str, str] = {}
        for correction_attempt in range(3):
            turn = self.planner.run_harness_turn(
                messages=history,
                user_content=content,
                workflow_state=workflow_state,
                tools=capability_tools,
                turn_context=turn_context,
            )
            call = turn.tool_calls[0] if turn.tool_calls else None
            if call is None or call.name != 'workflow.start':
                return self._create_harness_failure_run(
                    repository,
                    session,
                    content,
                    now,
                    code='HARNESS_CLARIFICATION_REQUIRED',
                    message='澄清阶段需要模型返回 workflow.start 的类型化工程意图。',
                )
            try:
                start = _CAPABILITY_DISPATCHER.authorize_and_validate(
                    'workflow.start',
                    call.arguments,
                    allowed_capabilities=['workflow.start'],
                    idempotency_key=f'{run["runId"]}:clarify:{call.tool_call_id}',
                )
                if start.task_type != run.get('taskType'):
                    raise ValueError('澄清回复的 taskType 与原 run 不匹配')
                if start.engineering_intent is None:
                    raise ValueError('澄清回复缺少 engineeringIntent')
                intent_for_plan, memory_field_sources = engineering_project_context_service.resolve_intent(
                    start.engineering_intent,
                    project_context=project_context,
                    user_content=content,
                    prior_user_content=str(run.get('goal') or ''),
                )
                start = start.model_copy(update={'engineering_intent': intent_for_plan})
                task_spec = engineering_task_spec(str(run.get('taskType')))
                if task_spec is None:
                    raise ValueError(f'未登记的工程任务类型: {run.get("taskType")}')
                plan_tool = task_spec.plan_tool
                WorkflowGuard().authorize(
                    workflow_snapshot=run['workflowSnapshot'],
                    current_step=str(run.get('currentStep') or 'REQUIREMENTS'),
                    completed_steps=run.get('completedSteps') or [],
                    tool_call=WorkflowToolCall(name=plan_tool),
                )
                break
            except (ValidationError, ValueError, ToolExecutionError) as exc:
                code = exc.code if isinstance(exc, ToolExecutionError) else 'INPUT_VALIDATION_ERROR'
                details = exc.details if isinstance(exc, ToolExecutionError) else {'message': str(exc)}
                if correction_attempt >= 2:
                    return self._create_harness_failure_run(
                        repository,
                        session,
                        content,
                        now,
                        code=code,
                        message=str(getattr(exc, 'message', exc)),
                        details=details,
                    )
                history.extend([
                    {
                        'role': 'assistant',
                        'content': turn.content,
                        'tool_calls': [{
                            'id': call.tool_call_id,
                            'type': 'function',
                            'function': {
                                'name': call.name,
                                'arguments': json.dumps(call.arguments, ensure_ascii=False),
                            },
                        }],
                    },
                    {
                        'role': 'tool',
                        'tool_call_id': call.tool_call_id,
                        'content': json.dumps({
                            'error': {'code': code, 'message': str(getattr(exc, 'message', exc))},
                            'workflowState': workflow_state,
                        }, ensure_ascii=False),
                    },
                ])
        effective_arguments = start.model_dump(by_alias=True, mode='json')
        resolved = self._resolve_clarification(
            repository,
            session,
            run,
            content,
            now,
            load_import,
            intent_override=intent_for_plan,
            planner_mode_override='LLM_TOOL_CALL',
            field_sources_override=memory_field_sources,
        )
        # 旧澄清实现会更新业务 status，但不会推进 Harness 游标；仅在这次
        # 澄清成功提交后重新建立一次初始游标，后续读取仍只认游标本身。
        if (
            resolved.get('runtimeMode') == 'WORKFLOW_HARNESS'
            and resolved.get('workflowSnapshot')
            and resolved.get('currentStep') == 'REQUIREMENTS'
            and resolved.get('status') != 'NEEDS_CLARIFICATION'
        ):
            resolved['currentStep'] = ''
            resolved['completedSteps'] = []
            self._attach_workflow_runtime(
                repository,
                resolved,
                str(resolved.get('taskType') or start.task_type),
            )
        self._record_harness_tool_call(
            repository,
            run_id=resolved['runId'],
            call_id=call.tool_call_id,
            name=call.name,
            arguments=call.arguments,
            effective_arguments=effective_arguments,
            cached_tokens=turn.cached_tokens,
        )
        self._persist_native_tool_exchange(
            repository,
            session_id=session['sessionId'],
            run_id=resolved['runId'],
            call=call,
            assistant_content=turn.content,
            tool_result={
                'resolvedTask': start.task_type,
                'effectiveArguments': effective_arguments,
                'workflowState': self._workflow_state_from_run(resolved),
            },
        )
        return self._decorate_run(resolved)

    def _dispatch_harness_message(
        self,
        repository: AgentRepository,
        session: dict[str, Any],
        content: str,
        now: str,
        load_import: dict[str, Any] | None,
        inquirable_run: dict[str, Any] | None,
        requested_task: str | None = None,
        event_sink: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        workflow_state = bootstrap_workflow_state(
            inquirable_run_id=inquirable_run.get('runId') if inquirable_run else None,
            requested_task=requested_task,
        )
        messages = self._history_for_harness_turn(
            repository,
            session['sessionId'],
            workflow_state,
            content,
        )
        project_context = engineering_project_context_service.build(
            repository=repository,
            session_id=session['sessionId'],
            owner=str(session.get('ownerId') or 'local'),
            query=content,
            requested_task=requested_task,
        )
        turn_context = (
            {'engineeringProjectContext': project_context} if project_context else None
        )
        capability_tools = harness_step_tool_catalog(workflow_state['allowedTools'])
        self._persist_harness_runtime_snapshot(
            repository, session['sessionId'], workflow_state=workflow_state, content=content,
            tools=capability_tools, turn_context=turn_context,
        )
        memory_field_sources: dict[str, str] = {}
        for correction_attempt in range(3):
            turn = self.planner.run_harness_turn(
                messages=messages,
                user_content=content,
                workflow_state=workflow_state,
                tools=capability_tools,
                turn_context=turn_context,
            )
            if not turn.tool_calls:
                if turn.content and turn.content.strip():
                    return self._create_harness_text_run(
                        repository, session, content, now, turn.content.strip(), turn.cached_tokens,
                    )
                return self._create_harness_failure_run(
                    repository,
                    session,
                    content,
                    now,
                    code='HARNESS_EMPTY_RESPONSE',
                    message='模型没有返回可执行工具或文字回复。',
                )
            if len(turn.tool_calls) != 1:
                return self._create_harness_failure_run(
                    repository,
                    session,
                    content,
                    now,
                    code='HARNESS_MULTIPLE_TOOL_CALLS',
                    message='工作流启动阶段每轮只允许选择一个工具。',
                )
            call = turn.tool_calls[0]
            try:
                WorkflowGuard().authorize(
                    workflow_snapshot=freeze_workflow(_bootstrap_definition())['workflowSnapshot'],
                    current_step='ROUTING',
                    tool_call=WorkflowToolCall(
                        name=call.name,
                        arguments=call.arguments,
                        risk=ToolRisk.MUTATING,
                        idempotencyKey=f'{session["sessionId"]}:{call.tool_call_id}',
                    ),
                )
                capability = _CAPABILITY_REGISTRY.require(call.name)
                start = _CAPABILITY_DISPATCHER.authorize_and_validate(
                    call.name,
                    call.arguments,
                    allowed_capabilities=workflow_state['allowedTools'],
                    idempotency_key=f'{session["sessionId"]}:{call.tool_call_id}',
                )
            except ToolExecutionError as exc:
                if correction_attempt >= 2:
                    return self._create_harness_failure_run(
                        repository,
                        session,
                        content,
                        now,
                        code=exc.code,
                        message=exc.message,
                        details=exc.details,
                    )
                messages.extend([
                    {
                        'role': 'assistant',
                        'content': turn.content,
                        'tool_calls': [{
                            'id': call.tool_call_id,
                            'type': 'function',
                            'function': {
                                'name': call.name,
                                'arguments': json.dumps(call.arguments, ensure_ascii=False),
                            },
                        }],
                    },
                    {
                        'role': 'tool',
                        'tool_call_id': call.tool_call_id,
                        'content': json.dumps({
                            'error': {
                                'code': exc.code,
                                'message': exc.message,
                                'details': exc.details,
                            },
                            'workflowState': workflow_state,
                        }, ensure_ascii=False),
                    },
                ])
                continue
            try:
                if start.engineering_intent is not None:
                    resolved_intent, memory_field_sources = (
                        engineering_project_context_service.resolve_intent(
                            start.engineering_intent,
                            project_context=project_context,
                            user_content=content,
                        )
                    )
                    start = start.model_copy(update={'engineering_intent': resolved_intent})
                semantic_error = self._workflow_start_error(start, user_content=content)
                if semantic_error:
                    raise ValueError(semantic_error)
                break
            except (ValidationError, ValueError) as exc:
                if correction_attempt >= 2:
                    return self._create_harness_failure_run(
                        repository,
                        session,
                        content,
                        now,
                        code='INPUT_VALIDATION_ERROR',
                        message='workflow.start 参数连续三次未通过校验。',
                        details={'message': str(exc)},
                    )
                messages.extend([
                    {
                        'role': 'assistant',
                        'content': turn.content,
                        'tool_calls': [{
                            'id': call.tool_call_id,
                            'type': 'function',
                            'function': {
                                'name': call.name,
                                'arguments': json.dumps(call.arguments, ensure_ascii=False),
                            },
                        }],
                    },
                    {
                        'role': 'tool',
                        'tool_call_id': call.tool_call_id,
                        'content': json.dumps({
                            'error': {'code': 'INPUT_VALIDATION_ERROR', 'message': str(exc)},
                            'workflowState': workflow_state,
                        }, ensure_ascii=False),
                    },
                ])

        effective_arguments = start.model_dump(by_alias=True, mode='json')
        task_type = start.task_type
        if requested_task and task_type != requested_task:
            return self._create_harness_failure_run(
                repository,
                session,
                content,
                now,
                code='WORKFLOW_TASK_MISMATCH',
                message=f'界面指定任务 {requested_task}，模型请求启动 {task_type}。',
            )
        route_evidence = {
            'routeMode': 'LLM_TOOL_CALL',
            'resolvedTask': task_type,
            'reason': '模型通过 workflow.start 选择已登记 Python 工作流。',
            **({
                'projectId': project_context['project']['projectId'],
                'workspaceRevision': project_context['project']['workspaceRevision'],
            } if project_context else {}),
        }
        if task_type == 'RESULT_INQUIRY':
            if inquirable_run is None:
                return self._create_harness_failure_run(
                    repository,
                    session,
                    content,
                    now,
                    code='INQUIRABLE_RUN_NOT_FOUND',
                    message='当前结果目录没有可供只读查询的终态结果。',
                )
            result = self._create_native_inquiry_run(
                repository,
                session,
                inquirable_run,
                content,
                now,
                prior_messages=messages,
                event_sink=event_sink,
            )
        else:
            intent = start.engineering_intent
            result = self._create_engineering_run(
                repository,
                session,
                content,
                now,
                requested_task=task_type,
                load_import=load_import,
                route_evidence=route_evidence,
                intent_override=intent,
                field_sources_override=memory_field_sources,
            )
        run_id = str(result['runId'])
        stored = repository.get_run(run_id) or dict(result)
        self._attach_workflow_runtime(repository, stored, task_type)
        self._record_harness_tool_call(
            repository,
            run_id=run_id,
            call_id=call.tool_call_id,
            name=call.name,
            arguments=call.arguments,
            effective_arguments=effective_arguments,
            cached_tokens=turn.cached_tokens,
        )
        self._persist_native_tool_exchange(
            repository,
            session_id=session['sessionId'],
            run_id=run_id,
            call=call,
            assistant_content=turn.content,
            tool_result={
                'effectiveArguments': effective_arguments,
                'workflowState': self._workflow_state_from_run(stored),
                'routeEvidence': route_evidence,
            },
        )
        return self._decorate_run(stored)

    @staticmethod
    def _workflow_start_error(
        start: WorkflowStartInput,
        *,
        user_content: str | None = None,
    ) -> str | None:
        if start.task_type == 'RESULT_INQUIRY':
            return None
        explicit_solver = _explicit_solver_from_user_content(user_content)
        selected_solver = (
            start.engineering_intent.solver if start.engineering_intent is not None else None
        )
        if explicit_solver and selected_solver and selected_solver != explicit_solver:
            requested_label = 'OpenSeesPy' if explicit_solver == 'OPENSEESPY_INPROC' else 'ANSYS'
            selected_label = 'OpenSeesPy' if selected_solver == 'OPENSEESPY_INPROC' else 'ANSYS'
            return (
                f'用户明确指定 {requested_label}，workflow.start 不得改写为 {selected_label}；'
                '请保留用户求解器并选择与其兼容的工作流。'
            )
        intent = start.engineering_intent
        if intent is None or intent.task_type != start.task_type:
            return '缺少 taskType 一致的 engineeringIntent'
        if intent.missing_fields:
            return f'仍缺少 {", ".join(intent.missing_fields)}；不要启动工作流，应直接向用户追问'
        if intent.solver is None:
            return 'engineeringIntent 缺少 solver'
        if start.task_type == 'DAMPER_OPTIMIZATION' and intent.damper_type is None:
            return '阻尼优化缺少 damperType'
        if start.task_type == 'DAMPER_COMPARISON' and len(intent.damper_types) not in {2, 3}:
            return '阻尼器对比必须提供两种或三种 damperTypes'
        if start.task_type == 'DAMPER_PARAMETER_SWEEP':
            if not intent.cases:
                return '阻尼器参数批量计算缺少 cases'
            if len({case.case_id for case in intent.cases}) != len(intent.cases):
                return '阻尼器参数批量计算的 caseId 必须唯一'
        return None

    def _create_native_inquiry_run(
        self,
        repository: AgentRepository,
        session: dict[str, Any],
        source_run: dict[str, Any],
        content: str,
        now: str,
        *,
        prior_messages: list[dict[str, Any]] | None,
        event_sink: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """直接绑定终态结果并执行只读 CSV 追问循环。"""
        service = ResultInquiryService(platform_store)
        catalog, artifacts, available_results, global_context = self._global_inquiry_context(
            repository, source_run, service,
        )
        definition = workflow_definition('RESULT_INQUIRY')
        frozen = freeze_workflow(definition)
        run = {
            'runId': gen_id('agr'),
            'sessionId': session['sessionId'],
            'goal': content,
            'taskType': 'INQUIRY',
            'status': 'PLANNING',
            'currentStage': 'QUERY',
            'runtimeMode': 'WORKFLOW_HARNESS',
            **frozen,
            'currentStep': 'QUERY',
            'completedSteps': ['LOCATE_RESULT', 'INSPECT_CATALOG'],
            'stepAttempt': 1,
            'activeToolCallId': None,
            'sourceRunId': source_run['runId'],
            'resultMetadata': self._result_metadata(source_run),
            'availableResultRunIds': [item['runId'] for item in available_results],
            'artifactIds': [],
            'jobId': None,
            'createdAt': now,
            'updatedAt': now,
        }
        repository.save_run(run)
        if event_sink is not None:
            event_sink({'type': 'run', 'run': self._stream_run_snapshot(run)})
        workflow_state = self._workflow_state_from_run(run)
        history = prior_messages
        if history is None:
            history = self._history_for_harness_turn(
                repository,
                session['sessionId'],
                workflow_state,
                content,
            )
        transcript = list(history)
        turn_context = {
            'resultInquiryContext': {
                'sourceRunId': source_run['runId'],
                'catalog': catalog,
                'registeredArtifacts': artifacts,
                **global_context,
            },
        }
        tools = InquiryTools(service=service)
        query_results: list[dict[str, Any]] = []
        normalized_question = ''.join(str(content).lower().split())
        topsis_requested = 'topsis' in normalized_question or any(
            marker in normalized_question for marker in ('前十项', '前10项', 'top10', 'top十')
        )
        # 只有全局目录恰好存在一份优化摘要时才预读；多份摘要必须交给模型
        # 结合 availableResults/catalogsByRunId 选择，避免把最近结果误当目标结果。
        topsis_candidates: list[tuple[str, str]] = []
        catalogs_by_run_id = global_context.get('catalogsByRunId') or {}
        for owner_run_id, scoped_context in catalogs_by_run_id.items():
            if not isinstance(scoped_context, dict):
                continue
            candidate_catalog = scoped_context.get('catalog')
            topsis_info = candidate_catalog.get('topsis') if isinstance(candidate_catalog, dict) else None
            if not isinstance(topsis_info, dict):
                continue
            artifact_name = str(topsis_info.get('artifact') or '')
            binding = (scoped_context.get('artifactBindings') or {}).get(artifact_name)
            artifact_id = binding.get('artifactId') if isinstance(binding, dict) else None
            if artifact_id and artifact_id in set(artifacts.values()):
                topsis_candidates.append((str(owner_run_id), str(artifact_id)))

        def complete_single_topsis_fallback() -> dict[str, Any] | None:
            """仅在唯一登记优化摘要时补齐模型漏发的 TOPSIS 工具调用。"""
            if not topsis_requested or len(topsis_candidates) != 1:
                return None
            _owner_run_id, topsis_artifact = topsis_candidates[0]
            arguments = {
                'artifact_id': topsis_artifact,
                'limit': 10,
            }
            WorkflowGuard().authorize(
                workflow_snapshot=run['workflowSnapshot'],
                current_step='QUERY',
                completed_steps=run['completedSteps'],
                repeated_no_progress=0,
                tool_call=WorkflowToolCall(name='result.topsis', arguments=arguments),
            )
            effective_arguments = _CAPABILITY_DISPATCHER.authorize_and_validate(
                'result.topsis',
                arguments,
                allowed_capabilities=self._workflow_state_from_run(run)['allowedTools'],
            ).model_dump(by_alias=True, mode='json')
            output = tools.call('result.topsis', arguments).model_dump(by_alias=True, mode='json')
            fallback_query = {
                'tool': 'result.topsis',
                'arguments': arguments,
                'effectiveArguments': effective_arguments,
                'output': output,
            }
            fallback_call = HarnessToolCall(
                toolCallId=f'{run["runId"]}:topsis-fallback',
                name='result.topsis',
                arguments=arguments,
            )
            self._record_read_only_tool_call(
                repository,
                run_id=run['runId'],
                call_id=fallback_call.tool_call_id,
                name=fallback_call.name,
                arguments=arguments,
                effective_arguments=effective_arguments,
                output=output,
                cached_tokens=None,
            )
            self._persist_native_tool_exchange(
                repository,
                session_id=session['sessionId'],
                run_id=run['runId'],
                call=fallback_call,
                assistant_content='',
                tool_result={
                    'effectiveArguments': effective_arguments,
                    'result': output,
                    'workflowState': self._workflow_state_from_run(run),
                },
            )
            return self._complete_native_inquiry(
                repository,
                session,
                run,
                catalog,
                [fallback_query],
                answer=self._deterministic_inquiry_answer([fallback_query]),
                narrative_mode='DETERMINISTIC',
                cached_tokens=None,
            )

        previous_signature: str | None = None
        repeated_no_progress = 0
        correction_count = 0
        for _round in range(10):
            try:
                turn = self.planner.run_harness_turn(
                    messages=transcript,
                    user_content=content,
                    workflow_state=self._workflow_state_from_run(run),
                    tools=harness_step_tool_catalog(
                        self._workflow_state_from_run(run)['allowedTools'],
                    ),
                    turn_context=turn_context,
                )
            except LLMUnavailableError:
                # 模型负责选择历史结果和查询工具；工具已经成功返回后，模型只负责
                # 叙述，叙述超时不能让已取得的确定性 TOPSIS/指标结果整体消失。
                if (
                    self._structured_inquiry_metrics(query_results)
                    or self._structured_inquiry_topsis(query_results)
                ):
                    return self._complete_native_inquiry(
                        repository,
                        session,
                        run,
                        catalog,
                        query_results,
                        answer=self._deterministic_inquiry_answer(query_results),
                        narrative_mode='DETERMINISTIC',
                        cached_tokens=None,
                    )
                fallback = complete_single_topsis_fallback()
                if fallback is not None:
                    return fallback
                raise
            if not turn.tool_calls:
                answer = str(turn.content or '').strip()
                narrative_mode = 'LLM'
                if not answer:
                    return self._fail_native_inquiry(
                        repository, session, run, 'HARNESS_EMPTY_RESPONSE', '模型没有给出追问答案。',
                    )
                if not query_results:
                    fallback = complete_single_topsis_fallback()
                    if fallback is not None:
                        return fallback
                    return self._fail_native_inquiry(
                        repository, session, run, 'INQUIRY_EVIDENCE_REQUIRED', '回答前必须查询至少一项已登记结果。',
                    )
                grounding_source = {'question': content, 'catalog': catalog, 'queries': query_results}
                if not self._numbers_are_grounded(answer, grounding_source):
                    if correction_count >= 1:
                        answer = self._deterministic_inquiry_answer(query_results)
                        narrative_mode = 'DETERMINISTIC'
                    else:
                        correction_count += 1
                        transcript.append({
                            'role': 'user',
                            'content': '上一版回答包含证据中没有的数字。请仅使用已返回的工具结果重新回答。',
                        })
                        continue
                return self._complete_native_inquiry(
                    repository,
                    session,
                    run,
                    catalog,
                    query_results,
                    answer=answer,
                    narrative_mode=narrative_mode,
                    cached_tokens=turn.cached_tokens,
                )

            assistant_calls = []
            for call in turn.tool_calls:
                signature = json.dumps(
                    {'name': call.name, 'arguments': call.arguments},
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(',', ':'),
                )
                repeated_no_progress = repeated_no_progress + 1 if signature == previous_signature else 0
                previous_signature = signature
                try:
                    capability = _CAPABILITY_REGISTRY.require(call.name)
                    validated = _CAPABILITY_DISPATCHER.authorize_and_validate(
                        call.name,
                        call.arguments,
                        allowed_capabilities=self._workflow_state_from_run(run)['allowedTools'],
                    )
                    effective_arguments = validated.model_dump(by_alias=True, mode='json')
                    WorkflowGuard().authorize(
                        workflow_snapshot=run['workflowSnapshot'],
                        current_step='QUERY',
                        completed_steps=run['completedSteps'],
                        repeated_no_progress=repeated_no_progress,
                        tool_call=WorkflowToolCall(
                            name=call.name,
                            arguments=effective_arguments,
                            risk=capability.risk,
                        ),
                    )
                    if call.name == 'result.compare_runs':
                        try:
                            comparison = cross_run_comparison_service.compare(
                                repository=repository,
                                session_id=session['sessionId'],
                                targets=effective_arguments['targets'],
                                baseline_run_id=effective_arguments.get('baselineRunId'),
                                metric_ids=effective_arguments.get('metricIds') or None,
                                owner=str(session.get('ownerId') or 'local'),
                                catalog_loader=self._load_result_catalog,
                            )
                            output = ResultCompareRunsOutput.model_validate(comparison).model_dump(
                                by_alias=True,
                                mode='json',
                            )
                        except (RunComparisonError, ValidationError) as exc:
                            raise ToolExecutionError(
                                'RUN_COMPARISON_INVALID',
                                str(exc),
                            ) from exc
                    else:
                        artifact_id = call.arguments.get('artifact_id') or call.arguments.get('artifactId')
                        if artifact_id not in set(artifacts.values()):
                            raise ToolExecutionError(
                                'ARTIFACT_NOT_REGISTERED',
                                '结果追问只能读取当前结果目录登记的只读制品。',
                            )
                        output = tools.call(call.name, effective_arguments).model_dump(by_alias=True, mode='json')
                except ToolExecutionError as exc:
                    return self._fail_native_inquiry(
                        repository,
                        session,
                        run,
                        exc.code,
                        exc.message,
                    )
                query_result = {
                    'tool': call.name,
                    'arguments': call.arguments,
                    'effectiveArguments': effective_arguments,
                    'output': output,
                }
                query_results.append(query_result)
                inquiry_metrics = self._structured_inquiry_metrics(query_results)
                inquiry_topsis = self._structured_inquiry_topsis(query_results)
                inquiry_topsis_weights = self._structured_inquiry_topsis_weights(query_results)
                inquiry_run_comparison = self._structured_inquiry_run_comparison(query_results)
                run.update({
                    'resultSummary': {
                        'inquiryMetrics': inquiry_metrics,
                        'inquiryTopsis': inquiry_topsis,
                        **({'inquiryTopsisWeights': inquiry_topsis_weights} if inquiry_topsis_weights else {}),
                        **({'inquiryRunComparison': inquiry_run_comparison} if inquiry_run_comparison else {}),
                        'queryProgress': {
                            'completed': len(inquiry_metrics) + len(inquiry_topsis) + (1 if inquiry_run_comparison else 0),
                            'message': (
                                f'已读取 {len(inquiry_metrics)} 项结果指标'
                                if inquiry_metrics
                                else '正在核对可查询指标…'
                            ),
                        },
                    },
                    'updatedAt': utc_now(),
                })
                repository.save_run(run)
                self._record_read_only_tool_call(
                    repository,
                    run_id=run['runId'],
                    call_id=call.tool_call_id,
                    name=call.name,
                    arguments=call.arguments,
                    effective_arguments=effective_arguments,
                    output=output,
                    cached_tokens=turn.cached_tokens,
                )
                self._persist_native_tool_exchange(
                    repository,
                    session_id=session['sessionId'],
                    run_id=run['runId'],
                    call=call,
                    assistant_content=turn.content,
                    tool_result={
                        'effectiveArguments': effective_arguments,
                        'result': output,
                        'workflowState': self._workflow_state_from_run(run),
                    },
                )
                assistant_calls.append({
                    'id': call.tool_call_id,
                    'type': 'function',
                    'function': {
                        'name': call.name,
                        'arguments': json.dumps(call.arguments, ensure_ascii=False),
                    },
                })
                transcript.append({
                    'role': 'tool',
                    'tool_call_id': call.tool_call_id,
                    'content': json.dumps({
                        'effectiveArguments': effective_arguments,
                        'result': output,
                        'workflowState': self._workflow_state_from_run(run),
                    }, ensure_ascii=False),
                })
                if event_sink is not None:
                    event_sink({'type': 'progress', 'run': self._stream_run_snapshot(run)})
            transcript.insert(
                len(transcript) - len(assistant_calls),
                {'role': 'assistant', 'content': turn.content, 'tool_calls': assistant_calls},
            )
        return self._fail_native_inquiry(
            repository, session, run, 'HARNESS_LOOP_DETECTED', '追问工具循环超过受控轮数。',
        )

    @staticmethod
    def _workflow_state_from_run(run: dict[str, Any]) -> dict[str, Any]:
        snapshot = run.get('workflowSnapshot') or {}
        step = next(
            (item for item in snapshot.get('steps', []) if item.get('stepId') == run.get('currentStep')),
            None,
        )
        if step is None:
            return {
                'workflowId': run.get('workflowId'),
                'currentStep': run.get('currentStep'),
                'completedSteps': run.get('completedSteps') or [],
                'allowedTools': [],
                'requiredGate': 'workflow.cursor.valid == true',
                'remainingRetries': 0,
                'error': {
                    'code': 'WORKFLOW_STEP_NOT_FOUND',
                    'message': '持久化流程游标不在冻结快照中。',
                },
            }
        return {
            'workflowId': run.get('workflowId'),
            'currentStep': run.get('currentStep'),
            'completedSteps': run.get('completedSteps') or [],
            'allowedTools': step['allowedTools'],
            'requiredGate': step['successGate'],
            'remainingRetries': max(0, int(step['retryPolicy']['maxAttempts']) - int(run.get('stepAttempt') or 1)),
        }

    @staticmethod
    def _native_history_message(item: dict[str, Any]) -> dict[str, Any]:
        """将存储消息投影成原生消息，保留 tool_calls/tool_call_id 等协议字段。"""
        if str(item.get('role') or '').upper() == 'USER':
            # 老会话没有 harnessContent；必须回退原始文本，不能投影出 content=None。
            content = item.get('harnessContent') or item.get('content')
        else:
            content = item.get('content')
        if str(item.get('role') or '').upper() == 'TOOL' and isinstance(content, str):
            # 历史回放使用审计所需的紧凑结果，避免完整 CSV/错误堆栈随会话无限增长。
            try:
                parsed = json.loads(content)
                compact = json.dumps(parsed, ensure_ascii=False, separators=(',', ':'))
                if len(compact) > WorkflowHarnessMixin._HARNESS_TOOL_RESULT_CHAR_LIMIT:
                    compact_payload: dict[str, Any] = {'summary': '工具结果已按历史预算压缩。'}
                    if isinstance(parsed, dict):
                        if parsed.get('workflowState') is not None:
                            compact_payload['workflowState'] = parsed['workflowState']
                        if parsed.get('error') is not None:
                            compact_payload['error'] = parsed['error']
                        if parsed.get('result') is not None:
                            compact_payload['resultPreview'] = json.dumps(
                                parsed['result'], ensure_ascii=False, separators=(',', ':')
                            )[:1000]
                    compact = json.dumps(compact_payload, ensure_ascii=False, separators=(',', ':'))
                content = compact
            except (TypeError, ValueError, json.JSONDecodeError):
                content = content[:WorkflowHarnessMixin._HARNESS_TOOL_RESULT_CHAR_LIMIT]
        message: dict[str, Any] = {
            'role': str(item.get('role') or '').lower(),
            'content': content,
        }
        for key in ('tool_calls', 'tool_call_id', 'name'):
            if key in item and item[key] is not None:
                message[key] = item[key]
        return message

    @staticmethod
    def _harness_user_content(
        workflow_state: dict[str, Any],
        content: str,
        *,
        turn_context: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> str:
        return json.dumps(
            build_runtime_turn_payload(
                workflow_state=workflow_state,
                user_content=content,
                tools=list(tools or []),
                turn_context=turn_context,
            ),
            ensure_ascii=False,
            separators=(',', ':'),
        )

    def _persist_harness_runtime_snapshot(
        self,
        repository: AgentRepository,
        session_id: str,
        *,
        workflow_state: dict[str, Any],
        content: str,
        tools: list[dict[str, Any]],
        turn_context: dict[str, Any] | None = None,
    ) -> None:
        messages = repository.list_messages(session_id)
        if not messages or str(messages[-1].get('role') or '').upper() != 'USER':
            return
        messages[-1]['harnessContent'] = self._harness_user_content(
            workflow_state,
            content,
            turn_context=turn_context,
            tools=tools,
        )
        save_message = getattr(repository, 'save_message', None)
        if callable(save_message):
            save_message(messages[-1])

    @staticmethod
    def _native_history_blocks(projected: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
        """把 assistant.tool_calls 及其 tool 结果合成不可分割块。

        原生协议要求每条 role='tool' 必须紧跟声明同 tool_call_id 的 assistant；
        按单条消息裁剪会切出孤立 tool 消息，被兼容端点直接拒绝。
        """
        blocks: list[list[dict[str, Any]]] = []
        index = 0
        while index < len(projected):
            message = projected[index]
            block = [message]
            index += 1
            pending = {
                str(call.get('id'))
                for call in (message.get('tool_calls') or [])
                if call.get('id')
            }
            while pending and index < len(projected):
                follower = projected[index]
                if follower.get('role') != 'tool' or str(follower.get('tool_call_id')) not in pending:
                    break
                pending.discard(str(follower.get('tool_call_id')))
                block.append(follower)
                index += 1
            blocks.append(block)
        return blocks

    @classmethod
    def _bounded_harness_history(
        cls,
        items: list[dict[str, Any]],
        char_budget: int | None = None,
    ) -> list[dict[str, Any]]:
        """按字符预算保留最新原生消息，老工具结果已在投影时压缩。"""
        budget = cls._HARNESS_HISTORY_CHAR_BUDGET if char_budget is None else int(char_budget)
        projected = [cls._native_history_message(item) for item in items]
        blocks = cls._native_history_blocks(projected)
        total = 0
        kept_blocks: list[list[dict[str, Any]]] = []
        for block in reversed(blocks):
            size = sum(
                len(json.dumps(message, ensure_ascii=False, separators=(',', ':')))
                for message in block
            )
            if kept_blocks and total + size > budget:
                break
            kept_blocks.append(block)
            total += size
        kept_blocks.reverse()
        kept = [message for block in kept_blocks for message in block]
        # 首块可能是被上一轮裁剪切断的孤立 tool 消息，原生协议不接受。
        while kept and kept[0].get('role') == 'tool':
            kept.pop(0)
        if len(kept) != len(projected):
            kept.insert(0, {
                'role': 'user',
                'content': json.dumps({
                    'historyProjectionNote': '较早的工具结果已按历史预算省略；如需数字，请重新查询已登记制品。',
                }, ensure_ascii=False, separators=(',', ':')),
            })
        return kept

    @staticmethod
    def _persist_native_tool_exchange(
        repository: AgentRepository,
        *,
        session_id: str,
        run_id: str,
        call: HarnessToolCall,
        assistant_content: str | None,
        tool_result: dict[str, Any],
    ) -> None:
        """把模型的原生 assistant/tool 对保留到消息表，跨请求恢复时不降级成文本。"""
        assistant_message = {
            'messageId': gen_id('msg'),
            'sessionId': session_id,
            'role': 'ASSISTANT',
            'content': assistant_content,
            'runId': run_id,
            'messageType': 'HARNESS_TOOL_CALL',
            'tool_calls': [{
                'id': call.tool_call_id,
                'type': 'function',
                'function': {
                    'name': call.name,
                    'arguments': json.dumps(call.arguments, ensure_ascii=False),
                },
            }],
            'createdAt': utc_now(),
        }
        tool_message = {
            'messageId': gen_id('msg'),
            'sessionId': session_id,
            'role': 'TOOL',
            'content': json.dumps(tool_result, ensure_ascii=False),
            'runId': run_id,
            'messageType': 'HARNESS_TOOL_RESULT',
            'tool_call_id': call.tool_call_id,
            'name': call.name,
            'createdAt': utc_now(),
        }
        repository.add_message(assistant_message)
        repository.add_message(tool_message)

    def _fail_native_inquiry(
        self,
        repository: AgentRepository,
        session: dict[str, Any],
        run: dict[str, Any],
        code: str,
        message: str,
    ) -> dict[str, Any]:
        visible = f'结果追问未完成：{message}'
        run.update({
            'status': 'FAILED',
            'currentStage': 'FAILED',
            'currentStep': 'FAILED',
            'resultSummary': {'code': code, 'message': visible},
            'updatedAt': utc_now(),
        })
        repository.save_run(run)
        repository.add_message({
            'messageId': gen_id('msg'),
            'sessionId': session['sessionId'],
            'role': 'ASSISTANT',
            'content': visible,
            'runId': run['runId'],
            'createdAt': utc_now(),
        })
        session['updatedAt'] = utc_now()
        repository.save_session(session)
        return self._decorate_run(run)

    def _complete_native_inquiry(
        self,
        repository: AgentRepository,
        session: dict[str, Any],
        run: dict[str, Any],
        catalog: dict[str, Any],
        query_results: list[dict[str, Any]],
        *,
        answer: str,
        narrative_mode: str,
        cached_tokens: int | None,
    ) -> dict[str, Any]:
        """保存已完成的只读查询；模型叙述失败时仍保留工具确定性结果。"""
        inquiry_metrics = self._structured_inquiry_metrics(query_results)
        inquiry_topsis = self._structured_inquiry_topsis(query_results)
        inquiry_topsis_weights = self._structured_inquiry_topsis_weights(query_results)
        inquiry_run_comparison = self._structured_inquiry_run_comparison(query_results)
        visible_message = (
            self._deterministic_inquiry_answer(query_results)
            if inquiry_metrics or inquiry_topsis or inquiry_run_comparison
            else answer
        )
        run.update({
            'status': 'SUCCEEDED',
            'currentStage': 'COMPLETED',
            'currentStep': 'COMPLETED',
            'completedSteps': [
                'LOCATE_RESULT', 'INSPECT_CATALOG', 'QUERY', 'EVIDENCE_REVIEW', 'REPORT',
            ],
            'inquiryFacts': {'queries': query_results, 'catalog': catalog},
            'resultSummary': {
                'message': visible_message,
                'narrativeSummary': answer,
                'narrativeMode': narrative_mode,
                'cachedTokens': cached_tokens,
                'inquiryMetrics': inquiry_metrics,
                'inquiryTopsis': inquiry_topsis,
                **({'inquiryTopsisWeights': inquiry_topsis_weights} if inquiry_topsis_weights else {}),
                **({'inquiryRunComparison': inquiry_run_comparison} if inquiry_run_comparison else {}),
                'queryProgress': {
                    'completed': len(inquiry_metrics) + len(inquiry_topsis) + (1 if inquiry_run_comparison else 0),
                    'message': f'已读取 {len(inquiry_metrics) + len(inquiry_topsis)} 项结果指标',
                },
            },
            'updatedAt': utc_now(),
        })
        repository.save_run(run)
        repository.add_message({
            'messageId': gen_id('msg'),
            'sessionId': session['sessionId'],
            'role': 'ASSISTANT',
            'content': visible_message,
            'runId': run['runId'],
            'createdAt': utc_now(),
        })
        session['updatedAt'] = utc_now()
        repository.save_session(session)
        return self._decorate_run(run)

    @staticmethod
    def _numbers_are_grounded(text: str, evidence: Any) -> bool:
        return numbers_are_grounded(text, evidence if isinstance(evidence, dict) else {'evidence': evidence})

    @staticmethod
    def _deterministic_inquiry_answer(query_results: list[dict[str, Any]]) -> str:
        """模型叙述未通过数字门时，只返回简短且不暴露内部校验的说明。"""
        comparison = WorkflowHarnessMixin._structured_inquiry_run_comparison(query_results)
        if comparison:
            return (
                f'已完成 {len(comparison.get("runs") or [])} 个工程对象的跨 Run 比较；'
                f'可比性为 {comparison.get("compatibility")}，数值均来自已登记工程证据。'
            )
        topsis = WorkflowHarnessMixin._structured_inquiry_topsis(query_results)
        if topsis:
            return f'已读取 TOPSIS 前 {len(topsis)} 项候选，排名和数值均来自已登记的优化摘要。'
        count = len(WorkflowHarnessMixin._structured_inquiry_metrics(query_results))
        return f'已读取 {count} 项结果指标，数值均来自已登记的只读结果文件。'

    @staticmethod
    def _structured_inquiry_run_comparison(query_results: list[dict[str, Any]]) -> dict[str, Any] | None:
        for item in reversed(query_results):
            if item.get('tool') == 'result.compare_runs' and isinstance(item.get('output'), dict):
                return dict(item['output'])
        return None

    @staticmethod
    def _structured_inquiry_topsis(query_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """把 TOPSIS 只读结果投影为前端可展示的排名表。"""
        rows: list[dict[str, Any]] = []
        for item in query_results:
            if item.get('tool') != 'result.topsis':
                continue
            output = item.get('output') or {}
            for row in output.get('rows') or []:
                if isinstance(row, dict):
                    rows.append(dict(row))
        return rows

    @staticmethod
    def _structured_inquiry_topsis_weights(query_results: list[dict[str, Any]]) -> dict[str, Any] | None:
        """保留 TOPSIS 的指标名称和决策权重，供追问卡片解释排名依据。"""
        for item in query_results:
            if item.get('tool') != 'result.topsis':
                continue
            output = item.get('output') or {}
            names = output.get('objectiveNames') or output.get('objective_names') or []
            weights = output.get('weights') or []
            if not isinstance(names, list) or not isinstance(weights, list) or not names or not weights:
                continue
            return {
                'objectiveNames': [str(name).split(':', 1)[-1] for name in names],
                'weights': [float(weight) for weight in weights],
            }
        return None

    @staticmethod
    def _structured_inquiry_metrics(query_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """把 peak 工具原值投影为前端可直接展示的工程指标。"""
        label_overrides = {
            'max_girder_end_displacement': '最大梁端位移',
            'max_acceleration': '最大加速度',
            'max_tower_base_shear': '最大塔底剪力',
            'max_tower_base_moment': '最大塔底弯矩',
            'max_damper_force': '最大阻尼器力',
            'max_damper_stroke': '最大阻尼器行程',
            'dissipated_energy': '阻尼器耗能',
            'cumulative_displacement': '累计位移',
        }
        metrics: list[dict[str, Any]] = []
        for item in query_results:
            if item.get('tool') != 'result.peak':
                continue
            arguments = item.get('effectiveArguments') or item.get('arguments') or {}
            output = item.get('output') or {}
            column = str(output.get('column') or arguments.get('column') or '')
            metric_id = next(
                (
                    response_id
                    for response_id, spec in RESPONSE_METRIC_SPECS.items()
                    if column in RESPONSE_COLUMN_ALIASES.get(str(spec.get('semantic') or ''), ())
                ),
                column,
            )
            spec = RESPONSE_METRIC_SPECS.get(metric_id) or {}
            metrics.append({
                'metricId': metric_id,
                'label': label_overrides.get(metric_id, str(spec.get('label') or column)),
                'sourceColumn': column,
                'peakAbsolute': output.get('peakAbsolute'),
                'peakSigned': output.get('peakSigned'),
                'unit': str(spec.get('unit') or ''),
                'peakTimeS': output.get('peakTime'),
                'sampleCount': output.get('sampleCount'),
            })
        return metrics

    def _stream_run_snapshot(self, run: dict[str, Any]) -> dict[str, Any]:
        """冻结流事件快照，避免后续 run 原地更新污染已排队事件。"""
        decorated = self._decorate_run(run)
        return json.loads(json.dumps(decorated, ensure_ascii=False))

    @staticmethod
    def _record_read_only_tool_call(
        repository: AgentRepository,
        *,
        run_id: str,
        call_id: str,
        name: str,
        arguments: dict[str, Any],
        effective_arguments: dict[str, Any],
        output: dict[str, Any],
        cached_tokens: int | None,
        step_id: str = 'QUERY',
    ) -> None:
        canonical = json.dumps(arguments, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        effective_canonical = json.dumps(
            effective_arguments,
            ensure_ascii=False,
            sort_keys=True,
            separators=(',', ':'),
        )
        repository.save_tool_call({
            'toolCallId': call_id,
            'runId': run_id,
            'stepId': step_id,
            'toolName': name,
            'toolVersion': '1.0.0',
            'arguments': arguments,
            'argumentsSha256': sha256(canonical.encode('utf-8')).hexdigest(),
            'effectiveArguments': effective_arguments,
            'effectiveArgumentsSha256': sha256(effective_canonical.encode('utf-8')).hexdigest(),
            'risk': 'READ_ONLY',
            'authorized': True,
            'executionSource': 'WORKFLOW_HARNESS',
            'status': 'SUCCEEDED',
            'compactResult': output,
            'cachedTokens': cached_tokens,
            'updatedAt': utc_now(),
        })

    @staticmethod
    def _attach_workflow_runtime(
        repository: AgentRepository,
        run: dict[str, Any],
        task_type: str,
    ) -> None:
        # Historical persisted FULL runs may predate workflow snapshots.  Project them
        # onto the canonical optimization workflow at read/resume time only; FULL is
        # intentionally absent from WorkflowStartInput and the workflow registry.
        if task_type == 'INQUIRY':
            workflow_type = 'RESULT_INQUIRY'
        elif task_type == 'FULL_OPTIMIZATION':
            workflow_type = 'DAMPER_OPTIMIZATION'
        else:
            workflow_type = task_type
        frozen = freeze_workflow(workflow_definition(workflow_type))
        current_step, completed = _initial_runtime_cursor(run, frozen['workflowSnapshot'], workflow_type)
        run.update({
            'runtimeMode': 'WORKFLOW_HARNESS',
            **frozen,
            'currentStep': current_step,
            'completedSteps': completed,
            'stepAttempt': int(run.get('stepAttempt') or 1),
            'activeToolCallId': run.get('activeToolCallId'),
            'updatedAt': utc_now(),
        })
        # 完成路径（persistent loop 与否）在挂载时冻结一次；已冻结的 run 保持原值。
        if not isinstance(run.get('persistentLoop'), bool):
            run['persistentLoop'] = WorkflowHarnessMixin._persistent_loop_enabled()
        repository.save_run(run)

    @staticmethod
    def _sync_workflow_runtime(
        repository: AgentRepository,
        run: dict[str, Any],
    ) -> None:
        """只从已持久化游标派生展示状态，绝不反推或覆盖流程游标。"""
        if run.get('runtimeMode') != 'WORKFLOW_HARNESS' or not run.get('workflowSnapshot'):
            return
        step_ids = {item['stepId'] for item in run['workflowSnapshot'].get('steps', [])}
        current_step = str(run.get('currentStep') or '')
        if current_step not in step_ids:
            # 旧版本可能写入不存在的阶段；这是数据迁移，不是根据 status 反推流程。
            run.update({
                'currentStep': run['workflowSnapshot'].get('initialStep'),
                'completedSteps': [],
                'workflowCursorError': 'INVALID_CURSOR_MIGRATED_TO_INITIAL',
                'updatedAt': utc_now(),
            })
            derived_status = _status_from_workflow_cursor(run)
            if derived_status:
                run['status'] = derived_status
            repository.save_run(run)
            return
        derived_status = _status_from_workflow_cursor(run)
        if derived_status and run.get('status') != derived_status:
            run.update({'status': derived_status, 'updatedAt': utc_now()})
            repository.save_run(run)

    @staticmethod
    def _cancel_workflow_cursor(
        repository: AgentRepository,
        run: dict[str, Any],
    ) -> None:
        """把取消操作写入冻结游标，终止态不会被展示同步覆盖。"""
        if run.get('runtimeMode') != 'WORKFLOW_HARNESS' or not run.get('workflowSnapshot'):
            return
        guard = WorkflowGuard()
        current = str(run.get('currentStep') or '')
        completed = list(run.get('completedSteps') or [])
        try:
            current, completed = guard.advance(
                workflow_snapshot=run['workflowSnapshot'],
                current_step=current,
                completed_steps=completed,
                gate_passed=False,
                failure_code='CANCELLED',
            )
        except ToolExecutionError:
            current = 'CANCELLED'
        run.update({
            'currentStep': current,
            'completedSteps': list(dict.fromkeys(completed)),
            'status': 'CANCELLED',
            'currentStage': 'CANCELLED',
            'pendingApprovalId': None,
            'stepAttempt': 1,
            'updatedAt': utc_now(),
        })
        repository.save_run(run)

    def _create_harness_text_run(
        self,
        repository: AgentRepository,
        session: dict[str, Any],
        content: str,
        now: str,
        reply: str,
        cached_tokens: int | None,
    ) -> dict[str, Any]:
        run = {
            'runId': gen_id('agr'),
            'sessionId': session['sessionId'],
            'goal': content,
            'taskType': 'CONVERSATION',
            'status': 'SUCCEEDED',
            'currentStage': 'COMPLETED',
            'runtimeMode': 'WORKFLOW_HARNESS',
            'artifactIds': [],
            'jobId': None,
            'resultSummary': {'message': reply, 'cachedTokens': cached_tokens},
            'createdAt': now,
            'updatedAt': utc_now(),
        }
        repository.save_run(run)
        repository.add_message({
            'messageId': gen_id('msg'),
            'sessionId': session['sessionId'],
            'role': 'ASSISTANT',
            'content': reply,
            'runId': run['runId'],
            'createdAt': utc_now(),
        })
        session['updatedAt'] = utc_now()
        repository.save_session(session)
        return self._decorate_run(run)

    def _create_harness_failure_run(
        self,
        repository: AgentRepository,
        session: dict[str, Any],
        content: str,
        now: str,
        *,
        code: str,
        message: str,
        details: Any = None,
    ) -> dict[str, Any]:
        visible = f'工作流未执行：{message}'
        run = {
            'runId': gen_id('agr'),
            'sessionId': session['sessionId'],
            'goal': content,
            'taskType': 'WORKFLOW_HARNESS',
            'status': 'FAILED',
            'currentStage': 'HARNESS_GUARD',
            'runtimeMode': 'WORKFLOW_HARNESS',
            'artifactIds': [],
            'jobId': None,
            'resultSummary': {'code': code, 'message': visible, 'details': details},
            'createdAt': now,
            'updatedAt': utc_now(),
        }
        repository.save_run(run)
        repository.add_message({
            'messageId': gen_id('msg'),
            'sessionId': session['sessionId'],
            'role': 'ASSISTANT',
            'content': visible,
            'runId': run['runId'],
            'createdAt': utc_now(),
        })
        session['updatedAt'] = utc_now()
        repository.save_session(session)
        return self._decorate_run(run)

    @staticmethod
    def _authorize_approved_execution(
        repository: AgentRepository,
        run: dict[str, Any],
        *,
        idempotency_key: str,
        effective_arguments: dict[str, Any] | None = None,
    ) -> str | None:
        """在创建真实 Job 前推进审批门并授权当前工作流的求解工具。"""
        if run.get('runtimeMode') != 'WORKFLOW_HARNESS' or not run.get('workflowSnapshot'):
            return None
        task_type = str(run.get('taskType') or '')
        task_spec = engineering_task_spec(task_type)
        if task_spec is None:
            raise ToolExecutionError(
                'UNSUPPORTED_TASK_TYPE',
                f'未登记的工程任务类型: {task_type}',
            )
        tool_name = task_spec.solver_run_tool
        capability = _CAPABILITY_REGISTRY.require(tool_name)
        guard = WorkflowGuard()
        current_step = str(run.get('currentStep') or 'WAITING_APPROVAL')
        completed = list(run.get('completedSteps') or [])
        recorded_calls = repository.list_tool_calls(run['runId'])
        existing = next(
            (
                item for item in recorded_calls
                if item.get('idempotencyKey') == idempotency_key
                and item.get('status') in {'RUNNING', 'WAITING_JOB', 'SUCCEEDED'}
            ),
            None,
        )
        if existing:
            run['activeToolCallId'] = existing.get('toolCallId')
            repository.save_run(run)
            return existing.get('toolCallId')
        if current_step == 'WAITING_APPROVAL':
            current_step, completed = guard.advance(
                workflow_snapshot=run['workflowSnapshot'],
                current_step=current_step,
                completed_steps=completed,
                gate_passed=True,
                gate_context={'approval': {'status': 'APPROVED'}},
            )
        # attempt 按已登记的求解轨迹数推算；run.stepAttempt 会被审批重放重置，
        # 用它计数会让 maxAttempts=1 的求解步骤在失败后被无限重试。
        attempt = 1 + sum(
            1 for item in recorded_calls
            if item.get('stepId') == current_step and item.get('risk') == 'SOLVER_EXECUTION'
        )
        call_id = gen_id('call')
        arguments = {'runId': run['runId']}
        executed_arguments = effective_arguments or arguments
        frozen_action = executed_arguments.get('frozenAction')
        usage: dict[str, int | float] = {}
        if task_type == 'ANALYSIS':
            usage['realSolveCount'] = 1
        elif task_type in {'DAMPER_COMPARISON', 'DAMPER_PARAMETER_SWEEP'}:
            # 让 Guard 依据工作流 limits.caseCount 真正强制工况数上限，
            # 而不是只靠 Pydantic schema 和平台侧请求校验兜底。
            frozen_cases = (
                frozen_action.get('cases') if isinstance(frozen_action, dict) else None
            ) or []
            if frozen_cases:
                usage['caseCount'] = len(frozen_cases)
        elif task_type in {'DAMPER_OPTIMIZATION', 'FULL_OPTIMIZATION'}:
            frozen_budget = (
                frozen_action.get('budget')
                if isinstance(frozen_action, dict)
                else None
            ) or {}
            workflow_budget = (
                run.get('workflowContract')
                or (run.get('workflowSnapshot') or {}).get('limits')
                or {}
            )
            requested_doe_count = (
                frozen_budget.get('doeDesignCount')
                if 'doeDesignCount' in frozen_budget
                else (workflow_budget.get('budget') or {}).get('doeDesignCount')
                or workflow_budget.get('doeDesignCount')
            )
            if (
                isinstance(requested_doe_count, bool)
                or not isinstance(requested_doe_count, int)
                or not DOE_INITIAL_MIN <= requested_doe_count <= DOE_INITIAL_MAX
            ):
                raise ToolExecutionError(
                    'WORKFLOW_BUDGET_INVALID',
                    f'doeDesignCount 必须是 {DOE_INITIAL_MIN}–{DOE_INITIAL_MAX} 的整数',
                    details={
                        'field': 'doeDesignCount',
                        'value': requested_doe_count,
                        'minimum': DOE_INITIAL_MIN,
                        'maximum': DOE_INITIAL_MAX,
                    },
                )
            usage['doeDesignCount'] = requested_doe_count
        arguments = _CAPABILITY_DISPATCHER.authorize_and_validate(
            tool_name,
            arguments,
            allowed_capabilities=[tool_name],
            approved=True,
            idempotency_key=idempotency_key,
        ).model_dump(by_alias=True, mode='json')
        guard.authorize(
            workflow_snapshot=run['workflowSnapshot'],
            current_step=current_step,
            completed_steps=completed,
            step_attempt=attempt,
            tool_call=WorkflowToolCall(
                name=tool_name,
                arguments=arguments,
                risk=capability.risk,
                requiresApproval=capability.requires_approval,
                approved=capability.requires_approval,
                idempotencyKey=idempotency_key,
                usage=usage,
            ),
        )
        run.update({
            'currentStep': current_step,
            'completedSteps': completed,
            # attempt 属于当前步骤，由已登记轨迹推算；这里同步展示值，不作为计数依据。
            'stepAttempt': attempt,
            'activeToolCallId': call_id,
            'updatedAt': utc_now(),
        })
        repository.save_run(run)
        arguments_canonical = json.dumps(
            arguments,
            ensure_ascii=False,
            sort_keys=True,
            separators=(',', ':'),
        )
        effective_canonical = json.dumps(
            executed_arguments,
            ensure_ascii=False,
            sort_keys=True,
            separators=(',', ':'),
        )
        repository.save_tool_call({
            'toolCallId': call_id,
            'runId': run['runId'],
            'stepId': current_step,
            'toolName': tool_name,
            'toolVersion': '1.0.0',
            'arguments': arguments,
            'argumentsSha256': sha256(arguments_canonical.encode('utf-8')).hexdigest(),
            'effectiveArguments': executed_arguments,
            'effectiveArgumentsSha256': sha256(effective_canonical.encode('utf-8')).hexdigest(),
            **({
                'frozenActionSha256': sha256(json.dumps(
                    frozen_action,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(',', ':'),
                ).encode('utf-8')).hexdigest(),
            } if isinstance(frozen_action, dict) else {}),
            'risk': capability.risk.value,
            'approved': capability.requires_approval,
            'authorized': True,
            'idempotencyKey': idempotency_key,
            'idempotencyKeySource': capability.idempotency_key_source,
            'executionSource': 'WORKFLOW_HARNESS',
            'status': 'RUNNING',
            'updatedAt': utc_now(),
        })
        return call_id

    @staticmethod
    def _finish_execution_tool_call(
        repository: AgentRepository,
        run: dict[str, Any],
        *,
        call_id: str | None,
        job_id: str,
    ) -> None:
        if call_id is None:
            return
        existing = repository.get_tool_call(call_id) or {}
        repository.save_tool_call({
            **existing,
            'toolCallId': call_id,
            'runId': run['runId'],
            'jobId': job_id,
            'status': 'WAITING_JOB',
            'updatedAt': utc_now(),
        })
        run['activeToolCallId'] = None
        if WorkflowHarnessMixin._run_persistent_loop(run):
            WorkflowHarnessMixin._set_harness_loop_state(
                run,
                status='WAITING_EXTERNAL',
                wake_reason='JOB_CREATED',
                external_job_id=job_id,
                last_tool_call_id=call_id,
            )
            repository.save_run(run)

    @staticmethod
    def _job_gate_context(
        *,
        run: dict[str, Any],
        job_status: str,
        artifact_ids: list[str],
        tool_calls: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """从 Job 和已登记轨迹构造门禁上下文，不凭空给出全通过结论。

        只覆盖 Job 终态能证明的门禁（tool.ok / job.id / preflight / approval）。
        evidence.checked 与 report.persisted 由后续证据审查与报告注册步骤给出，
        Job 载荷里没有对应信号，这里不能替它们编造真值。
        """
        solver_call = next(
            (
                item for item in tool_calls
                if item.get('runId') == run.get('runId')
                and item.get('risk') == 'SOLVER_EXECUTION'
                and item.get('approved') is True
            ),
            None,
        )
        preflight = run.get('preflight') or {}
        return {
            'tool': {
                'ok': job_status == 'SUCCEEDED' and bool(run.get('jobId')) and bool(artifact_ids),
            },
            'preflight': {'passed': preflight.get('passed') is True},
            'approval': {'status': 'APPROVED' if solver_call else None},
            'job': {'id': run.get('jobId') if job_status == 'SUCCEEDED' else None},
        }

    @staticmethod
    def _python_stage_tool(step: dict[str, Any]) -> str | None:
        tools = list(step.get('allowedTools') or [])
        if 'workflow.complete' in tools:
            return 'workflow.complete'
        return str(tools[0]) if tools else None

    @staticmethod
    def _python_stage_arguments(name: str, run: dict[str, Any]) -> dict[str, Any]:
        """只按 Capability InputModel 构造服务端阶段参数；最终校验统一交给 Dispatcher。"""
        capability = _CAPABILITY_REGISTRY.require(name)
        fields = capability.input_model.model_fields
        arguments: dict[str, Any] = {}
        if 'run_id' in fields:
            arguments['runId'] = run['runId']
        if 'job_id' in fields:
            arguments['jobId'] = run['jobId']
        return arguments

    @staticmethod
    def _record_python_job_stage(
        repository: AgentRepository,
        run: dict[str, Any],
        *,
        step: dict[str, Any],
        completed_steps: list[str],
        compact_result: dict[str, Any],
    ) -> None:
        """按工具登记合同授权并保存 Python Job 内部阶段轨迹。"""
        name = WorkflowHarnessMixin._python_stage_tool(step)
        if name is None:
            return
        call_id = f'{run["runId"]}:job:{step["stepId"]}'
        capability = _CAPABILITY_REGISTRY.require(name)
        arguments = WorkflowHarnessMixin._python_stage_arguments(name, run)
        idempotency_key = (
            call_id if capability.idempotency_key_source == 'SERVER_DERIVED' else None
        )
        runtime_approval = (
            'WAITING_APPROVAL' in completed_steps
            or any(
                item.get('approved') is True and item.get('authorized') is True
                for item in repository.list_tool_calls(run['runId'])
            )
        )
        approved = runtime_approval if capability.requires_approval else False
        arguments = _CAPABILITY_DISPATCHER.authorize_and_validate(
            name,
            arguments,
            allowed_capabilities=[name],
            approved=approved,
            idempotency_key=idempotency_key,
        ).model_dump(by_alias=True, mode='json')
        WorkflowGuard().authorize(
            workflow_snapshot=run['workflowSnapshot'],
            current_step=str(step['stepId']),
            completed_steps=completed_steps,
            step_attempt=int(run.get('stepAttempt') or 1),
            tool_call=WorkflowToolCall(
                name=name,
                arguments=arguments,
                risk=capability.risk,
                requiresApproval=capability.requires_approval,
                approved=approved,
                idempotencyKey=idempotency_key,
            ),
        )
        canonical = json.dumps(
            arguments,
            ensure_ascii=False,
            sort_keys=True,
            separators=(',', ':'),
        )
        digest = sha256(canonical.encode('utf-8')).hexdigest()
        repository.save_tool_call({
            'toolCallId': call_id,
            'runId': run['runId'],
            'stepId': step['stepId'],
            'toolName': name,
            'toolVersion': '1.0.0',
            'arguments': arguments,
            'argumentsSha256': digest,
            'effectiveArguments': arguments,
            'effectiveArgumentsSha256': digest,
            'risk': capability.risk.value,
            'approved': approved,
            'authorized': True,
            'executionSource': 'PYTHON_JOB',
            'jobId': run.get('jobId'),
            **({'idempotencyKey': idempotency_key} if idempotency_key else {}),
            **(
                {'idempotencyKeySource': capability.idempotency_key_source}
                if capability.idempotency_key_source
                else {}
            ),
            'status': 'SUCCEEDED',
            'compactResult': compact_result,
            'updatedAt': utc_now(),
        })

    def _resume_persistent_job_stage(
        self,
        repository: AgentRepository,
        run: dict[str, Any],
        *,
        artifact_ids: list[str],
        job_payload: dict[str, Any] | None = None,
    ) -> bool:
        """让模型从当前冻结步骤选择一个工具，并只持久化推进一步。"""
        if not self._run_persistent_loop(run) or run.get('runtimeMode') != 'WORKFLOW_HARNESS':
            return False
        loop = run.get('harnessLoop') if isinstance(run.get('harnessLoop'), dict) else {}
        if loop.get('status') not in {'READY', 'RUNNING'}:
            return False
        snapshot = run.get('workflowSnapshot') or {}
        current = str(run.get('currentStep') or '')
        step = next(
            (item for item in snapshot.get('steps', []) if item.get('stepId') == current),
            None,
        )
        if step is None or current in set(snapshot.get('terminalSteps') or []):
            return False
        # 审查和报告需要各自的真实处理器；本切片只接管原子 Job 已产生证据的内部阶段。
        if current == 'REPORT':
            return False
        allowed_tools = list(step.get('allowedTools') or [])
        if not allowed_tools:
            return False

        try:
            model_failure_count = int(loop.get('modelFailureCount') or 0)
        except (TypeError, ValueError):
            model_failure_count = 0
        if loop.get('currentStep') not in {None, current}:
            model_failure_count = 0

        call_id = f'{run["runId"]}:loop:{current}'
        existing = repository.get_tool_call(call_id)
        gate_context = self._job_gate_context(
            run=run,
            job_status='SUCCEEDED',
            artifact_ids=artifact_ids,
            tool_calls=repository.list_tool_calls(run['runId']),
        )
        if existing and existing.get('status') == 'SUCCEEDED':
            existing_result = (
                existing.get('compactResult')
                if isinstance(existing.get('compactResult'), dict)
                else {}
            )
            is_review_step = current in {'EVIDENCE_REVIEW', 'REVIEW'}
            review_accepted = bool(existing_result.get('accepted')) if is_review_step else True
            next_step, completed = WorkflowGuard().advance(
                workflow_snapshot=snapshot,
                current_step=current,
                completed_steps=run.get('completedSteps') or [],
                gate_passed=review_accepted,
                failure_code=(
                    'EVIDENCE_FAILED'
                    if is_review_step and not review_accepted
                    else None
                ),
                gate_context=(
                    {
                        'evidence': {
                            'checked': review_accepted,
                            'accepted': review_accepted,
                        },
                        'tool': {'ok': review_accepted},
                    }
                    if is_review_step and review_accepted
                    else gate_context if not is_review_step else None
                ),
            )
            run.update({
                'currentStep': next_step,
                'completedSteps': list(dict.fromkeys(completed)),
                'stepAttempt': 1,
                'updatedAt': utc_now(),
                **(
                    {'pendingReviewOutcome': existing_result, 'status': 'REVIEWING'}
                    if is_review_step
                    else {}
                ),
            })
            self._set_harness_loop_state(
                run,
                status='READY',
                wake_reason='RECOVERED_COMMITTED_TOOL',
                external_job_id=str(run.get('jobId') or '') or None,
                last_tool_call_id=call_id,
            )
            repository.save_run(run)
            return True

        self._set_harness_loop_state(
            run,
            status='RUNNING',
            wake_reason='MODEL_TURN_STARTED',
            external_job_id=str(run.get('jobId') or '') or None,
            last_tool_call_id=call_id,
            model_failure_count=model_failure_count,
        )
        repository.save_run(run)
        workflow_state = self._workflow_state_from_run(run)
        workflow_state.update({
            'runId': run.get('runId'),
            'taskType': run.get('taskType'),
            'runStatus': run.get('status'),
            'jobId': run.get('jobId'),
            'executionOwner': 'LLM_PERSISTENT_LOOP',
        })
        loop_instruction = '继续执行当前已审批工作流；每轮只调用当前步骤的一个工具。'
        messages = self._contextual_harness_history(
            repository,
            str(run.get('sessionId') or ''),
            repository.list_messages(str(run.get('sessionId') or '')),
            workflow_state=workflow_state,
            query=loop_instruction,
        )
        is_review_step = current in {'EVIDENCE_REVIEW', 'REVIEW'}
        execution_source = 'LLM_PERSISTENT_LOOP'
        try:
            try:
                turn = self.planner.run_harness_turn(
                    messages=messages,
                    user_content=loop_instruction,
                    workflow_state=workflow_state,
                    tools=harness_step_tool_catalog(allowed_tools),
                )
            except LLMUnavailableError:
                # 冻结审查步骤只有一个既定的只读工具；仍经能力和工作流双重校验。
                if not is_review_step or len(allowed_tools) != 1:
                    raise
                call = HarnessToolCall(
                    toolCallId=f'{call_id}:review-fallback',
                    name=allowed_tools[0],
                    arguments={'runId': str(run['runId'])},
                )
                execution_source = 'PYTHON_REVIEW_FALLBACK'
            else:
                if len(turn.tool_calls) != 1:
                    raise ToolExecutionError(
                        'HARNESS_SINGLE_TOOL_REQUIRED',
                        '持久化循环每轮必须且只能调用一个当前步骤工具。',
                        details={'currentStep': current, 'allowedTools': allowed_tools},
                    )
                call = turn.tool_calls[0]
                if call.name not in allowed_tools:
                    raise ToolExecutionError(
                        'WORKFLOW_STEP_VIOLATION',
                        f'步骤 {current} 不允许调用工具 {call.name}',
                        details={'currentStep': current, 'allowedTools': allowed_tools},
                    )
            capability = _CAPABILITY_REGISTRY.require(call.name)
            runtime_approval = (
                'WAITING_APPROVAL' in (run.get('completedSteps') or [])
                or any(
                    item.get('approved') is True and item.get('authorized') is True
                    for item in repository.list_tool_calls(run['runId'])
                )
            )
            idempotency_key = (
                call_id if capability.idempotency_key_source == 'SERVER_DERIVED' else None
            )
            validated = _CAPABILITY_DISPATCHER.authorize_and_validate(
                call.name,
                call.arguments,
                allowed_capabilities=allowed_tools,
                approved=runtime_approval if capability.requires_approval else False,
                idempotency_key=idempotency_key,
            )
            effective_arguments = validated.model_dump(by_alias=True, mode='json')
            WorkflowGuard().authorize(
                workflow_snapshot=snapshot,
                current_step=current,
                completed_steps=run.get('completedSteps') or [],
                step_attempt=int(run.get('stepAttempt') or 1),
                tool_call=WorkflowToolCall(
                    name=call.name,
                    arguments=effective_arguments,
                    risk=capability.risk,
                    requiresApproval=capability.requires_approval,
                    approved=runtime_approval if capability.requires_approval else False,
                    idempotencyKey=idempotency_key,
                ),
            )

            def stage_handler(_payload: BaseModel) -> dict[str, Any]:
                if is_review_step:
                    if job_payload is None:
                        raise ToolExecutionError(
                            'JOB_RESULT_REQUIRED',
                            '证据审查需要真实 Job 结果。',
                        )
                    outcome = self._agent_for(str(run.get('taskType'))).review(
                        job_payload,
                        workflow_contract={
                            'taskType': run.get('taskType'),
                            **(run.get('workflowContract') or {}),
                        },
                    )
                    return {
                        'accepted': outcome.accepted,
                        'runStatus': outcome.run_status,
                        'evidenceMode': outcome.evidence_mode,
                        'checks': outcome.checks,
                        'message': outcome.message,
                        'extra': outcome.extra,
                    }
                return {
                    'source': 'platform_job',
                    'stage': current,
                    'artifactIds': artifact_ids,
                }

            output_model = HarnessReviewOutput if is_review_step else HarnessStageEvidenceOutput
            stage_output = output_model.model_validate(stage_handler(validated)).model_dump(
                by_alias=True,
                mode='json',
            )
        except (LLMUnavailableError, ValidationError, ToolExecutionError) as exc:
            model_failure_count += 1
            error = {
                'code': str(getattr(exc, 'code', 'PERSISTENT_LOOP_MODEL_ERROR')),
                'message': str(getattr(exc, 'message', exc)),
            }
            if model_failure_count >= self._PERSISTENT_LOOP_MAX_MODEL_FAILURES:
                run.update({
                    'status': 'FAILED',
                    'currentStage': 'FAILED',
                    'currentStep': 'FAILED',
                    'workflowGateError': {
                        'code': 'PERSISTENT_LOOP_RETRY_EXHAUSTED',
                        'message': '持久化工作流连续三次无法完成当前模型回合，运行已安全终止。',
                        'details': {
                            'failedStep': current,
                            'attempts': model_failure_count,
                        },
                    },
                    'updatedAt': utc_now(),
                })
                self._set_harness_loop_state(
                    run,
                    status='FAILED',
                    wake_reason='MODEL_TURN_EXHAUSTED',
                    external_job_id=str(run.get('jobId') or '') or None,
                    last_tool_call_id=call_id,
                    error=error,
                    model_failure_count=model_failure_count,
                )
                repository.save_run(run)
                return False
            self._set_harness_loop_state(
                run,
                status='READY',
                wake_reason='MODEL_TURN_RETRY',
                external_job_id=str(run.get('jobId') or '') or None,
                last_tool_call_id=call_id,
                error=error,
                model_failure_count=model_failure_count,
            )
            repository.save_run(run)
            return False

        arguments_canonical = json.dumps(
            call.arguments, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
        )
        effective_canonical = json.dumps(
            effective_arguments, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
        )
        repository.save_tool_call({
            'toolCallId': call_id,
            'modelToolCallId': (
                call.tool_call_id if execution_source == 'LLM_PERSISTENT_LOOP' else None
            ),
            'runId': run['runId'],
            'stepId': current,
            'toolName': call.name,
            'toolVersion': '1.0.0',
            'arguments': call.arguments,
            'argumentsSha256': sha256(arguments_canonical.encode('utf-8')).hexdigest(),
            'effectiveArguments': effective_arguments,
            'effectiveArgumentsSha256': sha256(effective_canonical.encode('utf-8')).hexdigest(),
            'risk': capability.risk.value,
            'approved': runtime_approval if capability.requires_approval else False,
            'authorized': True,
            'executionSource': execution_source,
            'jobId': run.get('jobId'),
            **({'idempotencyKey': idempotency_key} if idempotency_key else {}),
            **(
                {'idempotencyKeySource': capability.idempotency_key_source}
                if capability.idempotency_key_source
                else {}
            ),
            'status': 'SUCCEEDED',
            'compactResult': stage_output,
            'updatedAt': utc_now(),
        })
        is_review_step = current in {'EVIDENCE_REVIEW', 'REVIEW'}
        review_accepted = bool(stage_output.get('accepted')) if is_review_step else True
        if is_review_step:
            next_step, completed = WorkflowGuard().advance(
                workflow_snapshot=snapshot,
                current_step=current,
                completed_steps=run.get('completedSteps') or [],
                gate_passed=review_accepted,
                failure_code='EVIDENCE_FAILED' if not review_accepted else None,
                gate_context={
                    'evidence': {
                        'checked': review_accepted,
                        'accepted': review_accepted,
                    },
                    'tool': {'ok': review_accepted},
                } if review_accepted else None,
            )
        else:
            next_step, completed = WorkflowGuard().advance(
                workflow_snapshot=snapshot,
                current_step=current,
                completed_steps=run.get('completedSteps') or [],
                gate_passed=True,
                gate_context=gate_context,
            )
        run.update({
            'currentStep': next_step,
            'completedSteps': list(dict.fromkeys(completed)),
            'stepAttempt': 1,
            'updatedAt': utc_now(),
            **({'pendingReviewOutcome': stage_output, 'status': 'REVIEWING'} if is_review_step else {}),
        })
        self._set_harness_loop_state(
            run,
            status='READY',
            wake_reason='TOOL_SUCCEEDED',
            external_job_id=str(run.get('jobId') or '') or None,
            last_tool_call_id=call_id,
        )
        repository.save_run(run)
        return True

    @staticmethod
    def _complete_job_tool_calls(
        repository: AgentRepository,
        run: dict[str, Any],
        *,
        job_status: str,
        artifact_ids: list[str],
    ) -> None:
        """把等待中的执行轨迹收敛到 Job 的真实终态。"""
        if run.get('runtimeMode') != 'WORKFLOW_HARNESS' or not run.get('jobId'):
            return
        completed_waiting_call = False
        last_tool_call_id: str | None = None
        for item in repository.list_tool_calls(run['runId']):
            if item.get('jobId') != run['jobId'] or item.get('status') != 'WAITING_JOB':
                continue
            completed_waiting_call = True
            last_tool_call_id = str(item.get('toolCallId') or '') or None
            repository.save_tool_call({
                **item,
                'status': job_status,
                'artifactIds': artifact_ids,
                'updatedAt': utc_now(),
            })
        if run.get('workflowSnapshot'):
            guard = WorkflowGuard()
            current = str(run.get('currentStep') or '')
            completed = list(run.get('completedSteps') or [])
            if job_status in {'FAILED', 'CANCELLED'}:
                failure_code = 'JOB_FAILED' if job_status == 'FAILED' else 'CANCELLED'
                try:
                    current, completed = guard.advance(
                        workflow_snapshot=run['workflowSnapshot'],
                        current_step=current,
                        completed_steps=completed,
                        gate_passed=False,
                        failure_code=failure_code,
                    )
                except ToolExecutionError:
                    current = 'CANCELLED' if job_status == 'CANCELLED' else 'FAILED'
            elif job_status == 'SUCCEEDED':
                if WorkflowHarnessMixin._run_persistent_loop(run) and completed_waiting_call:
                    try:
                        current, completed = guard.advance(
                            workflow_snapshot=run['workflowSnapshot'],
                            current_step=current,
                            completed_steps=completed,
                            gate_passed=True,
                            gate_context=WorkflowHarnessMixin._job_gate_context(
                                run=run,
                                job_status=job_status,
                                artifact_ids=artifact_ids,
                                tool_calls=repository.list_tool_calls(run['runId']),
                            ),
                        )
                    except ToolExecutionError as exc:
                        run['workflowGateError'] = {
                            'code': exc.code,
                            'message': exc.message,
                            'details': exc.details,
                        }
                    else:
                        run['stepAttempt'] = 1
                        run.update({
                            'currentStep': current,
                            'completedSteps': list(dict.fromkeys(completed)),
                            'updatedAt': utc_now(),
                        })
                        WorkflowHarnessMixin._set_harness_loop_state(
                            run,
                            status='READY',
                            wake_reason='JOB_SUCCEEDED',
                            external_job_id=str(run.get('jobId') or '') or None,
                            last_tool_call_id=last_tool_call_id,
                        )
                        repository.save_run(run)
                        return
                target_spec = engineering_task_spec(str(run.get('taskType')))
                target = target_spec.post_job_target_step if target_spec else None
                steps = {item['stepId'] for item in run['workflowSnapshot'].get('steps', [])}
                while target in steps and current != target:
                    current_definition = next(
                        item for item in run['workflowSnapshot']['steps']
                        if item['stepId'] == current
                    )
                    existing_stage_call = next(
                        (
                            item for item in repository.list_tool_calls(run['runId'])
                            if item.get('stepId') == current and item.get('status') in {'RUNNING', 'WAITING_JOB', 'SUCCEEDED'}
                        ),
                        None,
                    )
                    if existing_stage_call is None and current_definition.get('allowedTools'):
                        WorkflowHarnessMixin._record_python_job_stage(
                            repository,
                            run,
                            step=current_definition,
                            completed_steps=completed,
                            compact_result={'source': 'platform_job', 'stage': current},
                        )
                        run['stepAttempt'] = int(run.get('stepAttempt') or 1) + 1
                    try:
                        next_step, completed = guard.advance(
                            workflow_snapshot=run['workflowSnapshot'],
                            current_step=current,
                            completed_steps=completed,
                            gate_passed=True,
                            gate_context=WorkflowHarnessMixin._job_gate_context(
                                run=run,
                                job_status=job_status,
                                artifact_ids=artifact_ids,
                                tool_calls=repository.list_tool_calls(run['runId']),
                            ),
                        )
                    except ToolExecutionError as exc:
                        run['workflowGateError'] = {
                            'code': exc.code,
                            'message': exc.message,
                            'details': exc.details,
                        }
                        break
                    if next_step == current:
                        break
                    current = next_step
                    run['stepAttempt'] = 1
            run.update({
                'currentStep': current,
                'completedSteps': list(dict.fromkeys(completed)),
                'updatedAt': utc_now(),
            })
            if WorkflowHarnessMixin._run_persistent_loop(run) and job_status in {'FAILED', 'CANCELLED'}:
                WorkflowHarnessMixin._set_harness_loop_state(
                    run,
                    status='FAILED',
                    wake_reason=job_status,
                    external_job_id=str(run.get('jobId') or '') or None,
                    last_tool_call_id=last_tool_call_id,
                )
            repository.save_run(run)

    @staticmethod
    def _advance_workflow_terminal(
        repository: AgentRepository,
        run: dict[str, Any],
        *,
        evidence_accepted: bool,
        report_persisted: bool,
    ) -> None:
        """用真实证据和报告门禁推进终态，不补写未完成步骤。"""
        if run.get('runtimeMode') != 'WORKFLOW_HARNESS' or not run.get('workflowSnapshot'):
            return
        status = str(run.get('status') or '')
        if status not in {'SUCCEEDED', 'COMPLETED_DIAGNOSTIC', 'FAILED', 'CANCELLED'}:
            return
        snapshot = run['workflowSnapshot']
        terminal_steps = set(snapshot.get('terminalSteps') or [])
        if status in {'FAILED', 'CANCELLED'}:
            if status in terminal_steps:
                run.update({'currentStep': status, 'updatedAt': utc_now()})
                repository.save_run(run)
            return
        steps = {item['stepId']: item for item in snapshot.get('steps', [])}
        current = str(run.get('currentStep') or '')
        completed = list(run.get('completedSteps') or [])
        guard = WorkflowGuard()
        for _ in range(len(steps) + 1):
            if current in terminal_steps:
                break
            step = steps.get(current)
            if step is None:
                run['workflowGateError'] = {
                    'code': 'WORKFLOW_STEP_NOT_FOUND',
                    'message': f'冻结工作流不包含步骤 {current}',
                    'details': {'currentStep': current},
                }
                break
            is_evidence_step = current in {'EVIDENCE_REVIEW', 'REVIEW'}
            is_report_step = current == 'REPORT'
            gate_context = {
                'tool': {'ok': evidence_accepted if is_evidence_step else True},
                'evidence': {
                    'checked': evidence_accepted,
                    'accepted': evidence_accepted,
                },
                'report': {'persisted': report_persisted},
                'job': {'id': run.get('jobId')},
            }
            try:
                if is_report_step and not report_persisted:
                    guard.advance(
                        workflow_snapshot=snapshot,
                        current_step=current,
                        completed_steps=completed,
                        gate_passed=True,
                        gate_context=gate_context,
                    )
                existing = next(
                    (
                        item for item in repository.list_tool_calls(run['runId'])
                        if item.get('stepId') == current and item.get('status') == 'SUCCEEDED'
                    ),
                    None,
                )
                if existing is None:
                    WorkflowHarnessMixin._record_python_job_stage(
                        repository,
                        run,
                        step=step,
                        completed_steps=completed,
                        compact_result={
                            'source': 'agent_review' if is_evidence_step else 'agent_report',
                            'stage': current,
                            **({'accepted': evidence_accepted} if is_evidence_step else {}),
                            **({'persisted': report_persisted} if is_report_step else {}),
                        },
                    )
                next_step, completed = guard.advance(
                    workflow_snapshot=snapshot,
                    current_step=current,
                    completed_steps=completed,
                    gate_passed=not is_evidence_step or evidence_accepted,
                    failure_code='EVIDENCE_FAILED' if is_evidence_step and not evidence_accepted else None,
                    gate_context=gate_context if not is_evidence_step or evidence_accepted else None,
                )
            except ToolExecutionError as exc:
                run['workflowGateError'] = {
                    'code': exc.code,
                    'message': exc.message,
                    'details': exc.details,
                }
                break
            if next_step == current:
                run['workflowGateError'] = {
                    'code': 'WORKFLOW_GATE_FAILED',
                    'message': f'步骤 {current} 未声明可用的失败路由。',
                    'details': {'currentStep': current, 'requiredGate': step.get('successGate')},
                }
                break
            current = next_step
            run['stepAttempt'] = 1
        if current == 'COMPLETED':
            run.pop('workflowGateError', None)
        run.update({
            'currentStep': current,
            'completedSteps': list(dict.fromkeys(completed)),
            'updatedAt': utc_now(),
        })
        repository.save_run(run)

    @staticmethod
    def _record_harness_tool_call(
        repository: AgentRepository,
        *,
        run_id: str,
        call_id: str,
        name: str,
        arguments: dict[str, Any],
        effective_arguments: dict[str, Any],
        cached_tokens: int | None,
    ) -> None:
        canonical = json.dumps(arguments, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        effective_canonical = json.dumps(
            effective_arguments,
            ensure_ascii=False,
            sort_keys=True,
            separators=(',', ':'),
        )
        repository.save_tool_call({
            'toolCallId': call_id,
            'runId': run_id,
            'stepId': 'ROUTING',
            'toolName': name,
            'toolVersion': '1.0.0',
            'arguments': arguments,
            'argumentsSha256': sha256(canonical.encode('utf-8')).hexdigest(),
            'effectiveArguments': effective_arguments,
            'effectiveArgumentsSha256': sha256(effective_canonical.encode('utf-8')).hexdigest(),
            'risk': 'MUTATING',
            'authorized': True,
            'status': 'SUCCEEDED',
            'cachedTokens': cached_tokens,
            'updatedAt': utc_now(),
        })
