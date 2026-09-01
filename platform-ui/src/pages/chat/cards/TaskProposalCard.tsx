import type React from "react";
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
  PLANNING: "正在生成受控计划",
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
