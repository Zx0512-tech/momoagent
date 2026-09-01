from __future__ import annotations

import json
from hashlib import sha256
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


ProposalSource = Literal[
    'USER_CONFIRMED',
    'PROJECT_WORKSPACE',
    'VERIFIED_RUN',
    'VERIFIED_TEMPLATE',
    'FILE_DERIVED',
    'SYSTEM_DEFAULT',
    'UNKNOWN',
]
ProposalState = Literal['NEEDS_CLARIFICATION', 'NEEDS_INPUT', 'READY_FOR_CONFIRMATION', 'BLOCKED']


class EngineeringProposalField(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra='forbid', strict=True)

    field: str
    label: str
    value: Any
    source: ProposalSource
    raw_source: str | None = Field(default=None, alias='rawSource')
    inherited: bool = False


class EngineeringTaskProposal(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra='forbid', strict=True)

    schema_version: Literal['1.0'] = Field(default='1.0', alias='schemaVersion')
    proposal_id: str = Field(alias='proposalId')
    task_type: str = Field(alias='taskType')
    summary: str
    proposal_state: ProposalState = Field(alias='proposalState')
    fields: list[EngineeringProposalField]
    unresolved_fields: list[str] = Field(alias='unresolvedFields')
    inherited_fields: list[str] = Field(alias='inheritedFields')
    warnings: list[str]
    proposed_actions: list[str] = Field(alias='proposedActions')
    approval_required: bool = Field(alias='approvalRequired')
    ready_for_approval: bool = Field(alias='readyForApproval')
    preflight_passed: bool | None = Field(default=None, alias='preflightPassed')


_FIELD_LABELS = {
    'model': '模型',
    'modelArtifactId': '模型制品',
    'solver': '求解器',
    'loadKind': '荷载类型',
    'loadArtifactId': '荷载制品',
    'damperType': '阻尼器',
    'damperTypes': '阻尼器方案',
    'selectedLayoutId': '阻尼器布置',
    'responseIds': '关注响应',
    'responseNodes': '响应节点',
    'responseElementIds': '响应单元',
    'optimizationProfile': '优化强度',
    'cases': '参数案例',
    'budget': '计算预算',
}

_TASK_ACTIONS = {
    'ANALYSIS': ['冻结分析配置', '执行环境预检', '等待用户审批', '执行真实有限元求解', '提取并核验结果证据'],
    'DAMPER_COMPARISON': ['冻结对比方案', '统一比较基准', '执行环境预检', '等待用户审批', '执行真实有限元对比并核验证据'],
    'DAMPER_PARAMETER_SWEEP': ['冻结参数案例', '执行环境预检', '等待用户审批', '执行真实有限元批量计算', '核验各案例结果'],
    'DAMPER_OPTIMIZATION': ['冻结优化配置与预算', '执行环境预检', '等待用户审批', '执行 DOE / 代理 / 候选验证', '核验并登记推荐结果'],
}


def _normalize_source(raw: str | None) -> ProposalSource:
    normalized = str(raw or '').strip().upper()
    if normalized in {'USER_SPECIFIED', 'USER_DECISION', 'CURRENT_USER', 'PRIOR_EXPLICIT'}:
        return 'USER_CONFIRMED'
    if normalized == 'PROJECT_WORKSPACE':
        return 'PROJECT_WORKSPACE'
    if normalized == 'VERIFIED_RUN':
        return 'VERIFIED_RUN'
    if normalized == 'VERIFIED_TEMPLATE':
        return 'VERIFIED_TEMPLATE'
    if normalized == 'FILE_DERIVED':
        return 'FILE_DERIVED'
    if normalized in {'DEFAULT', 'SYSTEM_DEFAULT', 'OPERATIONAL_DEFAULT'}:
        return 'SYSTEM_DEFAULT'
    return 'UNKNOWN'


def _nonempty(value: Any) -> bool:
    return value is not None and value != '' and value != [] and value != {}


def _field_value(field: str, intent: dict[str, Any], contract: dict[str, Any]) -> Any:
    if field == 'model':
        return contract.get('model')
    if field == 'modelArtifactId':
        return contract.get('modelArtifactId') or intent.get('modelArtifactId')
    if field == 'solver':
        return contract.get('solver') or intent.get('solver')
    if field == 'loadKind':
        return contract.get('loadKind') or intent.get('loadKind')
    if field == 'loadArtifactId':
        return contract.get('loadArtifactId')
    if field == 'damperType':
        damper = contract.get('damper')
        return intent.get('damperType') or (damper.get('type') if isinstance(damper, dict) else None)
    if field == 'damperTypes':
        return intent.get('damperTypes')
    if field == 'selectedLayoutId':
        return contract.get('selectedLayoutId') or intent.get('selectedLayoutId')
    if field == 'responseIds':
        return contract.get('responseIds') or intent.get('responseIds')
    if field == 'responseNodes':
        return contract.get('responseNodes') or intent.get('responseNodes')
    if field == 'responseElementIds':
        return contract.get('responseElementIds') or intent.get('responseElementIds')
    if field == 'optimizationProfile':
        return contract.get('optimizationProfile') or intent.get('optimizationProfile')
    if field == 'cases':
        return intent.get('cases')
    if field == 'budget':
        return contract.get('budget')
    return None


def _fallback_source(field: str, value: Any) -> str | None:
    if not _nonempty(value):
        return None
    if field == 'model' and value == 'STbridge':
        return 'DEFAULT'
    if field in {'modelArtifactId', 'damperType', 'damperTypes', 'responseNodes', 'responseElementIds', 'cases'}:
        return 'USER_SPECIFIED'
    return None


def build_engineering_task_proposal(run: dict[str, Any]) -> dict[str, Any]:
    """把已解析的工程意图投影成稳定、可审阅的 Chat-first 任务提案。

    提案是展示/确认层，不是新的执行入口。真实求解仍由 WorkflowGuard、预检和审批控制。
    """

    intent = dict(run.get('intent') or {})
    contract = dict(run.get('workflowContract') or {})
    field_sources = dict(contract.get('fieldSources') or {})
    missing = [str(item) for item in (intent.get('missingFields') or [])]
    task_type = str(run.get('taskType') or intent.get('taskType') or 'ANALYSIS')

    fields: list[EngineeringProposalField] = []
    for field in _FIELD_LABELS:
        value = _field_value(field, intent, contract)
        if not _nonempty(value):
            continue
        raw_source = field_sources.get(field) or _fallback_source(field, value)
        source = _normalize_source(raw_source)
        fields.append(EngineeringProposalField(
            field=field,
            label=_FIELD_LABELS[field],
            value=value,
            source=source,
            rawSource=str(raw_source) if raw_source is not None else None,
            inherited=source in {'PROJECT_WORKSPACE', 'VERIFIED_RUN'},
        ))

    inherited_fields = [item.field for item in fields if item.inherited]
    default_fields = [item.field for item in fields if item.source == 'SYSTEM_DEFAULT']
    preflight = run.get('preflight')
    preflight_passed = preflight.get('passed') if isinstance(preflight, dict) and isinstance(preflight.get('passed'), bool) else None

    if missing:
        proposal_state: ProposalState = 'NEEDS_CLARIFICATION'
    elif preflight_passed is False:
        proposal_state = 'BLOCKED'
    elif str(run.get('status') or '') in {'WAITING_MAPPING', 'LOAD_STANDARDIZATION'}:
        proposal_state = 'NEEDS_INPUT'
    else:
        proposal_state = 'READY_FOR_CONFIRMATION'

    warnings: list[str] = []
    if inherited_fields:
        warnings.append('部分配置继承自当前 Project Workspace 或已验证历史；本轮用户明确输入始终优先。')
    if default_fields:
        warnings.append('提案包含系统默认值，请在执行审批前核对。')
    if missing:
        warnings.append(f'仍需补充：{", ".join(missing)}。')
    if preflight_passed is False:
        warnings.append('工程预检未通过，当前提案不能进入真实求解。')

    plan = [str(item) for item in (run.get('plan') or []) if str(item).strip()]
    proposed_actions = plan or list(_TASK_ACTIONS.get(task_type, ['确认任务配置', '等待用户审批', '执行受控工程工作流']))
    summary = str(intent.get('summary') or run.get('goal') or '工程任务提案')

    seed = json.dumps({
        'runId': run.get('runId'),
        'taskType': task_type,
        'summary': summary,
        'fields': [item.model_dump(by_alias=True) for item in fields],
        'missing': missing,
    }, ensure_ascii=False, sort_keys=True, default=str)
    proposal_id = f'etp_{sha256(seed.encode("utf-8")).hexdigest()[:16]}'

    proposal = EngineeringTaskProposal(
        proposalId=proposal_id,
        taskType=task_type,
        summary=summary,
        proposalState=proposal_state,
        fields=fields,
        unresolvedFields=missing,
        inheritedFields=inherited_fields,
        warnings=warnings,
        proposedActions=proposed_actions,
        approvalRequired=True,
        readyForApproval=proposal_state == 'READY_FOR_CONFIRMATION',
        preflightPassed=preflight_passed,
    )
    return proposal.model_dump(by_alias=True)
