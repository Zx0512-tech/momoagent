import { ShieldCheck } from "lucide-react";
import type { AgentApproval } from "../../../api/agentApi";
import { cardStyles } from "./cardStyles";

const actionTitles: Record<string, string> = {
  STANDARDIZE_LOAD: "荷载标准化审批",
  RUN_SOLVER: "求解执行审批",
  RUN_FULL_OPTIMIZATION: "整单优化审批",
  RUN_ENGINEERING_WORKFLOW: "工程工作流审批",
  RUN_DAMPER_COMPARISON: "阻尼器对比审批",
  RUN_DAMPER_PARAMETER_SWEEP: "阻尼器参数批量审批"
};

type ApprovalCardProps = {
  approval: AgentApproval;
  busy: boolean;
  onDecide: (approved: boolean) => void;
};

const statusLabels: Record<string, string> = {
  APPROVED: "已批准，任务已进入执行队列",
  REJECTED: "已拒绝，本次任务未执行",
  SUPERSEDED: "方案已替代，以后续审批为准",
  PENDING: "等待你的确认"
};

/** 对话中的审批消息；冻结参数仍保存在后端，仅供审计，不在界面展开。 */
export const ApprovalCard = ({ approval, busy, onDecide }: ApprovalCardProps) => {
  const pending = approval.status === "PENDING";
  return (
    <div style={cardStyles.approvalInline} role="group" aria-label="工程执行审批">
      <div style={cardStyles.approvalLine}>
        <ShieldCheck size={15} aria-hidden="true" />
        <strong>{actionTitles[approval.action] ?? "执行审批"}</strong>
        <span style={pending ? cardStyles.badgeWarning : cardStyles.badge}>{statusLabels[approval.status] ?? approval.status}</span>
      </div>
      <p style={cardStyles.summary}>{approval.summary}</p>
      {pending && (
        <div style={cardStyles.actions}>
          <button className="btn btn-primary" disabled={busy} onClick={() => onDecide(true)}>
            批准并执行
          </button>
          <button className="btn btn-secondary" disabled={busy} onClick={() => onDecide(false)}>
            拒绝
          </button>
        </div>
      )}
    </div>
  );
};
