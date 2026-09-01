from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding='utf-8')
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'{path}: expected exactly one match, found {count}: {old[:80]!r}')
    target.write_text(text.replace(old, new), encoding='utf-8')


def write_new(path: str, content: str) -> None:
    target = Path(path)
    if target.exists():
        raise SystemExit(f'{path}: target already exists')
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding='utf-8')


PROPOSAL_SERVICE = r'''from __future__ import annotations

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
'''

PROPOSAL_TEST = r'''from app.services.agent_task_proposal import build_engineering_task_proposal


def test_proposal_exposes_user_workspace_and_default_sources() -> None:
    run = {
        'runId': 'agr_proposal',
        'taskType': 'DAMPER_OPTIMIZATION',
        'goal': '沿用上次地震做完整黏滞阻尼优化',
        'status': 'WAITING_APPROVAL',
        'intent': {
            'taskType': 'DAMPER_OPTIMIZATION',
            'solver': 'ANSYS',
            'damperType': 'VISCOUS',
            'loadKind': 'EARTHQUAKE',
            'selectedLayoutId': 'TWO_PER_TOWER',
            'responseIds': ['max_girder_end_displacement', 'max_tower_base_shear'],
            'optimizationProfile': 'FULL',
            'missingFields': [],
            'summary': '完整黏滞阻尼优化',
        },
        'workflowContract': {
            'model': 'STbridge',
            'solver': 'ANSYS',
            'loadKind': 'EARTHQUAKE',
            'damper': {'type': 'VISCOUS'},
            'selectedLayoutId': 'TWO_PER_TOWER',
            'responseIds': ['max_girder_end_displacement', 'max_tower_base_shear'],
            'optimizationProfile': 'FULL',
            'budget': {'doeDesignCount': 15},
            'fieldSources': {
                'solver': 'USER_SPECIFIED',
                'loadKind': 'PROJECT_WORKSPACE',
                'selectedLayoutId': 'PROJECT_WORKSPACE',
                'responseIds': 'PROJECT_WORKSPACE',
                'optimizationProfile': 'USER_SPECIFIED',
                'budget': 'DEFAULT',
            },
        },
        'preflight': {'passed': True},
        'plan': ['冻结优化计划', '审批后执行真实 FEM'],
    }

    proposal = build_engineering_task_proposal(run)
    by_field = {item['field']: item for item in proposal['fields']}

    assert proposal['proposalState'] == 'READY_FOR_CONFIRMATION'
    assert proposal['readyForApproval'] is True
    assert proposal['preflightPassed'] is True
    assert by_field['solver']['source'] == 'USER_CONFIRMED'
    assert by_field['loadKind']['source'] == 'PROJECT_WORKSPACE'
    assert by_field['loadKind']['inherited'] is True
    assert by_field['budget']['source'] == 'SYSTEM_DEFAULT'
    assert by_field['damperType']['source'] == 'USER_CONFIRMED'
    assert proposal['inheritedFields'] == ['loadKind', 'selectedLayoutId', 'responseIds']
    assert proposal['proposedActions'] == ['冻结优化计划', '审批后执行真实 FEM']
    assert any('Project Workspace' in warning for warning in proposal['warnings'])


def test_proposal_keeps_missing_fields_out_of_approval_state() -> None:
    run = {
        'runId': 'agr_clarify',
        'taskType': 'ANALYSIS',
        'goal': '分析一下',
        'status': 'NEEDS_CLARIFICATION',
        'intent': {
            'taskType': 'CLARIFICATION',
            'solver': None,
            'responseIds': [],
            'missingFields': ['solver', 'responseIds'],
            'summary': '需要补充分析配置',
        },
        'workflowContract': {},
    }

    proposal = build_engineering_task_proposal(run)

    assert proposal['proposalState'] == 'NEEDS_CLARIFICATION'
    assert proposal['readyForApproval'] is False
    assert proposal['unresolvedFields'] == ['solver', 'responseIds']
    assert proposal['approvalRequired'] is True


def test_proposal_is_stable_for_same_resolved_contract() -> None:
    run = {
        'runId': 'agr_stable',
        'taskType': 'ANALYSIS',
        'goal': '做一次分析',
        'status': 'PLANNING',
        'intent': {'taskType': 'ANALYSIS', 'solver': 'OPENSEESPY_INPROC', 'missingFields': [], 'summary': '单次分析'},
        'workflowContract': {
            'model': 'STbridge',
            'solver': 'OPENSEESPY_INPROC',
            'loadKind': 'EARTHQUAKE',
            'responseIds': ['max_girder_end_displacement'],
            'fieldSources': {'solver': 'USER_SPECIFIED', 'loadKind': 'DEFAULT', 'responseIds': 'USER_SPECIFIED'},
        },
    }

    first = build_engineering_task_proposal(run)
    second = build_engineering_task_proposal(run)
    assert first['proposalId'] == second['proposalId']
'''

TASK_PROPOSAL_CARD = r'''import type React from "react";
import type { EngineeringTaskProposal, EngineeringProposalSource } from "../../../api/agentApi";
import { cardStyles } from "./cardStyles";

const taskLabels: Record<string, string> = {
  ANALYSIS: "有限元分析",
  DAMPER_OPTIMIZATION: "阻尼器优化",
  DAMPER_COMPARISON: "阻尼器对比",
  DAMPER_PARAMETER_SWEEP: "阻尼器参数批量计算"
};

const stateLabels: Record<EngineeringTaskProposal["proposalState"], string> = {
  NEEDS_CLARIFICATION: "待补充信息",
  NEEDS_INPUT: "待确认输入",
  READY_FOR_CONFIRMATION: "待执行确认",
  BLOCKED: "预检阻断"
};

const sourceLabels: Record<EngineeringProposalSource, string> = {
  USER_CONFIRMED: "用户已明确",
  PROJECT_WORKSPACE: "Project Workspace",
  VERIFIED_RUN: "已验证历史 Run",
  VERIFIED_TEMPLATE: "已验证模板",
  FILE_DERIVED: "文件派生",
  SYSTEM_DEFAULT: "系统默认",
  UNKNOWN: "来源未登记"
};

function formatValue(value: unknown): string {
  if (Array.isArray(value)) {
    if (value.every(item => typeof item !== "object" || item === null)) return value.join("、");
    return value.map(item => {
      if (!item || typeof item !== "object") return String(item);
      const record = item as Record<string, unknown>;
      const name = String(record.caseId ?? record.case_id ?? "case");
      const parameters = record.parameters;
      if (!parameters || typeof parameters !== "object") return name;
      const detail = Object.entries(parameters as Record<string, unknown>)
        .map(([key, parameter]) => `${key}=${String(parameter)}`)
        .join(", ");
      return `${name} (${detail})`;
    }).join("；");
  }
  if (value && typeof value === "object") {
    return Object.entries(value as Record<string, unknown>)
      .map(([key, item]) => `${key}=${String(item)}`)
      .join("，");
  }
  return String(value);
}

export const TaskProposalCard = ({ proposal }: { proposal: EngineeringTaskProposal }) => (
  <div style={cardStyles.card} aria-label="工程任务提案">
    <div style={cardStyles.header}>
      <span style={cardStyles.title}>工程任务提案</span>
      <span style={proposal.proposalState === "BLOCKED" ? cardStyles.badgeWarning : cardStyles.badge}>
        {stateLabels[proposal.proposalState]}
      </span>
    </div>

    <p style={cardStyles.summary}>{proposal.summary}</p>
    <div style={{ ...cardStyles.metaGrid, marginTop: 10 }}>
      <div>
        <span style={cardStyles.metaLabel}>任务类型</span>
        <div style={cardStyles.metaValue}>{taskLabels[proposal.taskType] ?? proposal.taskType}</div>
      </div>
      {proposal.fields.map(item => (
        <div key={item.field}>
          <span style={cardStyles.metaLabel}>{item.label}</span>
          <div style={styles.valueLine}>
            <span style={cardStyles.metaValue}>{formatValue(item.value)}</span>
            <span style={sourceStyle(item.source)}>{sourceLabels[item.source]}</span>
          </div>
        </div>
      ))}
    </div>

    {proposal.unresolvedFields.length > 0 && (
      <p style={cardStyles.note}>仍需补充：{proposal.unresolvedFields.join("、")}</p>
    )}

    {proposal.proposedActions.length > 0 && (
      <ol style={cardStyles.list}>
        {proposal.proposedActions.map(action => <li key={action}>{action}</li>)}
      </ol>
    )}

    {proposal.preflightPassed !== null && proposal.preflightPassed !== undefined && (
      <p style={cardStyles.note}>
        环境预检：
        <strong style={{ color: proposal.preflightPassed ? "var(--success-color)" : "var(--error-color)" }}>
          {proposal.preflightPassed ? " 通过" : " 未通过"}
        </strong>
      </p>
    )}

    {proposal.warnings.length > 0 && (
      <ul style={styles.warnings}>
        {proposal.warnings.map(warning => <li key={warning}>{warning}</li>)}
      </ul>
    )}

    <p style={styles.guardNote}>
      这是执行前提案；只有通过下方工程审批后，现有 WorkflowGuard 才允许进入真实求解。
    </p>
  </div>
);

function sourceStyle(source: EngineeringProposalSource): React.CSSProperties {
  return {
    ...styles.source,
    ...(source === "PROJECT_WORKSPACE" || source === "VERIFIED_RUN"
      ? { color: "var(--primary-color)", borderColor: "var(--primary-color)" }
      : source === "SYSTEM_DEFAULT"
        ? { color: "var(--warning-color)" }
        : {})
  };
}

const styles: Record<string, React.CSSProperties> = {
  valueLine: { display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" },
  source: {
    display: "inline-flex",
    alignItems: "center",
    padding: "1px 5px",
    border: "1px solid var(--border-color)",
    borderRadius: 999,
    color: "var(--text-secondary)",
    fontSize: 10,
    lineHeight: 1.5
  },
  warnings: { margin: "10px 0 0", paddingLeft: 18, color: "var(--warning-color)", fontSize: 11, lineHeight: 1.6 },
  guardNote: { margin: "10px 0 0", color: "var(--text-secondary)", fontSize: 11, lineHeight: 1.6 }
};
'''

TASK_PROPOSAL_CARD_TEST = r'''import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { TaskProposalCard } from "./TaskProposalCard";


describe("TaskProposalCard", () => {
  it("区分用户明确、Workspace 继承和系统默认来源", () => {
    const markup = renderToStaticMarkup(<TaskProposalCard proposal={{
      schemaVersion: "1.0",
      proposalId: "etp_1",
      taskType: "DAMPER_OPTIMIZATION",
      summary: "沿用当前工程做 FULL 黏滞阻尼优化",
      proposalState: "READY_FOR_CONFIRMATION",
      fields: [
        { field: "solver", label: "求解器", value: "ANSYS", source: "USER_CONFIRMED", rawSource: "USER_SPECIFIED", inherited: false },
        { field: "loadKind", label: "荷载类型", value: "EARTHQUAKE", source: "PROJECT_WORKSPACE", rawSource: "PROJECT_WORKSPACE", inherited: true },
        { field: "budget", label: "计算预算", value: { doeDesignCount: 15 }, source: "SYSTEM_DEFAULT", rawSource: "DEFAULT", inherited: false }
      ],
      unresolvedFields: [],
      inheritedFields: ["loadKind"],
      warnings: ["部分配置继承自当前 Project Workspace 或已验证历史；本轮用户明确输入始终优先。"],
      proposedActions: ["冻结优化配置", "等待用户审批"],
      approvalRequired: true,
      readyForApproval: true,
      preflightPassed: true
    }} />);

    expect(markup).toContain("工程任务提案");
    expect(markup).toContain("用户已明确");
    expect(markup).toContain("Project Workspace");
    expect(markup).toContain("系统默认");
    expect(markup).toContain("等待用户审批");
    expect(markup).toContain("WorkflowGuard");
  });

  it("缺字段时明确标记待补充，不冒充可执行提案", () => {
    const markup = renderToStaticMarkup(<TaskProposalCard proposal={{
      schemaVersion: "1.0",
      proposalId: "etp_2",
      taskType: "ANALYSIS",
      summary: "需要补充分析配置",
      proposalState: "NEEDS_CLARIFICATION",
      fields: [],
      unresolvedFields: ["solver", "responseIds"],
      inheritedFields: [],
      warnings: ["仍需补充：solver, responseIds。"],
      proposedActions: ["确认任务配置"],
      approvalRequired: true,
      readyForApproval: false,
      preflightPassed: null
    }} />);

    expect(markup).toContain("待补充信息");
    expect(markup).toContain("solver");
    expect(markup).toContain("responseIds");
  });
});
'''

write_new('momo_agent/backend/app/services/agent_task_proposal.py', PROPOSAL_SERVICE)
write_new('momo_agent/backend/tests/test_agent_task_proposal.py', PROPOSAL_TEST)
write_new('platform-ui/src/pages/chat/cards/TaskProposalCard.tsx', TASK_PROPOSAL_CARD)
write_new('platform-ui/src/pages/chat/cards/TaskProposalCard.test.tsx', TASK_PROPOSAL_CARD_TEST)

replace_once(
    'momo_agent/backend/app/services/agent_service.py',
    "from app.services.agent_project_context import engineering_project_context_service\n",
    "from app.services.agent_project_context import engineering_project_context_service\nfrom app.services.agent_task_proposal import build_engineering_task_proposal\n",
)
replace_once(
    'momo_agent/backend/app/services/agent_service.py',
    "            **({'agentRuntime': agent_runtime} if agent_runtime else {}),\n            'createdAt': now,\n            'updatedAt': now,\n        }\n        if load_import:\n",
    "            **({'agentRuntime': agent_runtime} if agent_runtime else {}),\n            'createdAt': now,\n            'updatedAt': now,\n        }\n        run['taskProposal'] = build_engineering_task_proposal(run)\n        if load_import:\n",
)
replace_once(
    'momo_agent/backend/app/services/agent_service.py',
    "        if prepared.plan:\n            run['plan'] = list(prepared.plan)\n        if agent.task_type != 'ANALYSIS' and prepared.preflight:\n",
    "        if prepared.plan:\n            run['plan'] = list(prepared.plan)\n        run['taskProposal'] = build_engineering_task_proposal(run)\n        if agent.task_type != 'ANALYSIS' and prepared.preflight:\n",
)

replace_once(
    'platform-ui/src/api/agentApi.ts',
    'export type AgentTaskType = "AUTO" | "ANALYSIS" | "DAMPER_OPTIMIZATION" | "DAMPER_COMPARISON" | "DAMPER_PARAMETER_SWEEP" | "LOAD_IMPORT" | "FULL_OPTIMIZATION";\nexport type AgentRunTaskType = AgentTaskType | "CONVERSATION" | "INQUIRY" | "UNSUPPORTED" | "LLM_UNAVAILABLE" | "WORKFLOW_HARNESS";\n',
    'export type AgentTaskType = "AUTO" | "ANALYSIS" | "DAMPER_OPTIMIZATION" | "DAMPER_COMPARISON" | "DAMPER_PARAMETER_SWEEP" | "LOAD_IMPORT";\n/** FULL_OPTIMIZATION 只保留为历史 Run 身份；新请求统一使用 DAMPER_OPTIMIZATION + optimizationProfile=FULL。 */\nexport type AgentRunTaskType = AgentTaskType | "FULL_OPTIMIZATION" | "CONVERSATION" | "INQUIRY" | "UNSUPPORTED" | "LLM_UNAVAILABLE" | "WORKFLOW_HARNESS";\n',
)
proposal_types = r'''export type EngineeringProposalSource =
  | "USER_CONFIRMED"
  | "PROJECT_WORKSPACE"
  | "VERIFIED_RUN"
  | "VERIFIED_TEMPLATE"
  | "FILE_DERIVED"
  | "SYSTEM_DEFAULT"
  | "UNKNOWN";

export interface EngineeringProposalField {
  field: string;
  label: string;
  value: unknown;
  source: EngineeringProposalSource;
  rawSource?: string | null;
  inherited: boolean;
}

export interface EngineeringTaskProposal {
  schemaVersion: "1.0";
  proposalId: string;
  taskType: string;
  summary: string;
  proposalState: "NEEDS_CLARIFICATION" | "NEEDS_INPUT" | "READY_FOR_CONFIRMATION" | "BLOCKED";
  fields: EngineeringProposalField[];
  unresolvedFields: string[];
  inheritedFields: string[];
  warnings: string[];
  proposedActions: string[];
  approvalRequired: boolean;
  readyForApproval: boolean;
  preflightPassed?: boolean | null;
}

'''
replace_once(
    'platform-ui/src/api/agentApi.ts',
    'export interface AgentRun {\n',
    proposal_types + 'export interface AgentRun {\n',
)
replace_once(
    'platform-ui/src/api/agentApi.ts',
    '  workflowContract?: Record<string, unknown>;\n',
    '  workflowContract?: Record<string, unknown>;\n  taskProposal?: EngineeringTaskProposal;\n',
)

replace_once(
    'platform-ui/src/pages/chat/ChatPage.tsx',
    'import { PlanCard } from "./cards/PlanCard";\n',
    'import { PlanCard } from "./cards/PlanCard";\nimport { TaskProposalCard } from "./cards/TaskProposalCard";\n',
)
replace_once(
    'platform-ui/src/pages/chat/ChatPage.tsx',
    '      || resultSummary?.inquiryMetrics?.length\n      || resultSummary?.inquiryTopsis?.length\n      || cardRun.figureArtifactIds?.length\n',
    '      || resultSummary?.inquiryMetrics?.length\n      || resultSummary?.inquiryTopsis?.length\n      || resultSummary?.inquiryRunComparison\n      || cardRun.figureArtifactIds?.length\n',
)
replace_once(
    'platform-ui/src/pages/chat/ChatPage.tsx',
    '        {cardRun.workflowContract || cardRun.plan || cardRun.intent ? <PlanCard run={cardRun} /> : null}\n',
    '        {cardRun.taskProposal\n          ? <TaskProposalCard proposal={cardRun.taskProposal} />\n          : cardRun.workflowContract || cardRun.plan || cardRun.intent\n            ? <PlanCard run={cardRun} />\n            : null}\n',
)
replace_once(
    'platform-ui/src/pages/chat/ChatPage.tsx',
    '                        runHistory[message.runId]?.resultSummary?.inquiryMetrics?.length\n                        || runHistory[message.runId]?.resultSummary?.inquiryTopsis?.length\n                      )\n',
    '                        runHistory[message.runId]?.resultSummary?.inquiryMetrics?.length\n                        || runHistory[message.runId]?.resultSummary?.inquiryTopsis?.length\n                        || runHistory[message.runId]?.resultSummary?.inquiryRunComparison\n                      )\n',
)
replace_once(
    'platform-ui/src/pages/chat/ChatPage.tsx',
    '                  run.resultSummary?.inquiryMetrics?.length || run.resultSummary?.inquiryTopsis?.length\n                ) ? (\n',
    '                  run.resultSummary?.inquiryMetrics?.length\n                  || run.resultSummary?.inquiryTopsis?.length\n                  || run.resultSummary?.inquiryRunComparison\n                ) ? (\n',
)

replace_once(
    'platform-ui/src/pages/chat/cards/ResultCard.tsx',
    'import { TimeseriesSection } from "./TimeseriesSection";\n',
    'import { TimeseriesSection } from "./TimeseriesSection";\nimport RunComparisonCard from "../../agent/RunComparisonCard";\n',
)
replace_once(
    'platform-ui/src/pages/chat/cards/ResultCard.tsx',
    '  const inquiryTopsisWeights = summary.inquiryTopsisWeights;\n  const isInquiry = run.taskType === "INQUIRY";\n',
    '  const inquiryTopsisWeights = summary.inquiryTopsisWeights;\n  const inquiryRunComparison = summary.inquiryRunComparison;\n  const isInquiry = run.taskType === "INQUIRY";\n',
)
replace_once(
    'platform-ui/src/pages/chat/cards/ResultCard.tsx',
    '      {isInquiry && !inquiryPending && summary.message && (\n',
    '      {isInquiry && inquiryRunComparison && (\n        <RunComparisonCard comparison={inquiryRunComparison} />\n      )}\n\n      {isInquiry && !inquiryPending && summary.message && (\n',
)

print('PR7 migration applied successfully')
