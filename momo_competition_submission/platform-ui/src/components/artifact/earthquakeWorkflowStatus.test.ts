import { describe, expect, it } from "vitest";

import {
  isVerifiedAndAccepted,
  verificationLabel,
  workflowAcceptanceLabel
} from "./earthquakeWorkflowStatus";

describe("earthquake workflow status", () => {
  it("仅在真实执行与验收均通过时返回通过", () => {
    expect(isVerifiedAndAccepted({
      all_verified_execution: true,
      all_accepted: true
    })).toBe(true);
    expect(isVerifiedAndAccepted({
      all_verified_execution: true,
      all_accepted: false
    })).toBe(false);
  });

  it("优先使用后端给出的最终推荐状态", () => {
    expect(workflowAcceptanceLabel(null, null, "ACCEPTED")).toBe("最终推荐方案");
    expect(workflowAcceptanceLabel(
      { all_verified_execution: true, all_accepted: true },
      { all_verified_execution: true, all_accepted: true },
      "DIAGNOSTIC_NOT_ACCEPTED"
    )).toBe("诊断结果/未接受");
  });

  it("缺少后端最终状态时要求验证与复核同时通过", () => {
    const accepted = { all_verified_execution: true, all_accepted: true };
    const rejected = { all_verified_execution: true, all_accepted: false };

    expect(workflowAcceptanceLabel(accepted, accepted)).toBe("最终推荐方案");
    expect(workflowAcceptanceLabel(accepted, rejected)).toBe("诊断结果/未接受");
    expect(verificationLabel(rejected)).toBe("verified / not accepted");
  });
});
