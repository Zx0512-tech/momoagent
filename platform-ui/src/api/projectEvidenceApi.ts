const API_BASE = (import.meta.env.VITE_API_BASE || "").replace(/\/$/, "");

export type ProjectEvidenceTrustState = "REAL_FEM" | "VERIFIED" | "LIMITED" | "NOT_VERIFIED";
export type ProjectEvidenceIntegrityState = "VALID" | "MISSING_ARTIFACT" | "HASH_MISMATCH" | "RUN_MISMATCH" | "NOT_CHECKED";

export interface ProjectEvidenceArtifactRef {
  artifactId: string;
  role: "REPORT" | "OUTPUT_MANIFEST" | "FIGURE" | "RESULT" | "REGISTERED" | "CLAIM_EVIDENCE";
  name?: string;
  kind?: string;
  sourceRunId?: string;
}

export interface ProjectEvidenceIntegrityIssue {
  code: "MISSING_ARTIFACT" | "HASH_MISMATCH" | "RUN_MISMATCH" | string;
  artifactId: string;
  expectedRunId?: string;
  actualRunId?: string;
  expectedSha256?: string;
  actualSha256?: string;
}

export interface ProjectEvidenceClaim {
  claimId: string;
  claimType: "COMPARISON_METRIC";
  label: string;
  value: number;
  unit: string;
  compatibility: "DIRECT" | "CROSS_SOLVER" | "LIMITED" | "NOT_COMPARABLE" | string;
  source: {
    comparisonRunId?: string;
    targetRunId?: string;
    targetKey?: string;
    metricId?: string;
    evidence: Record<string, unknown>;
  };
}

export interface ProjectEvidenceRun {
  runId: string;
  sessionId?: string;
  taskType?: string;
  status?: string;
  currentStage?: string;
  createdAt?: string;
  updatedAt?: string;
  trustState: ProjectEvidenceTrustState;
  integrityState: ProjectEvidenceIntegrityState;
  integrity: {
    state: ProjectEvidenceIntegrityState;
    checkedArtifactCount: number;
    issues: ProjectEvidenceIntegrityIssue[];
  };
  evidenceMode?: string | null;
  solverVersionProfile?: {
    solver?: { name?: string; version?: string; versionSource?: string };
    sdk?: { package?: string; version?: string };
    responseContract?: { id?: string; version?: string; description?: string };
  } | null;
  inputProvenance: Array<{ field: string; source: string; value: unknown }>;
  contractHash?: string | null;
  artifacts: ProjectEvidenceArtifactRef[];
  claims: ProjectEvidenceClaim[];
  narrativeSummary?: string | null;
}

export interface ProjectEvidenceReport {
  schemaVersion: "1.1";
  reportType: "PROJECT_EVIDENCE_INDEX";
  projectId: string;
  projectName: string;
  workspaceRevision: number;
  workspaceSnapshot: Record<string, unknown>;
  runCount: number;
  trustedRunCount: number;
  integrityValidRunCount: number;
  claimCount: number;
  evidenceIndex: Array<{
    runId: string;
    trustState: ProjectEvidenceTrustState;
    integrityState: ProjectEvidenceIntegrityState;
    evidenceMode?: string | null;
    artifactIds: string[];
    claimIds: string[];
  }>;
  limitations: string[];
}

export interface ProjectEvidenceBundle {
  schemaVersion: "1.1";
  generatedAt: string;
  projectId: string;
  projectName: string;
  workspaceRevision: number;
  workspaceSnapshot: Record<string, unknown>;
  trustCounts: Record<ProjectEvidenceTrustState, number>;
  integrityCounts: Record<ProjectEvidenceIntegrityState, number>;
  runs: ProjectEvidenceRun[];
  claims: ProjectEvidenceClaim[];
  projectReport: ProjectEvidenceReport;
}

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE}/api/v1${path}`, {
    headers: { "Content-Type": "application/json" }
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(payload?.error?.message || payload?.detail?.message || `请求失败（HTTP ${response.status}）`);
  }
  return response.json() as Promise<T>;
}

export const projectEvidenceApi = {
  getProjectEvidence(projectId: string): Promise<ProjectEvidenceBundle> {
    return getJson(`/agent/projects/${projectId}/evidence`);
  }
};
