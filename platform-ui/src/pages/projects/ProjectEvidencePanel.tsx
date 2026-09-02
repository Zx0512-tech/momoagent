import type React from "react";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Download,
  FileCheck2,
  FileText,
  RefreshCw,
  ShieldCheck
} from "lucide-react";

import { agentApi } from "../../api/agentApi";
import {
  projectEvidenceApi,
  type ProjectEvidenceBundle,
  type ProjectEvidenceRun,
  type ProjectEvidenceTrustState
} from "../../api/projectEvidenceApi";
import {
  buildEvidenceReportDownload,
  claimInterpretation,
  evidenceReportFileName,
  TRUST_DESCRIPTIONS,
  TRUST_LABELS
} from "./projectEvidenceModel";

const TRUST_ORDER: ProjectEvidenceTrustState[] = ["REAL_FEM", "VERIFIED", "LIMITED", "NOT_VERIFIED"];

function formatTime(value?: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function trustTone(state: ProjectEvidenceTrustState): React.CSSProperties {
  if (state === "REAL_FEM" || state === "VERIFIED") return { color: "var(--success-color)" };
  if (state === "LIMITED") return { color: "var(--warning-color)" };
  return { color: "var(--text-muted)" };
}

function downloadJson(content: string, fileName: string) {
  const blob = new Blob([content], { type: "application/json;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = fileName;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

function RunEvidenceCard({ run }: { run: ProjectEvidenceRun }) {
  const profile = run.solverVersionProfile;
  return (
    <article style={styles.runCard}>
      <div style={styles.runHeader}>
        <div>
          <div style={styles.runTitleRow}>
            <code style={styles.code}>{run.runId}</code>
            <strong style={trustTone(run.trustState)}>{TRUST_LABELS[run.trustState]}</strong>
          </div>
          <div style={styles.meta}>
            {run.taskType || "—"} · {run.status || "—"} · {formatTime(run.updatedAt || run.createdAt)}
          </div>
        </div>
        <span style={styles.evidenceMode}>{run.evidenceMode || "NO_EVIDENCE_MODE"}</span>
      </div>

      <p style={styles.trustDescription}>{TRUST_DESCRIPTIONS[run.trustState]}</p>

      <div style={styles.detailGrid}>
        <div>
          <span style={styles.label}>Solver</span>
          <strong>{profile?.solver?.name || "—"} {profile?.solver?.version || ""}</strong>
          {profile?.sdk?.package && <div style={styles.meta}>{profile.sdk.package} {profile.sdk.version}</div>}
        </div>
        <div>
          <span style={styles.label}>Response Contract</span>
          <strong>{profile?.responseContract?.id || "—"}</strong>
          {profile?.responseContract?.version && <div style={styles.meta}>v{profile.responseContract.version}</div>}
        </div>
        <div>
          <span style={styles.label}>Contract Hash</span>
          <code style={styles.code}>{run.contractHash || "—"}</code>
        </div>
        <div>
          <span style={styles.label}>Artifacts</span>
          <strong>{run.artifacts.length}</strong>
        </div>
      </div>

      {run.inputProvenance.length > 0 && (
        <div style={styles.block}>
          <div style={styles.label}>输入来源</div>
          <div style={styles.provenanceList}>
            {run.inputProvenance.map(item => (
              <div key={item.field} style={styles.provenanceItem}>
                <code style={styles.code}>{item.field}</code>
                <span>{item.source}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      <div style={styles.block}>
        <div style={styles.label}>Evidence Artifacts</div>
        {run.artifacts.length === 0 ? (
          <div style={styles.muted}>当前 Run 没有已登记 Evidence Artifact。</div>
        ) : (
          <div style={styles.artifactList}>
            {run.artifacts.map(item => (
              <a
                key={item.artifactId}
                href={agentApi.artifactDownloadUrl(item.artifactId)}
                style={styles.artifactLink}
                title={`${item.role}${item.kind ? ` · ${item.kind}` : ""}`}
              >
                <FileText size={13} />
                <span>{item.name || item.artifactId}</span>
                <small style={styles.role}>{item.role}</small>
              </a>
            ))}
          </div>
        )}
      </div>

      {run.claims.length > 0 && (
        <div style={styles.block}>
          <div style={styles.label}>Evidence-backed Claims</div>
          <div style={styles.claimList}>
            {run.claims.map(claim => (
              <div key={claim.claimId} style={styles.claimCard}>
                <div style={styles.claimValue}>{claim.label}: {claim.value} {claim.unit}</div>
                <div style={styles.meta}>{claimInterpretation(claim)}</div>
                <code style={styles.claimSource}>{JSON.stringify(claim.source.evidence)}</code>
              </div>
            ))}
          </div>
        </div>
      )}

      {run.narrativeSummary && (
        <div style={styles.narrativeBox}>
          <AlertTriangle size={14} />
          <div>
            <strong>Run 说明（非数值证据真源）</strong>
            <p>{run.narrativeSummary}</p>
          </div>
        </div>
      )}
    </article>
  );
}

export default function ProjectEvidencePanel({ projectId }: { projectId: string }) {
  const [bundle, setBundle] = useState<ProjectEvidenceBundle>();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string>();
  const [trustFilter, setTrustFilter] = useState<ProjectEvidenceTrustState | "ALL">("ALL");

  const load = useCallback(async () => {
    setLoading(true);
    setError(undefined);
    try {
      setBundle(await projectEvidenceApi.getProjectEvidence(projectId));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "读取 Project Evidence 失败");
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => { void load(); }, [load]);

  const visibleRuns = useMemo(() => {
    const runs = bundle?.runs ?? [];
    return trustFilter === "ALL" ? runs : runs.filter(run => run.trustState === trustFilter);
  }, [bundle?.runs, trustFilter]);

  const exportReport = () => {
    if (!bundle) return;
    downloadJson(buildEvidenceReportDownload(bundle), evidenceReportFileName(bundle.projectName));
  };

  return (
    <section style={styles.card} aria-label="Engineering Evidence Center">
      <div style={styles.toolbar}>
        <div>
          <div style={styles.sectionTitle}><ShieldCheck size={15} />Engineering Evidence Center</div>
          <p style={styles.subtitle}>
            这里展示的是 Project → Run → Artifact → Evidence 的确定性索引。没有显式 Evidence 映射的数字不会被提升为工程 Claim。
          </p>
        </div>
        <div style={styles.actions}>
          <button type="button" style={styles.secondaryButton} onClick={() => void load()} disabled={loading}>
            <RefreshCw size={13} />{loading ? "刷新中…" : "刷新证据"}
          </button>
          <button type="button" style={styles.primaryButton} onClick={exportReport} disabled={!bundle}>
            <Download size={13} />导出 Evidence Report
          </button>
        </div>
      </div>

      {error && <div style={styles.error}>{error}</div>}
      {!bundle && loading && <div style={styles.empty}>正在构建 Project Evidence Index…</div>}

      {bundle && (
        <>
          <div style={styles.summaryGrid}>
            {TRUST_ORDER.map(state => (
              <button
                key={state}
                type="button"
                style={{ ...styles.summaryCard, ...(trustFilter === state ? styles.summaryCardActive : {}) }}
                onClick={() => setTrustFilter(current => current === state ? "ALL" : state)}
              >
                <span style={styles.label}>{TRUST_LABELS[state]}</span>
                <strong style={{ ...styles.summaryValue, ...trustTone(state) }}>{bundle.trustCounts[state] ?? 0}</strong>
              </button>
            ))}
            <div style={styles.summaryCard}>
              <span style={styles.label}>Evidence Claims</span>
              <strong style={styles.summaryValue}>{bundle.claims.length}</strong>
            </div>
          </div>

          <div style={styles.reportCard}>
            <div style={styles.reportTitle}><FileCheck2 size={14} />Project Evidence Report</div>
            <div style={styles.reportStats}>
              <span>{bundle.projectReport.runCount} Runs</span>
              <span>{bundle.projectReport.trustedRunCount} Trusted Runs</span>
              <span>{bundle.projectReport.claimCount} Evidence Claims</span>
              <span>Workspace rev. {bundle.projectReport.workspaceRevision}</span>
            </div>
            <div style={styles.limitations}>
              {bundle.projectReport.limitations.map(item => <div key={item}>• {item}</div>)}
            </div>
          </div>

          <div style={styles.filterRow}>
            <span style={styles.muted}>显示 {visibleRuns.length} / {bundle.runs.length} Runs</span>
            {trustFilter !== "ALL" && (
              <button type="button" style={styles.linkButton} onClick={() => setTrustFilter("ALL")}>清除筛选</button>
            )}
          </div>

          <div style={styles.runList}>
            {visibleRuns.length === 0 ? (
              <div style={styles.empty}>当前筛选条件下没有 Run Evidence。</div>
            ) : visibleRuns.map(run => <RunEvidenceCard key={run.runId} run={run} />)}
          </div>
        </>
      )}
    </section>
  );
}

const styles: Record<string, React.CSSProperties> = {
  card: { background: "var(--bg-secondary)", border: "1px solid var(--border-color)", borderRadius: 10, padding: 14 },
  toolbar: { display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 16 },
  sectionTitle: { display: "flex", alignItems: "center", gap: 7, fontSize: 13, fontWeight: 750 },
  subtitle: { margin: "5px 0 0", color: "var(--text-secondary)", fontSize: 11, lineHeight: 1.7, maxWidth: 760 },
  actions: { display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" },
  primaryButton: { display: "inline-flex", alignItems: "center", gap: 6, border: "none", borderRadius: 6, padding: "7px 10px", background: "var(--primary-color)", color: "var(--btn-primary-ink)", cursor: "pointer", fontSize: 11, fontWeight: 650 },
  secondaryButton: { display: "inline-flex", alignItems: "center", gap: 6, border: "1px solid var(--border-color)", borderRadius: 6, padding: "7px 10px", background: "var(--bg-secondary)", color: "var(--text-secondary)", cursor: "pointer", fontSize: 11 },
  error: { marginTop: 12, padding: 10, border: "1px solid var(--error-color)", borderRadius: 6, color: "var(--error-color)", fontSize: 11 },
  empty: { padding: 20, textAlign: "center", color: "var(--text-muted)", fontSize: 11 },
  summaryGrid: { marginTop: 14, display: "grid", gridTemplateColumns: "repeat(5, minmax(0, 1fr))", gap: 8 },
  summaryCard: { minWidth: 0, padding: 10, border: "1px solid var(--border-color)", borderRadius: 8, background: "var(--bg-primary)", textAlign: "left", color: "var(--text-primary)" },
  summaryCardActive: { borderColor: "var(--primary-color)", boxShadow: "0 0 0 1px var(--primary-color) inset" },
  summaryValue: { display: "block", marginTop: 5, fontSize: 20 },
  label: { display: "block", color: "var(--text-muted)", fontSize: 10, fontWeight: 650 },
  reportCard: { marginTop: 12, padding: 12, borderRadius: 8, border: "1px solid var(--border-color)", background: "var(--bg-primary)" },
  reportTitle: { display: "flex", alignItems: "center", gap: 6, fontSize: 12, fontWeight: 700 },
  reportStats: { display: "flex", gap: 12, flexWrap: "wrap", marginTop: 8, color: "var(--text-secondary)", fontSize: 10 },
  limitations: { marginTop: 9, color: "var(--text-muted)", fontSize: 10, lineHeight: 1.6 },
  filterRow: { display: "flex", justifyContent: "space-between", alignItems: "center", margin: "12px 0 8px" },
  muted: { color: "var(--text-muted)", fontSize: 10 },
  linkButton: { border: "none", background: "transparent", color: "var(--primary-color)", cursor: "pointer", fontSize: 10 },
  runList: { display: "flex", flexDirection: "column", gap: 10 },
  runCard: { padding: 12, borderRadius: 8, border: "1px solid var(--border-color)", background: "var(--bg-primary)" },
  runHeader: { display: "flex", justifyContent: "space-between", gap: 12 },
  runTitleRow: { display: "flex", alignItems: "center", gap: 8, fontSize: 11 },
  code: { fontFamily: "var(--font-mono, monospace)", fontSize: 10, wordBreak: "break-all" },
  meta: { color: "var(--text-muted)", fontSize: 10, marginTop: 3, lineHeight: 1.5 },
  evidenceMode: { padding: "3px 6px", borderRadius: 999, border: "1px solid var(--border-color)", color: "var(--text-secondary)", fontSize: 9, alignSelf: "flex-start" },
  trustDescription: { margin: "9px 0", color: "var(--text-secondary)", fontSize: 10, lineHeight: 1.6 },
  detailGrid: { display: "grid", gridTemplateColumns: "repeat(4, minmax(0, 1fr))", gap: 8, padding: 9, borderRadius: 7, background: "var(--bg-secondary)", fontSize: 10 },
  block: { marginTop: 10 },
  provenanceList: { display: "flex", gap: 6, flexWrap: "wrap", marginTop: 6 },
  provenanceItem: { display: "flex", alignItems: "center", gap: 5, padding: "4px 6px", borderRadius: 5, background: "var(--bg-secondary)", color: "var(--text-secondary)", fontSize: 9 },
  artifactList: { display: "flex", gap: 6, flexWrap: "wrap", marginTop: 6 },
  artifactLink: { display: "inline-flex", alignItems: "center", gap: 5, maxWidth: "100%", padding: "5px 7px", border: "1px solid var(--border-color)", borderRadius: 5, color: "var(--primary-color)", textDecoration: "none", fontSize: 10 },
  role: { color: "var(--text-muted)", fontSize: 8 },
  claimList: { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(250px, 1fr))", gap: 7, marginTop: 6 },
  claimCard: { padding: 8, borderRadius: 6, border: "1px solid var(--border-color)", background: "var(--bg-secondary)" },
  claimValue: { fontSize: 11, fontWeight: 700 },
  claimSource: { display: "block", marginTop: 5, color: "var(--text-muted)", fontSize: 8, whiteSpace: "pre-wrap", overflowWrap: "anywhere" },
  narrativeBox: { marginTop: 10, display: "flex", gap: 7, padding: 8, borderRadius: 6, border: "1px dashed var(--warning-color)", color: "var(--text-secondary)", fontSize: 9 },
};
