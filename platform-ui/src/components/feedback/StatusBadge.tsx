import React from "react";
import type { JobStatus } from "../../api/types";
import { Play, CheckCircle, XCircle, AlertTriangle, HelpCircle } from "lucide-react";

interface StatusBadgeProps {
  status: JobStatus | string;
}

export const StatusBadge: React.FC<StatusBadgeProps> = ({ status }) => {
  let text = status;
  let className = "badge-secondary";
  let icon = <HelpCircle size={12} />;

  switch (status) {
    case "QUEUED":
      text = "队列排队中";
      className = "badge-warning";
      icon = <AlertTriangle size={12} />;
      break;
    case "RUNNING":
      text = "计算求解中";
      className = "badge-info pulse";
      icon = <Play size={12} />;
      break;
    case "SUCCEEDED":
    case "PASS":
      text = status === "PASS" ? "指标通过" : "计算成功";
      className = "badge-success";
      icon = <CheckCircle size={12} />;
      break;
    case "FAILED":
    case "FAIL":
      text = status === "FAIL" ? "指标未过" : "计算失败";
      className = "badge-error";
      icon = <XCircle size={12} />;
      break;
    case "CANCELLED":
      text = "任务已取消";
      className = "badge-secondary";
      icon = <XCircle size={12} />;
      break;
  }

  return (
    <span className={`badge ${className}`} style={{ gap: 4 }}>
      {icon}
      <span>{text}</span>
    </span>
  );
};
