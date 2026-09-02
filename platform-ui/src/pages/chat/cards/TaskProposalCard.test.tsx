import { renderToStaticMarkup } from "react-dom/server";
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
