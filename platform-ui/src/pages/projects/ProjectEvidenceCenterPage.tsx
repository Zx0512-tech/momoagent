import type React from "react";
import { useEffect, useMemo, useState } from "react";
import { FolderKanban, ShieldCheck } from "lucide-react";
import { Link, useSearchParams } from "react-router-dom";

import { agentApi, type EngineeringProjectSummary } from "../../api/agentApi";
import ProjectEvidencePanel from "./ProjectEvidencePanel";

export default function ProjectEvidenceCenterPage() {
  const [projects, setProjects] = useState<EngineeringProjectSummary[]>([]);
  const [searchParams, setSearchParams] = useSearchParams();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string>();
  const selectedProjectId = searchParams.get("project") || "";

  useEffect(() => {
    let active = true;
    setLoading(true);
    agentApi.listProjects()
      .then(response => {
        if (!active) return;
        const next = response.data ?? [];
        setProjects(next);
        if (!selectedProjectId && next.length > 0) {
          setSearchParams({ project: next[0].projectId }, { replace: true });
        }
      })
      .catch(reason => {
        if (active) setError(reason instanceof Error ? reason.message : "读取工程项目失败");
      })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [selectedProjectId, setSearchParams]);

  const selectedProject = useMemo(
    () => projects.find(project => project.projectId === selectedProjectId),
    [projects, selectedProjectId]
  );

  const selectProject = (projectId: string) => {
    setSearchParams(projectId ? { project: projectId } : {});
  };

  return (
    <div style={styles.page}>
      <header style={styles.header}>
        <div>
          <div style={styles.eyebrow}>ENGINEERING EVIDENCE</div>
          <h1 style={styles.title}>工程证据中心</h1>
          <p style={styles.subtitle}>
            在 Project 作用域内追溯 Run、Solver 版本、输入来源、Artifact 和显式 Evidence Claim。此页面不重新计算工程结果。
          </p>
        </div>
        {selectedProject && (
          <Link to={`/projects/${selectedProject.projectId}`} style={styles.projectLink}>
            <FolderKanban size={14} />返回工程工作台
          </Link>
        )}
      </header>

      <section style={styles.selectorCard}>
        <div style={styles.selectorTitle}><ShieldCheck size={15} />选择 Project</div>
        <select
          value={selectedProjectId}
          onChange={event => selectProject(event.target.value)}
          style={styles.select}
          disabled={loading}
        >
          <option value="">请选择工程项目</option>
          {projects.map(project => (
            <option key={project.projectId} value={project.projectId}>
              {project.name} · {project.runCount} Runs
            </option>
          ))}
        </select>
        {selectedProject && (
          <div style={styles.projectMeta}>
            <strong>{selectedProject.name}</strong>
            <span>Workspace rev. {selectedProject.workspaceRevision}</span>
            <span>{selectedProject.runCount} Runs</span>
            <span>{selectedProject.sessionCount} Sessions</span>
          </div>
        )}
      </section>

      {error && <div style={styles.error}>{error}</div>}
      {!loading && projects.length === 0 && <div style={styles.empty}>暂无工程项目，请先在工程项目工作台创建 Project。</div>}
      {selectedProjectId && <ProjectEvidencePanel projectId={selectedProjectId} />}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  page: { display: "flex", flexDirection: "column", gap: 14, minHeight: "100%" },
  header: { display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 20 },
  eyebrow: { fontSize: 10, letterSpacing: "0.14em", color: "var(--primary-color)", fontWeight: 700 },
  title: { margin: "4px 0 6px", fontSize: 24 },
  subtitle: { margin: 0, maxWidth: 780, color: "var(--text-secondary)", fontSize: 12, lineHeight: 1.7 },
  projectLink: { display: "inline-flex", alignItems: "center", gap: 6, padding: "7px 10px", border: "1px solid var(--border-color)", borderRadius: 6, color: "var(--primary-color)", textDecoration: "none", fontSize: 11 },
  selectorCard: { display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap", padding: 12, background: "var(--bg-secondary)", border: "1px solid var(--border-color)", borderRadius: 10 },
  selectorTitle: { display: "flex", alignItems: "center", gap: 6, fontSize: 12, fontWeight: 700 },
  select: { minWidth: 260, border: "1px solid var(--border-color)", borderRadius: 6, padding: "7px 9px", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 11 },
  projectMeta: { display: "flex", alignItems: "center", gap: 10, color: "var(--text-secondary)", fontSize: 10 },
  error: { padding: 10, border: "1px solid var(--error-color)", borderRadius: 6, color: "var(--error-color)", fontSize: 11 },
  empty: { padding: 28, textAlign: "center", color: "var(--text-muted)", background: "var(--bg-secondary)", border: "1px solid var(--border-color)", borderRadius: 10 }
};
