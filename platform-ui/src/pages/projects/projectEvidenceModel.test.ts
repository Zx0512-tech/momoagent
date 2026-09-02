import { describe, expect, it } from "vitest";

import type { ProjectEvidenceBundle, ProjectEvidenceClaim } from "../../api/projectEvidenceApi";
import {
  buildEvidenceReportDownload,
  claimInterpretation,
  evidenceReportFileName,
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

  it("exports only deterministic report projection plus evidence claims", () => {
    const bundle: ProjectEvidenceBundle = {
      schemaVersion: "1.0",
      generatedAt: "2026-09-02T00:00:00Z",
      projectId: "agp_1",
      projectName: "Bridge / Test",
      workspaceRevision: 3,
      workspaceSnapshot: { solver: "OPENSEESPY_INPROC" },
      trustCounts: { REAL_FEM: 1, VERIFIED: 0, LIMITED: 0, NOT_VERIFIED: 0 },
      runs: [],
      claims: [baseClaim],
      projectReport: {
        schemaVersion: "1.0",
        reportType: "PROJECT_EVIDENCE_INDEX",
        projectId: "agp_1",
        projectName: "Bridge / Test",
        workspaceRevision: 3,
        workspaceSnapshot: { solver: "OPENSEESPY_INPROC" },
        runCount: 1,
        trustedRunCount: 1,
        claimCount: 1,
        evidenceIndex: [],
        limitations: ["exact numbers require evidence"]
      }
    };

    const exported = JSON.parse(buildEvidenceReportDownload(bundle));
    expect(exported.generatedAt).toBe(bundle.generatedAt);
    expect(exported.trustCounts.REAL_FEM).toBe(1);
    expect(exported.claims).toEqual([baseClaim]);
    expect(exported.runs).toBeUndefined();
  });

  it("sanitizes project name for report downloads", () => {
    expect(evidenceReportFileName(" Bridge / Test: 01 ")).toBe("Bridge-Test-01-evidence-report.json");
    expect(TRUST_LABELS.REAL_FEM).toContain("真实有限元");
  });
});
