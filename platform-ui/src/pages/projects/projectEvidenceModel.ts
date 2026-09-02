import type {
  ProjectEvidenceBundle,
  ProjectEvidenceClaim,
  ProjectEvidenceTrustState
} from "../../api/projectEvidenceApi";

export const TRUST_LABELS: Record<ProjectEvidenceTrustState, string> = {
  REAL_FEM: "真实有限元证据",
  VERIFIED: "已验证证据",
  LIMITED: "有限证据",
  NOT_VERIFIED: "未验证"
};

export const TRUST_DESCRIPTIONS: Record<ProjectEvidenceTrustState, string> = {
  REAL_FEM: "Run 已成功完成，并存在 REAL_FEM 标记与已登记报告 Artifact。",
  VERIFIED: "Run 已成功完成，并存在确定性/已验证证据与已登记报告。",
  LIMITED: "Run 有部分 Artifact 或输出记录，但不满足完整 REAL_FEM/VERIFIED 门槛。",
  NOT_VERIFIED: "当前 Run 不能作为已验证工程结论来源。"
};

export function claimInterpretation(claim: ProjectEvidenceClaim): string {
  if (claim.compatibility === "CROSS_SOLVER") {
    return "跨求解器验证值：仅用于一致性核验，不用于方案优劣排序。";
  }
  if (claim.compatibility === "LIMITED" || claim.compatibility === "NOT_COMPARABLE") {
    return "兼容性受限：仅展示已登记值，不解释为改善幅度或方案排名。";
  }
  return "该数值来自已有显式 Evidence 映射。";
}

export function buildEvidenceReportDownload(bundle: ProjectEvidenceBundle): string {
  return JSON.stringify({
    ...bundle.projectReport,
    generatedAt: bundle.generatedAt,
    trustCounts: bundle.trustCounts,
    claims: bundle.claims
  }, null, 2);
}

export function evidenceReportFileName(projectName: string): string {
  const normalized = projectName.trim().replace(/[\\/:*?"<>|\s]+/g, "-").replace(/^-+|-+$/g, "");
  return `${normalized || "engineering-project"}-evidence-report.json`;
}
