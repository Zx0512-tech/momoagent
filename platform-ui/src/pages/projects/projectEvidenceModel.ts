import type {
  ProjectEvidenceBundle,
  ProjectEvidenceClaim,
  ProjectEvidenceIntegrityState,
  ProjectEvidenceTrustState
} from "../../api/projectEvidenceApi";

export const TRUST_LABELS: Record<ProjectEvidenceTrustState, string> = {
  REAL_FEM: "真实有限元证据",
  VERIFIED: "已验证证据",
  LIMITED: "有限证据",
  NOT_VERIFIED: "未验证"
};

export const TRUST_DESCRIPTIONS: Record<ProjectEvidenceTrustState, string> = {
  REAL_FEM: "Run 的持久化合同满足 REAL_FEM 门槛；这不等同于当前 Artifact 已重新通过完整性检查。",
  VERIFIED: "Run 的持久化合同满足确定性/已验证证据门槛；当前文件完整性请同时查看 Integrity。",
  LIMITED: "Run 有部分 Artifact 或输出记录，但不满足完整 REAL_FEM/VERIFIED 门槛。",
  NOT_VERIFIED: "当前 Run 不能作为已验证工程结论来源。"
};

export const INTEGRITY_LABELS: Record<ProjectEvidenceIntegrityState, string> = {
  VALID: "Artifact 完整",
  MISSING_ARTIFACT: "Artifact 缺失",
  HASH_MISMATCH: "Hash 不一致",
  RUN_MISMATCH: "Run 归属不一致",
  NOT_CHECKED: "无 Artifact 可检查"
};

export const INTEGRITY_DESCRIPTIONS: Record<ProjectEvidenceIntegrityState, string> = {
  VALID: "当前登记的 Evidence Artifact 均可读取，Run 归属与 SHA256 校验通过。",
  MISSING_ARTIFACT: "至少一个 Run 声明的 Artifact 当前无法从登记存储中读取。",
  HASH_MISMATCH: "至少一个 Artifact 当前字节内容与登记 SHA256 不一致。",
  RUN_MISMATCH: "至少一个 Artifact 的登记 runId 与当前 Run 不一致。",
  NOT_CHECKED: "当前 Run 没有可用于完整性检查的已登记 Artifact。"
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
    integrityCounts: bundle.integrityCounts,
    claims: bundle.claims
  }, null, 2);
}

export function evidenceReportFileName(projectName: string): string {
  const normalized = projectName.trim().replace(/[\\/:*?"<>|\s]+/g, "-").replace(/^-+|-+$/g, "");
  return `${normalized || "engineering-project"}-evidence-report.json`;
}
