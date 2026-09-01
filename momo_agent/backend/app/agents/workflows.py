from __future__ import annotations

import json
from hashlib import sha256
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.agents.tools import ToolExecutionError, ToolRisk
from app.core.engineering_limits import (
    DOE_ACTIVE_LEARNING_MAX_ITERATIONS,
    DOE_INITIAL_MAX,
)


ENGINEERING_WORKFLOW_VERSION = '1.1.0'


class RetryPolicy(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra='forbid')

    max_attempts: int = Field(default=2, ge=1, alias='maxAttempts')
    retryable_codes: tuple[str, ...] = Field(default_factory=tuple, alias='retryableCodes')


class WorkflowStep(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra='forbid')

    step_id: str = Field(min_length=1, alias='stepId')
    title: str = Field(min_length=1)
    allowed_tools: tuple[str, ...] = Field(alias='allowedTools')
    prerequisites: tuple[str, ...] = ()
    success_gate: str = Field(min_length=1, alias='successGate')
    on_success: str | None = Field(default=None, alias='onSuccess')
    failure_routes: dict[str, str] = Field(default_factory=dict, alias='failureRoutes')
    retry_policy: RetryPolicy = Field(default_factory=RetryPolicy, alias='retryPolicy')


class WorkflowDefinition(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra='forbid')

    workflow_id: str = Field(min_length=1, alias='workflowId')
    version: str = Field(min_length=1)
    initial_step: str = Field(min_length=1, alias='initialStep')
    terminal_steps: tuple[str, ...] = Field(alias='terminalSteps')
    steps: tuple[WorkflowStep, ...]
    limits: dict[str, int | float] = Field(default_factory=dict)

    def model_post_init(self, __context: Any) -> None:
        step_ids = [step.step_id for step in self.steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError('工作流步骤 ID 不得重复')
        known = set(step_ids)
        if self.initial_step not in known:
            raise ValueError('initialStep 必须引用已登记步骤')
        if not set(self.terminal_steps) <= known:
            raise ValueError('terminalSteps 必须引用已登记步骤')
        for step in self.steps:
            targets = ({step.on_success} if step.on_success else set()) | set(step.failure_routes.values())
            if not targets <= known:
                raise ValueError(f'步骤 {step.step_id} 引用了未登记迁移目标')
            if not set(step.prerequisites) <= known:
                raise ValueError(f'步骤 {step.step_id} 引用了未登记前置步骤')


class WorkflowToolCall(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra='forbid')

    name: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
    risk: ToolRisk = ToolRisk.READ_ONLY
    requires_approval: bool = Field(default=False, alias='requiresApproval')
    approved: bool = False
    idempotency_key: str | None = Field(default=None, alias='idempotencyKey')
    usage: dict[str, int | float] = Field(default_factory=dict)


class WorkflowAuthorization(BaseModel):
    allowed: bool = True
    current_step: str = Field(alias='currentStep')
    tool_name: str = Field(alias='toolName')


def _step(
    step_id: str,
    title: str,
    tools: tuple[str, ...],
    *,
    prerequisites: tuple[str, ...] = (),
    gate: str = 'tool.ok == true',
    next_step: str | None = None,
    failures: dict[str, str] | None = None,
    max_attempts: int = 2,
) -> WorkflowStep:
    return WorkflowStep(
        stepId=step_id,
        title=title,
        allowedTools=tools,
        prerequisites=prerequisites,
        successGate=gate,
        onSuccess=next_step,
        failureRoutes=failures or {},
        retryPolicy=RetryPolicy(maxAttempts=max_attempts),
    )


def _analysis_workflow() -> WorkflowDefinition:
    return WorkflowDefinition(
        workflowId='analysis',
        version=ENGINEERING_WORKFLOW_VERSION,
        initialStep='REQUIREMENTS',
        terminalSteps=('COMPLETED', 'FAILED', 'CANCELLED'),
        steps=(
            _step('REQUIREMENTS', '需求确定', ('analysis.plan',), next_step='LOAD_PREPARATION'),
            _step('LOAD_PREPARATION', '荷载准备', ('load.inspect', 'load.map_targets'), prerequisites=('REQUIREMENTS',), next_step='PREFLIGHT', failures={'LOAD_MAPPING_FAILED': 'LOAD_PREPARATION'}),
            _step('PREFLIGHT', '环境预检', ('analysis.prepare',), prerequisites=('REQUIREMENTS', 'LOAD_PREPARATION'), gate='preflight.passed == true', next_step='WAITING_APPROVAL', failures={'PREFLIGHT_FAILED': 'PREFLIGHT'}),
            _step('WAITING_APPROVAL', '冻结审批', ('approval.request', 'approval.decide'), prerequisites=('REQUIREMENTS', 'LOAD_PREPARATION', 'PREFLIGHT'), gate='approval.status == APPROVED', next_step='EXECUTION', failures={'APPROVAL_REJECTED': 'PREFLIGHT'}, max_attempts=1),
            _step('EXECUTION', '单次真实求解', ('analysis.run',), prerequisites=('REQUIREMENTS', 'LOAD_PREPARATION', 'PREFLIGHT', 'WAITING_APPROVAL'), gate='job.id != null', next_step='RESULT_EXTRACTION', failures={'JOB_FAILED': 'FAILED'}, max_attempts=1),
            _step('RESULT_EXTRACTION', '结果提取', ('result.extract',), prerequisites=('EXECUTION',), next_step='EVIDENCE_REVIEW'),
            _step('EVIDENCE_REVIEW', '证据审查', ('analysis.review',), prerequisites=('EXECUTION', 'RESULT_EXTRACTION'), gate='evidence.checked == true', next_step='REPORT', failures={'EVIDENCE_FAILED': 'REPORT'}),
            _step('REPORT', '结果报告', ('analysis.visualize', 'workflow.complete'), prerequisites=('RESULT_EXTRACTION',), gate='report.persisted == true', next_step='COMPLETED'),
            _step('COMPLETED', '完成', (), gate='terminal'),
            _step('FAILED', '失败', (), gate='terminal'),
            _step('CANCELLED', '取消', (), gate='terminal'),
        ),
        limits={'realSolveCount': 1},
    )


def _comparison_workflow() -> WorkflowDefinition:
    return WorkflowDefinition(
        workflowId='damper_comparison',
        version=ENGINEERING_WORKFLOW_VERSION,
        initialStep='REQUIREMENTS',
        terminalSteps=('COMPLETED', 'FAILED', 'CANCELLED'),
        steps=(
            _step('REQUIREMENTS', '需求确定', ('comparison.plan',), next_step='CALIBRATION'),
            _step('CALIBRATION', '各阻尼器校准', ('comparison.calibrate',), prerequisites=('REQUIREMENTS',), next_step='PREFLIGHT'),
            _step('PREFLIGHT', '荷载与环境预检', ('comparison.prepare',), prerequisites=('CALIBRATION',), next_step='WAITING_APPROVAL'),
            _step('WAITING_APPROVAL', '冻结审批', ('approval.request', 'approval.decide'), prerequisites=('PREFLIGHT',), gate='approval.status == APPROVED', next_step='EXECUTION', max_attempts=1),
            _step('EXECUTION', '串行执行各工况', ('comparison.run',), prerequisites=('WAITING_APPROVAL',), next_step='COMPARISON', max_attempts=1),
            _step('COMPARISON', '同基准比较', ('comparison.compare',), prerequisites=('EXECUTION',), next_step='EVIDENCE_REVIEW'),
            _step('EVIDENCE_REVIEW', '证据审查', ('comparison.review',), prerequisites=('COMPARISON',), gate='evidence.checked == true', next_step='REPORT', failures={'EVIDENCE_FAILED': 'REPORT'}),
            _step('REPORT', '结果报告', ('workflow.complete',), prerequisites=('COMPARISON',), gate='report.persisted == true', next_step='COMPLETED'),
            _step('COMPLETED', '完成', (), gate='terminal'),
            _step('FAILED', '失败', (), gate='terminal'),
            _step('CANCELLED', '取消', (), gate='terminal'),
        ),
        # 对比放行两种或三种阻尼器；上限与 DamperComparisonAgent/平台侧校验保持一致。
        limits={'caseCount': 3, 'maxConcurrentCases': 1},
    )


def _parameter_sweep_workflow() -> WorkflowDefinition:
    """阻尼器参数批量正向求解；不含基线、DOE、代理模型和优化约束。"""
    return WorkflowDefinition(
        workflowId='damper_parameter_sweep',
        version=ENGINEERING_WORKFLOW_VERSION,
        initialStep='REQUIREMENTS',
        terminalSteps=('COMPLETED', 'FAILED', 'CANCELLED'),
        steps=(
            _step('REQUIREMENTS', '批量参数需求确定', ('sweep.plan',), next_step='LOAD_PREPARATION'),
            _step('LOAD_PREPARATION', '荷载准备', ('load.inspect', 'load.map_targets'), prerequisites=('REQUIREMENTS',), next_step='PREFLIGHT'),
            _step('PREFLIGHT', '求解环境预检', ('sweep.prepare',), prerequisites=('REQUIREMENTS', 'LOAD_PREPARATION'), gate='preflight.passed == true', next_step='WAITING_APPROVAL', failures={'PREFLIGHT_FAILED': 'PREFLIGHT'}),
            _step('WAITING_APPROVAL', '冻结审批', ('approval.request', 'approval.decide'), prerequisites=('REQUIREMENTS', 'LOAD_PREPARATION', 'PREFLIGHT'), gate='approval.status == APPROVED', next_step='EXECUTION', max_attempts=1),
            _step('EXECUTION', '批量并发正向求解', ('sweep.run',), prerequisites=('WAITING_APPROVAL',), gate='job.id != null', next_step='RESULT_EXTRACTION', failures={'JOB_FAILED': 'FAILED'}, max_attempts=1),
            _step('RESULT_EXTRACTION', '结果提取', ('result.extract',), prerequisites=('EXECUTION',), next_step='EVIDENCE_REVIEW'),
            _step('EVIDENCE_REVIEW', '证据审查', ('sweep.review',), prerequisites=('EXECUTION', 'RESULT_EXTRACTION'), gate='evidence.checked == true', next_step='REPORT', failures={'EVIDENCE_FAILED': 'REPORT'}),
            _step('REPORT', '结果报告', ('workflow.complete',), prerequisites=('RESULT_EXTRACTION',), gate='report.persisted == true', next_step='COMPLETED'),
            _step('COMPLETED', '完成', (), gate='terminal'),
            _step('FAILED', '失败', (), gate='terminal'),
            _step('CANCELLED', '取消', (), gate='terminal'),
        ),
        limits={'caseCount': 64, 'maxConcurrentCases': 8},
    )


def _optimization_workflow() -> WorkflowDefinition:
    """所有优化 Profile 共用的一条 baseline-first 工作流。"""
    return WorkflowDefinition(
        workflowId='damper_optimization',
        version=ENGINEERING_WORKFLOW_VERSION,
        initialStep='REQUIREMENTS',
        terminalSteps=('COMPLETED', 'FAILED', 'CANCELLED'),
        steps=(
            _step('REQUIREMENTS', '需求确定', ('optimization.prepare_plan',), next_step='PREFLIGHT'),
            _step('PREFLIGHT', '真实环境预检', ('optimization.preflight',), prerequisites=('REQUIREMENTS',), next_step='WAITING_APPROVAL'),
            _step('WAITING_APPROVAL', '执行授权审批', ('approval.request', 'approval.decide'), prerequisites=('PREFLIGHT',), gate='approval.status == APPROVED', next_step='BASELINE', max_attempts=1),
            _step('BASELINE', '无控基线', ('optimization.run_baseline',), prerequisites=('WAITING_APPROVAL',), next_step='DOE', max_attempts=1),
            _step('DOE', '受控 DOE（与无控基线并行）', ('optimization.run_doe',), prerequisites=('WAITING_APPROVAL',), next_step='SURROGATE', max_attempts=1),
            _step('SURROGATE', '代理模型', ('optimization.fit_surrogate',), prerequisites=('DOE',), next_step='ACTIVE_LEARNING'),
            _step('ACTIVE_LEARNING', '受控主动学习', ('optimization.active_learning',), prerequisites=('SURROGATE',), next_step='CANDIDATES'),
            _step('CANDIDATES', '候选枚举', ('optimization.rank_candidates',), prerequisites=('ACTIVE_LEARNING',), next_step='RECOMMENDATION'),
            _step('RECOMMENDATION', 'Pareto 与 TOPSIS 推荐', ('optimization.recommend',), prerequisites=('CANDIDATES',), next_step='FEM_VALIDATION'),
            _step('FEM_VALIDATION', '独立 FEM 复核', ('optimization.validate_candidates',), prerequisites=('RECOMMENDATION',), next_step='REVIEW', max_attempts=1),
            _step('REVIEW', '最终审查', ('optimization.review',), prerequisites=('FEM_VALIDATION',), gate='evidence.checked == true', next_step='REPORT', failures={'REVIEW_CORRECTION': 'FEM_VALIDATION', 'EVIDENCE_FAILED': 'REPORT'}),
            _step('REPORT', '证据报告', ('workflow.complete',), prerequisites=('FEM_VALIDATION',), gate='report.persisted == true', next_step='COMPLETED'),
            _step('COMPLETED', '完成', (), gate='terminal'),
            _step('FAILED', '失败', (), gate='terminal'),
            _step('CANCELLED', '取消', (), gate='terminal'),
        ),
        limits={
            'doeDesignCount': DOE_INITIAL_MAX,
            'surrogateCv': 10,
            'maxActiveLearningIterations': DOE_ACTIVE_LEARNING_MAX_ITERATIONS,
            'candidateCount': 728,
            'maxReviewIterations': 1,
        },
    )


def _inquiry_workflow() -> WorkflowDefinition:
    return WorkflowDefinition(
        workflowId='result_inquiry',
        version='1.0.0',
        initialStep='LOCATE_RESULT',
        terminalSteps=('COMPLETED', 'FAILED'),
        steps=(
            _step('LOCATE_RESULT', '定位终态结果', ('result.find_recent',), next_step='INSPECT_CATALOG'),
            _step('INSPECT_CATALOG', '查看数据目录', ('result.columns',), prerequisites=('LOCATE_RESULT',), next_step='QUERY'),
            _step('QUERY', '查询只读结果', ('result.columns', 'result.peak', 'result.at_time', 'result.correlate', 'result.compare', 'result.topsis', 'result.sweep_cases'), prerequisites=('INSPECT_CATALOG',), next_step='EVIDENCE_REVIEW'),
            _step('EVIDENCE_REVIEW', '核对证据', ('evidence.verify',), prerequisites=('QUERY',), next_step='REPORT'),
            _step('REPORT', '回答追问', ('workflow.complete',), prerequisites=('EVIDENCE_REVIEW',), next_step='COMPLETED'),
            _step('COMPLETED', '完成', (), gate='terminal'),
            _step('FAILED', '失败', (), gate='terminal'),
        ),
    )


_WORKFLOWS = {
    'ANALYSIS': _analysis_workflow(),
    'DAMPER_COMPARISON': _comparison_workflow(),
    'DAMPER_PARAMETER_SWEEP': _parameter_sweep_workflow(),
    'DAMPER_OPTIMIZATION': _optimization_workflow(),
    'RESULT_INQUIRY': _inquiry_workflow(),
}


def workflow_definition(task_type: str) -> WorkflowDefinition:
    try:
        return _WORKFLOWS[task_type].model_copy(deep=True)
    except KeyError as exc:
        raise ToolExecutionError('WORKFLOW_NOT_FOUND', f'未登记工作流: {task_type}') from exc


def freeze_workflow(definition: WorkflowDefinition) -> dict[str, Any]:
    snapshot = definition.model_dump(mode='json', by_alias=True)
    # 冻结时展开前置条件传递闭包，运行时只读取快照即可阻止跳步。
    steps = {item['stepId']: item for item in snapshot['steps']}
    expanded: dict[str, tuple[str, ...]] = {}

    def expand(step_id: str, trail: tuple[str, ...] = ()) -> tuple[str, ...]:
        if step_id in expanded:
            return expanded[step_id]
        if step_id in trail:
            raise ToolExecutionError('WORKFLOW_CYCLE', f'工作流前置条件存在循环: {step_id}')
        result: list[str] = []
        for prerequisite in steps[step_id].get('prerequisites', []):
            for ancestor in (prerequisite, *expand(prerequisite, (*trail, step_id))):
                if ancestor not in result:
                    result.append(ancestor)
        expanded[step_id] = tuple(result)
        return expanded[step_id]

    for step_id, item in steps.items():
        item['prerequisites'] = list(expand(step_id))
    canonical = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return {
        'workflowId': definition.workflow_id,
        'workflowVersion': definition.version,
        'workflowSha256': sha256(canonical.encode('utf-8')).hexdigest(),
        'workflowSnapshot': snapshot,
    }


class WorkflowGuard:
    def authorize(
        self,
        *,
        workflow_snapshot: dict[str, Any],
        current_step: str,
        tool_call: WorkflowToolCall,
        completed_steps: tuple[str, ...] | list[str] = (),
        step_attempt: int = 1,
        repeated_no_progress: int = 0,
        observational_tools: tuple[str, ...] | list[str] | frozenset[str] = (),
    ) -> WorkflowAuthorization:
        definition = WorkflowDefinition.model_validate(workflow_snapshot)
        step = next((item for item in definition.steps if item.step_id == current_step), None)
        if step is None:
            raise ToolExecutionError(
                'WORKFLOW_STEP_NOT_FOUND',
                f'工作流 {definition.workflow_id} 不包含步骤 {current_step}',
            )
        is_observation = (
            tool_call.risk is ToolRisk.READ_ONLY
            and tool_call.name in observational_tools
        )
        if tool_call.name not in step.allowed_tools and not is_observation:
            raise ToolExecutionError(
                'WORKFLOW_STEP_VIOLATION',
                f'步骤 {current_step} 不允许调用工具 {tool_call.name}',
                details={
                    'currentStep': current_step,
                    'requestedTool': tool_call.name,
                    'allowedTools': list(step.allowed_tools),
                    'requiredGate': step.success_gate,
                },
            )
        missing = [name for name in step.prerequisites if name not in completed_steps]
        if missing:
            raise ToolExecutionError(
                'WORKFLOW_PREREQUISITE_MISSING',
                f'步骤 {current_step} 的前置条件尚未完成',
                details={'currentStep': current_step, 'missingSteps': missing},
            )
        usage = dict(tool_call.usage)
        if 'realSolveCount' in definition.limits and tool_call.risk is ToolRisk.SOLVER_EXECUTION:
            usage.setdefault('realSolveCount', 1)
        for limit_name, limit in definition.limits.items():
            if limit_name in usage and usage[limit_name] > limit:
                raise ToolExecutionError(
                    'WORKFLOW_LIMIT_EXCEEDED',
                    f'工具 {tool_call.name} 超出工作流资源上限 {limit_name}={limit}',
                    details={
                        'limit': limit_name,
                        'maximum': limit,
                        'requested': usage[limit_name],
                        'currentStep': current_step,
                    },
                )
        if not is_observation and repeated_no_progress >= 2:
            raise ToolExecutionError(
                'HARNESS_LOOP_DETECTED',
                '相同工具调用连续两次未推动工作流状态',
                details={'currentStep': current_step, 'toolName': tool_call.name},
            )
        if not is_observation and step_attempt > step.retry_policy.max_attempts:
            raise ToolExecutionError(
                'WORKFLOW_RETRY_EXHAUSTED',
                f'步骤 {current_step} 已超过最大尝试次数',
                details={
                    'currentStep': current_step,
                    'attempt': step_attempt,
                    'maxAttempts': step.retry_policy.max_attempts,
                },
            )
        if tool_call.requires_approval and not tool_call.approved:
            raise ToolExecutionError('APPROVAL_REQUIRED', f'工具 {tool_call.name} 需要人工审批')
        if tool_call.risk is not ToolRisk.READ_ONLY and not tool_call.idempotency_key:
            raise ToolExecutionError(
                'IDEMPOTENCY_KEY_REQUIRED',
                f'有副作用工具 {tool_call.name} 必须提供幂等键',
            )
        return WorkflowAuthorization(
            currentStep=current_step,
            toolName=tool_call.name,
        )

    def advance(
        self,
        *,
        workflow_snapshot: dict[str, Any],
        current_step: str,
        completed_steps: tuple[str, ...] | list[str],
        gate_passed: bool,
        failure_code: str | None = None,
        gate_context: dict[str, Any] | None = None,
    ) -> tuple[str, list[str]]:
        definition = WorkflowDefinition.model_validate(workflow_snapshot)
        step = next((item for item in definition.steps if item.step_id == current_step), None)
        if step is None:
            raise ToolExecutionError('WORKFLOW_STEP_NOT_FOUND', f'未登记步骤: {current_step}')
        completed = list(dict.fromkeys(completed_steps))
        if gate_passed:
            if gate_context is None:
                raise ToolExecutionError(
                    'WORKFLOW_GATE_CONTEXT_REQUIRED',
                    f'步骤 {current_step} 推进必须提供成功门禁上下文',
                    details={'currentStep': current_step, 'requiredGate': step.success_gate},
                )
            if not self._gate_satisfied(step.success_gate, gate_context):
                raise ToolExecutionError(
                    'WORKFLOW_GATE_FAILED',
                    f'步骤 {current_step} 未满足成功门槛 {step.success_gate}',
                    details={'currentStep': current_step, 'requiredGate': step.success_gate},
                )
            if current_step not in completed:
                completed.append(current_step)
            return step.on_success or current_step, completed
        # 取消是全局终止操作，不依赖每个业务步骤重复声明 failure route。
        # 将游标落到冻结快照中的 CANCELLED，避免后续展示同步把终态改回中间态。
        if failure_code == 'CANCELLED' and 'CANCELLED' in definition.terminal_steps:
            return 'CANCELLED', completed
        if failure_code and failure_code in step.failure_routes:
            return step.failure_routes[failure_code], completed
        return current_step, completed

    @staticmethod
    def _gate_satisfied(expression: str, context: dict[str, Any]) -> bool:
        if expression == 'terminal':
            return True
        operator = '==' if '==' in expression else '!=' if '!=' in expression else None
        if operator is None:
            return False
        left, right = (part.strip() for part in expression.split(operator, 1))
        value: Any = context
        for part in left.split('.'):
            if not isinstance(value, dict) or part not in value:
                value = None
                break
            value = value[part]
        if right.lower() == 'true':
            expected: Any = True
        elif right.lower() == 'false':
            expected = False
        elif right.lower() == 'null':
            expected = None
        else:
            expected = right.strip('"\'')
        return value == expected if operator == '==' else value != expected
