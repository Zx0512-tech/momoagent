import { describe, expect, it } from "vitest";

import type { ProjectEvidenceBundle, ProjectEvidenceClaim } from "../../api/projectEvidenceApi";
import {
  buildEvidenceReportDownload,
  claimInterpretation,
  evidenceReportFileName,
  INTEGRITY_LABELS,
  TRUST_LABELS
} from "./projectEvidenceModel";

const baseClaim: ProjectEvidenceClaim = {
  claimId: "claim-1",
  claimType: "COMPARISON_METRIC",
  label: "塔底剪力",
  value: 100,
  unit: "kN",
  compatibility: "DIRECT",
  source: { evidence: { artifactId: "art_1" } }
};

describe("projectEvidenceModel", () => {
  it("keeps CROSS_SOLVER evidence validation-only", () => {
    expect(claimInterpretation({ ...baseClaim, compatibility: "CROSS_SOLVER" })).toContain("不用于方案优劣排序");
  });

  it("labels limited compatibility without improvement semantics", () => {
    expect(claimInterpretation({ ...baseClaim, compatibility: "LIMITED" })).toContain("不解释为改善幅度");
    expect(claimInterpretation({ ...baseClaim, compatibility: "NOT_COMPARABLE" })).toContain("不解释为改善幅度");
  });

  it("exports deterministic trust and integrity projections plus evidence claims", () => {
    const bundle: ProjectEvidenceBundle = {
      schemaVersion: "1.1",
      generatedAt: "2026-09-02T00:00:00Z",
      projectId: "agp_1",
      projectName: "Bridge / Test",
      workspaceRevision: 3,
      workspaceSnapshot: { solver: "OPENSEESPY_INPROC" },
      trustCounts: { REAL_FEM: 1, VERIFIED: 0, LIMITED: 0, NOT_VERIFIED: 0 },
      integrityCounts: { VALID: 1, MISSING_ARTIFACT: 0, HASH_MISMATCH: 0, RUN_MISMATCH: 0, NOT_CHECKED: 0 },
      runs: [],
      claims: [baseClaim],
      projectReport: {
        schemaVersion: "1.1",
        reportType: "PROJECT_EVIDENCE_INDEX",
        projectId: "agp_1",
        projectName: "Bridge / Test",
        workspaceRevision: 3,
        workspaceSnapshot: { solver: "OPENSEESPY_INPROC" },
        runCount: 1,
        trustedRunCount: 1,
        integrityValidRunCount: 1,
        claimCount: 1,
        evidenceIndex: [],
        limitations: ["exact numbers require evidence"]
      }
    };

    const exported = JSON.parse(buildEvidenceReportDownload(bundle));
    expect(exported.generatedAt).toBe(bundle.generatedAt);
    expect(exported.trustCounts.REAL_FEM).toBe(1);
    expect(exported.integrityCounts.VALID).toBe(1);
    expect(exported.claims).toEqual([baseClaim]);
    expect(exported.runs).toBeUndefined();
  });

  it("distinguishes persisted trust from current artifact integrity", () => {
    expect(TRUST_LABELS.REAL_FEM).toContain("真实有限元");
    expect(INTEGRITY_LABELS.HASH_MISMATCH).toContain("Hash");
    expect(INTEGRITY_LABELS.MISSING_ARTIFACT).toContain("缺失");
  });

  it("sanitizes project name for report downloads", () => {
    expect(evidenceReportFileName(" Bridge / Test: 01 ")).toBe("Bridge-Test-01-evidence-report.json");
  });
});
