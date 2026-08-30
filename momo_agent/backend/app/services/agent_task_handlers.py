"""按任务类型收敛的工程编排 handler。

AgentService 此前在建约、审批修改重建、意图回填、预估求解数、Job 验收
投影等处各自维护一条 task_type if/elif 分支；新增任务类型需要同步改动
所有分支且容易漏。本模块把每个任务类型的编排差异收敛为一个 handler，
与 app.agents.task_registry 的 EngineeringTaskSpec 一一对应：spec 描述
"这个任务类型是什么"（注册键、工具名、报告文件名），handler 描述
"这个任务类型怎么编排"。AgentService 只按 task_type 查表并委托。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException

from app.agents.damper_optimization import FULL_OPTIMIZATION_CONTRACT
from app.core.engineering_limits import (
    DOE_ACTIVE_LEARNING_MAX_ADDITIONAL,
    DOE_FIXED_REAL_SOLVE_OVERHEAD,
)
from app.services.agent_engineering import (
    build_damper_comparison_contract,
    build_engineering_contract,
    build_parameter_sweep_contract,
)


@dataclass(frozen=True)
class ApprovalUpdateContext:
    """审批前修改（PATCH approval）时重建合同所需的全部输入。

    值由 AgentService 统一按 changes → frozen → previous_contract 优先级
    解析，handler 只负责各任务类型的差异部分。
    """

    changes: dict[str, Any]
    frozen: dict[str, Any]
    previous_contract: dict[str, Any]
    solver: str
    response_ids: list[Any] = field(default_factory=list)
    damper_type: Any = None
    damper_types: list[Any] = field(default_factory=list)
    load_artifact_id: Any = None
    load_sha256: Any = None


def _review_projection(outcome: Any) -> dict[str, Any]:
    return {
        'runStatus': outcome.run_status,
        'evidenceMode': outcome.evidence_mode,
        'accepted': outcome.accepted,
        'checks': outcome.checks,
        'message': outcome.message,
    }


class EngineeringTaskHandler:
    """单个工程任务类型的编排差异；基类实现通用工程任务行为。

    ``prepare_approval_method`` 指向 AgentService 上的准备审批方法名。
    这些方法保留为独立可覆盖的接缝：测试和会话澄清合并流程会按名称
    monkeypatch / getattr 它们。
    """

    prepare_approval_method = '_prepare_engineering_analysis_approval'

    def __init__(self, task_type: str) -> None:
        self.task_type = task_type

    def build_contract_from_intent(
        self,
        intent: Any,
        *,
        load_import: dict[str, Any] | None,
        field_sources: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        return build_engineering_contract(
            task_type=self.task_type,
            solver=intent.solver,
            damper_type=intent.damper_type,
            response_ids=list(intent.response_ids),
            selected_layout_id=intent.selected_layout_id or 'TWO_PER_TOWER',
            load_kind=intent.load_kind or 'EARTHQUAKE',
            load_artifact_id=load_import.get('fileArtifactId') if load_import else None,
            field_sources=field_sources,
        )

    def rebuild_contract(self, update: ApprovalUpdateContext) -> dict[str, Any]:
        return build_engineering_contract(
            task_type=self.task_type,
            solver=update.solver,
            damper_type=update.damper_type,
            response_ids=list(update.response_ids),
            load_artifact_id=update.load_artifact_id,
            load_sha256=update.load_sha256,
        )

    def apply_intent_updates(
        self,
        intent: dict[str, Any],
        update: ApprovalUpdateContext,
        *,
        contract: dict[str, Any],
    ) -> None:
        if update.damper_type is not None:
            intent['damperType'] = update.damper_type

    def estimated_solves(
        self,
        *,
        budget: dict[str, Any],
        contract: dict[str, Any],
        cases: list[Any],
    ) -> tuple[int | None, int | None]:
        return (None, None)

    def reflect(
        self,
        agent: Any,
        job: dict[str, Any],
        *,
        run: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        outcome = agent.review(
            job,
            workflow_contract={
                'taskType': (run or {}).get('taskType'),
                **((run or {}).get('workflowContract') or {}),
            },
        )
        return {**_review_projection(outcome), **outcome.extra}


class AnalysisTaskHandler(EngineeringTaskHandler):
    def __init__(self) -> None:
        super().__init__('ANALYSIS')

    def estimated_solves(self, *, budget, contract, cases):
        return (1, 1)


class DamperComparisonTaskHandler(EngineeringTaskHandler):
    prepare_approval_method = '_prepare_damper_comparison_approval'

    def __init__(self) -> None:
        super().__init__('DAMPER_COMPARISON')

    def build_contract_from_intent(self, intent, *, load_import, field_sources=None):
        return build_damper_comparison_contract(
            solver=intent.solver,
            damper_types=list(intent.damper_types),
            response_ids=list(intent.response_ids),
            selected_layout_id=intent.selected_layout_id or 'TWO_PER_TOWER',
            load_kind=intent.load_kind or 'EARTHQUAKE',
            field_sources=field_sources,
        )

    def rebuild_contract(self, update):
        if len(update.damper_types) not in {2, 3}:
            raise HTTPException(status_code=422, detail={
                'code': 'INVALID_DAMPER_TYPES',
                'message': '对比任务必须提供两种或三种不同阻尼器类型。',
            })
        return build_damper_comparison_contract(
            solver=update.solver,
            damper_types=list(update.damper_types),
            response_ids=list(update.response_ids),
            selected_layout_id=(
                update.changes.get('selectedLayoutId')
                or update.frozen.get('selectedLayoutId')
                or update.previous_contract.get('selectedLayoutId')
                or 'TWO_PER_TOWER'
            ),
            # 荷载类型不在审批可改白名单里：沿用已冻结的工况，避免改阻尼器
            # 类型或响应量时把风工况静默退回地震模板。
            load_kind=(
                update.frozen.get('loadKind')
                or update.previous_contract.get('loadKind')
                or 'EARTHQUAKE'
            ),
        )

    def apply_intent_updates(self, intent, update, *, contract):
        intent['damperTypes'] = list(update.damper_types)

    def estimated_solves(self, *, budget, contract, cases):
        # 比较任务的预算以冻结的 cases 为准；三种阻尼器对比不能继续沿用
        # 旧的双工况常量，否则审批说明会低估真实求解数。
        case_count = len(cases)
        if not case_count:
            case_count = int(
                (budget or {}).get('caseCount')
                or (contract or {}).get('budget', {}).get('caseCount')
                or 0
            )
        return (case_count, case_count)

    def reflect(self, agent, job, *, run=None):
        outcome = agent.review(job)
        return {
            **_review_projection(outcome),
            'caseResults': outcome.extra.get('caseResults', []),
        }


class DamperParameterSweepTaskHandler(EngineeringTaskHandler):
    prepare_approval_method = '_prepare_damper_parameter_sweep_approval'

    def __init__(self) -> None:
        super().__init__('DAMPER_PARAMETER_SWEEP')

    def build_contract_from_intent(self, intent, *, load_import, field_sources=None):
        return build_parameter_sweep_contract(
            solver=intent.solver,
            cases=[case.model_dump(by_alias=True) for case in intent.cases],
            response_ids=list(intent.response_ids),
            selected_layout_id=intent.selected_layout_id or 'TWO_PER_TOWER',
            load_kind=intent.load_kind or 'EARTHQUAKE',
            max_concurrent_cases=intent.max_concurrent_cases,
            field_sources=field_sources,
        )

    def rebuild_contract(self, update):
        cases = [
            # 冻结/已建契约里的算例是规范化后的形态，带 solverModule、
            # productionReady 等派生字段；DamperParameterSweepCase 是
            # extra='forbid' 的意图模型，直接回灌会抛 ValidationError（HTTP 500）。
            # 只回灌用户可指定的字段，派生字段由契约构造按求解器重新登记。
            {
                'caseId': case.get('caseId'),
                'damperType': case.get('damperType'),
                'parameters': dict(case.get('parameters') or {}),
            }
            for case in (
                update.changes.get('cases')
                or update.frozen.get('cases')
                or update.previous_contract.get('cases')
                or []
            )
        ]
        return build_parameter_sweep_contract(
            solver=update.solver,
            cases=cases,
            response_ids=list(update.response_ids),
            selected_layout_id=(
                update.changes.get('selectedLayoutId')
                or update.frozen.get('selectedLayoutId')
                or update.previous_contract.get('selectedLayoutId')
                or 'TWO_PER_TOWER'
            ),
            max_concurrent_cases=int(
                update.changes.get('maxConcurrentCases')
                or update.frozen.get('budget', {}).get('maxConcurrentCases')
                or update.previous_contract.get('budget', {}).get('maxConcurrentCases')
                or 4
            ),
            # 荷载类型不在审批可改白名单里：沿用已冻结的工况，避免改并发数
            # 或案例时把风工况静默退回地震模板。
            load_kind=(
                update.frozen.get('loadKind')
                or update.previous_contract.get('loadKind')
                or 'EARTHQUAKE'
            ),
        )

    def apply_intent_updates(self, intent, update, *, contract):
        intent['cases'] = contract.get('cases') or []
        intent['maxConcurrentCases'] = contract.get('budget', {}).get('maxConcurrentCases', 4)

    def estimated_solves(self, *, budget, contract, cases):
        return (len(cases), len(cases))

    def reflect(self, agent, job, *, run=None):
        outcome = agent.review(
            job,
            workflow_contract=(run or {}).get('workflowContract') if run else None,
        )
        return {
            **_review_projection(outcome),
            'caseResults': outcome.extra.get('caseResults', []),
        }


class DamperOptimizationTaskHandler(EngineeringTaskHandler):
    prepare_approval_method = '_prepare_engineering_optimization_approval'

    def __init__(self, task_type: str = 'DAMPER_OPTIMIZATION') -> None:
        super().__init__(task_type)

    def estimated_solves(self, *, budget, contract, cases):
        initial_doe_count = int(
            budget.get('doeDesignCount')
            or contract.get('doeDesignCount')
            or FULL_OPTIMIZATION_CONTRACT.get('doeDesignCount')
            or 0
        )
        estimated_min = initial_doe_count + DOE_FIXED_REAL_SOLVE_OVERHEAD
        return (estimated_min, estimated_min + DOE_ACTIVE_LEARNING_MAX_ADDITIONAL)


class FullOptimizationTaskHandler(DamperOptimizationTaskHandler):
    def __init__(self) -> None:
        super().__init__('FULL_OPTIMIZATION')

    def rebuild_contract(self, update):
        # 完整优化的合同在计划阶段冻结，审批前修改只允许白名单字段
        # （预算等）覆盖，不重建合同本体。
        return dict(update.previous_contract)


_HANDLERS: dict[str, EngineeringTaskHandler] = {
    handler.task_type: handler
    for handler in (
        AnalysisTaskHandler(),
        DamperComparisonTaskHandler(),
        DamperParameterSweepTaskHandler(),
        DamperOptimizationTaskHandler(),
        FullOptimizationTaskHandler(),
    )
}


def orchestration_handler(task_type: str) -> EngineeringTaskHandler | None:
    """返回已注册的任务编排 handler；未知任务类型返回 None。"""
    return _HANDLERS.get(str(task_type))


def orchestration_handler_or_generic(task_type: str) -> EngineeringTaskHandler:
    """未注册的任务类型退回通用工程行为（与旧 else 分支一致）。"""
    return _HANDLERS.get(str(task_type)) or EngineeringTaskHandler(str(task_type))
