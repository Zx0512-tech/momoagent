from __future__ import annotations

from pathlib import Path
from textwrap import dedent

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one match, found {count}: {old[:80]!r}")
    target.write_text(text.replace(old, new), encoding="utf-8")


def write(path: str, content: str) -> None:
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(dedent(content).lstrip(), encoding="utf-8")


replace_once(
    "platform-ui/src/App.tsx",
    'const ChatPage = lazy(() => import("./pages/chat/ChatPage").then(m => ({ default: m.ChatPage })));\n',
    'const ChatPage = lazy(() => import("./pages/chat/ChatPage").then(m => ({ default: m.ChatPage })));\n'
    'const ProjectWorkspacePage = lazy(() => import("./pages/projects/ProjectWorkspacePage"));\n',
)
replace_once(
    "platform-ui/src/App.tsx",
    '            <Route path="/agent" element={<Navigate to="/" replace />} />\n',
    '            <Route path="/agent" element={<Navigate to="/" replace />} />\n'
    '            <Route path="/projects" element={<ProjectWorkspacePage />} />\n'
    '            <Route path="/projects/:projectId" element={<ProjectWorkspacePage />} />\n',
)

replace_once(
    "platform-ui/src/components/layout/Sidebar.tsx",
    '  FolderOpen,\n  Wrench,\n',
    '  FolderOpen,\n  FolderKanban,\n  Wrench,\n',
)
replace_once(
    "platform-ui/src/components/layout/Sidebar.tsx",
    '    { name: "工程智能体", path: "/agent", icon: Bot },\n',
    '    { name: "工程智能体", path: "/agent", icon: Bot },\n'
    '    { name: "工程项目", path: "/projects", icon: FolderKanban },\n',
)

project_types = r'''
export interface EngineeringWorkspace {
  schemaVersion: number;
  modelArtifactId: string | null;
  modelFileName: string | null;
  modelSha256: string | null;
  solver: "ANSYS" | "OPENSEESPY_INPROC" | null;
  loadKind: "EARTHQUAKE" | "WIND" | "TRAFFIC" | "GENERIC_NODAL" | null;
  loadArtifactId: string | null;
  loadSha256: string | null;
  damperType: "VISCOUS" | "FRICTION" | "EDDY_CURRENT" | null;
  selectedLayoutId: string | null;
  responseIds: string[];
  optimizationProfile: "STANDARD" | "FULL" | "CUSTOM";
}

export type EngineeringWorkspacePatchPayload = Partial<Omit<EngineeringWorkspace, "schemaVersion">>;

export interface EngineeringProjectSummary {
  projectId: string;
  ownerId?: string;
  name: string;
  description: string;
  status: string;
  workspace: EngineeringWorkspace;
  workspaceRevision: number;
  sessionIds: string[];
  sessionCount: number;
  runCount: number;
  createdAt: string;
  updatedAt: string;
}

export interface EngineeringProjectSession {
  sessionId: string;
  title: string;
  createdAt?: string;
  updatedAt?: string;
  runCount?: number;
}

export interface EngineeringProjectRunSummary {
  runId: string;
  sessionId: string;
  taskType?: AgentRunTaskType;
  status: string;
  currentStage: string;
  createdAt?: string;
  updatedAt?: string;
  resultSummary?: AgentRun["resultSummary"];
}

export interface EngineeringProjectDetail extends EngineeringProjectSummary {
  sessions: EngineeringProjectSession[];
  runs: EngineeringProjectRunSummary[];
}

'''
replace_once(
    "platform-ui/src/api/agentApi.ts",
    'export interface AgentSessionDetail extends AgentSessionSummary {\n',
    project_types + 'export interface AgentSessionDetail extends AgentSessionSummary {\n',
)

project_methods = r'''  listProjects(): Promise<{ data: EngineeringProjectSummary[] }> {
    return request("GET", "/agent/projects");
  },

  createProject(name: string, description = ""): Promise<EngineeringProjectSummary> {
    return request("POST", "/agent/projects", { name, description });
  },

  getProject(projectId: string): Promise<EngineeringProjectDetail> {
    return request("GET", `/agent/projects/${projectId}`);
  },

  updateProjectWorkspace(
    projectId: string,
    workspace: EngineeringWorkspacePatchPayload
  ): Promise<EngineeringProjectSummary> {
    return request("PUT", `/agent/projects/${projectId}/workspace`, workspace);
  },

  createProjectSession(projectId: string, title = "新建工程智能体会话"): Promise<{ sessionId: string; projectId: string }> {
    return request("POST", `/agent/projects/${projectId}/sessions`, { title });
  },

  attachProjectSession(projectId: string, sessionId: string): Promise<{ projectId: string; sessionId: string; attached: true }> {
    return request("PUT", `/agent/projects/${projectId}/sessions/${sessionId}`);
  },

'''
replace_once(
    "platform-ui/src/api/agentApi.ts",
    'export const agentApi = {\n  createSession(title: string): Promise<{ sessionId: string }> {\n',
    'export const agentApi = {\n' + project_methods + '  createSession(title: string): Promise<{ sessionId: string }> {\n',
)

replace_once(
    "platform-ui/src/pages/chat/ChatPage.tsx",
    'import { useEffect, useMemo, useRef, useState } from "react";\n',
    'import { useEffect, useMemo, useRef, useState } from "react";\n'
    'import { Link, useSearchParams } from "react-router-dom";\n',
)
replace_once(
    "platform-ui/src/pages/chat/ChatPage.tsx",
    '    loadSessions, send, updateMapping, submitMapping, decide, cancel, refreshRun, dismissError\n',
    '    loadSessions, switchSession, send, updateMapping, submitMapping, decide, cancel, refreshRun, dismissError\n',
)
replace_once(
    "platform-ui/src/pages/chat/ChatPage.tsx",
    '  const [draft, setDraft] = useState("");\n  const bottomRef = useRef<HTMLDivElement>(null);\n',
    '  const [draft, setDraft] = useState("");\n'
    '  const bottomRef = useRef<HTMLDivElement>(null);\n'
    '  const [searchParams] = useSearchParams();\n'
    '  const requestedSessionId = searchParams.get("session");\n'
    '  const requestedProjectId = searchParams.get("project");\n'
    '  const requestedPrompt = searchParams.get("prompt");\n'
    '  const appliedSessionLinkRef = useRef<string>();\n'
    '  const appliedPromptRef = useRef<string>();\n',
)
replace_once(
    "platform-ui/src/pages/chat/ChatPage.tsx",
    '  useEffect(() => {\n    void loadSessions();\n  }, [loadSessions]);\n\n  // 轮询：仅在 run 处于等待求解/复核时开启。\n',
    '  useEffect(() => {\n'
    '    void loadSessions();\n'
    '  }, [loadSessions]);\n\n'
    '  // Project Workspace 深链只在 URL 变化时应用一次，避免侧栏切换会话后被旧 query 强行切回。\n'
    '  useEffect(() => {\n'
    '    if (!requestedSessionId || appliedSessionLinkRef.current === requestedSessionId) return;\n'
    '    appliedSessionLinkRef.current = requestedSessionId;\n'
    '    void switchSession(requestedSessionId);\n'
    '  }, [requestedSessionId, switchSession]);\n\n'
    '  useEffect(() => {\n'
    '    if (!requestedPrompt || appliedPromptRef.current === requestedPrompt) return;\n'
    '    appliedPromptRef.current = requestedPrompt;\n'
    '    setDraft(requestedPrompt);\n'
    '  }, [requestedPrompt]);\n\n'
    '  // 轮询：仅在 run 处于等待求解/复核时开启。\n',
)
replace_once(
    "platform-ui/src/pages/chat/ChatPage.tsx",
    '          <span style={styles.headerTitle}>{activeTitle ?? "工程智能体"}</span>\n          {run && <span style={styles.headerBadge}>{run.status}</span>}\n',
    '          <div style={styles.headerContext}>\n'
    '            {requestedProjectId && (\n'
    '              <Link to={`/projects/${requestedProjectId}`} style={styles.projectLink}>\n'
    '                返回工程项目\n'
    '              </Link>\n'
    '            )}\n'
    '            <span style={styles.headerTitle}>{activeTitle ?? "工程智能体"}</span>\n'
    '          </div>\n'
    '          {run && <span style={styles.headerBadge}>{run.status}</span>}\n',
)
replace_once(
    "platform-ui/src/pages/chat/ChatPage.tsx",
    '  headerTitle: { fontSize: 13, fontWeight: 600 },\n',
    '  headerContext: { display: "flex", alignItems: "center", gap: 10, minWidth: 0 },\n'
    '  projectLink: { color: "var(--primary-color)", fontSize: 11, textDecoration: "none", whiteSpace: "nowrap" },\n'
    '  headerTitle: { fontSize: 13, fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" },\n',
)

write(
    "platform-ui/src/pages/projects/projectWorkspaceModel.ts",
    r'''
    import type {
      EngineeringProjectRunSummary,
      EngineeringWorkspace,
      EngineeringWorkspacePatchPayload
    } from "../../api/agentApi";

    export interface WorkspaceDraft {
      solver: string;
      loadKind: string;
      damperType: string;
      selectedLayoutId: string;
      responseIdsText: string;
      optimizationProfile: string;
    }

    export function workspaceToDraft(workspace: EngineeringWorkspace): WorkspaceDraft {
      return {
        solver: workspace.solver ?? "",
        loadKind: workspace.loadKind ?? "",
        damperType: workspace.damperType ?? "",
        selectedLayoutId: workspace.selectedLayoutId ?? "",
        responseIdsText: workspace.responseIds.join("\n"),
        optimizationProfile: workspace.optimizationProfile || "STANDARD"
      };
    }

    function nullable(value: string): string | null {
      const trimmed = value.trim();
      return trimmed ? trimmed : null;
    }

    export function workspaceDraftToPatch(draft: WorkspaceDraft): EngineeringWorkspacePatchPayload {
      return {
        solver: nullable(draft.solver) as EngineeringWorkspacePatchPayload["solver"],
        loadKind: nullable(draft.loadKind) as EngineeringWorkspacePatchPayload["loadKind"],
        damperType: nullable(draft.damperType) as EngineeringWorkspacePatchPayload["damperType"],
        selectedLayoutId: nullable(draft.selectedLayoutId),
        responseIds: Array.from(new Set(
          draft.responseIdsText
            .split(/[\n,]/)
            .map(item => item.trim())
            .filter(Boolean)
        )),
        optimizationProfile: (draft.optimizationProfile || "STANDARD") as EngineeringWorkspacePatchPayload["optimizationProfile"]
      };
    }

    export function buildRunInquiryPrompt(runId: string): string {
      return `请查看工程 Run ${runId} 的已登记结果与 Evidence，概括主要工程结果、关键限制和可追溯 Artifact。精确数值只从注册结果读取。`;
    }

    export function buildComparisonPrompt(runIds: string[]): string {
      const unique = Array.from(new Set(runIds.map(item => item.trim()).filter(Boolean)));
      if (unique.length < 2) throw new Error("至少选择两个 Run 才能比较");
      if (unique.length > 8) throw new Error("一次最多比较 8 个 Run");
      return `比较这些工程 Run：${unique.join("、")}。请以第一个 Run 为基线，使用已登记 Evidence 做确定性比较，并遵守 DIRECT / CROSS_SOLVER / LIMITED / NOT_COMPARABLE 的兼容性限制。`;
    }

    export function runEvidenceMode(run: EngineeringProjectRunSummary): string {
      return String(run.resultSummary?.evidenceMode || "—");
    }
    ''',
)

write(
    "platform-ui/src/pages/projects/projectWorkspaceModel.test.ts",
    r'''
    import { describe, expect, it } from "vitest";
    import {
      buildComparisonPrompt,
      buildRunInquiryPrompt,
      workspaceDraftToPatch,
      workspaceToDraft
    } from "./projectWorkspaceModel";

    describe("project workspace model", () => {
      it("round-trips editable workspace fields without inventing artifact bindings", () => {
        const draft = workspaceToDraft({
          schemaVersion: 1,
          modelArtifactId: "art_model",
          modelFileName: "bridge.cdb",
          modelSha256: "a".repeat(64),
          solver: "OPENSEESPY_INPROC",
          loadKind: "EARTHQUAKE",
          loadArtifactId: "art_load",
          loadSha256: "b".repeat(64),
          damperType: "VISCOUS",
          selectedLayoutId: "TWO_PER_TOWER",
          responseIds: ["tower_base_shear", "girder_end_ux"],
          optimizationProfile: "FULL"
        });
        const patch = workspaceDraftToPatch(draft);
        expect(patch).toEqual({
          solver: "OPENSEESPY_INPROC",
          loadKind: "EARTHQUAKE",
          damperType: "VISCOUS",
          selectedLayoutId: "TWO_PER_TOWER",
          responseIds: ["tower_base_shear", "girder_end_ux"],
          optimizationProfile: "FULL"
        });
        expect("modelArtifactId" in patch).toBe(false);
        expect("loadArtifactId" in patch).toBe(false);
      });

      it("builds evidence-bound chat prompts", () => {
        expect(buildRunInquiryPrompt("agr_1")).toContain("agr_1");
        const prompt = buildComparisonPrompt(["agr_1", "agr_2", "agr_2"]);
        expect(prompt).toContain("agr_1、agr_2");
        expect(prompt).toContain("Evidence");
      });

      it("requires two distinct runs for comparison", () => {
        expect(() => buildComparisonPrompt(["agr_1", "agr_1"])).toThrow("至少选择两个 Run");
      });
    });
    ''',
)

write(
    "platform-ui/src/pages/projects/ProjectWorkspacePage.tsx",
    r'''
    import type React from "react";
    import { useCallback, useEffect, useMemo, useState } from "react";
    import {
      Boxes,
      CheckCircle2,
      ExternalLink,
      FileText,
      FolderKanban,
      GitCompareArrows,
      MessageSquare,
      Plus,
      RefreshCw,
      Save
    } from "lucide-react";
    import { useNavigate, useParams } from "react-router-dom";

    import {
      agentApi,
      type AgentRun,
      type EngineeringProjectDetail,
      type EngineeringProjectRunSummary,
      type EngineeringProjectSummary
    } from "../../api/agentApi";
    import {
      buildComparisonPrompt,
      buildRunInquiryPrompt,
      runEvidenceMode,
      workspaceDraftToPatch,
      workspaceToDraft,
      type WorkspaceDraft
    } from "./projectWorkspaceModel";

    const EMPTY_DRAFT: WorkspaceDraft = {
      solver: "",
      loadKind: "",
      damperType: "",
      selectedLayoutId: "",
      responseIdsText: "",
      optimizationProfile: "STANDARD"
    };

    function formatTime(value?: string): string {
      if (!value) return "—";
      const date = new Date(value);
      return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
    }

    function statusTone(status: string): React.CSSProperties {
      if (status === "SUCCEEDED") return { color: "var(--success-color)" };
      if (["FAILED", "CANCELLED"].includes(status)) return { color: "var(--error-color)" };
      return { color: "var(--primary-color)" };
    }

    export default function ProjectWorkspacePage() {
      const { projectId } = useParams();
      const navigate = useNavigate();
      const [projects, setProjects] = useState<EngineeringProjectSummary[]>([]);
      const [project, setProject] = useState<EngineeringProjectDetail>();
      const [workspaceDraft, setWorkspaceDraft] = useState<WorkspaceDraft>(EMPTY_DRAFT);
      const [selectedRunIds, setSelectedRunIds] = useState<string[]>([]);
      const [runDetail, setRunDetail] = useState<AgentRun>();
      const [loading, setLoading] = useState(false);
      const [saving, setSaving] = useState(false);
      const [creating, setCreating] = useState(false);
      const [error, setError] = useState<string>();
      const [newName, setNewName] = useState("");
      const [newDescription, setNewDescription] = useState("");

      const loadProjects = useCallback(async () => {
        try {
          const response = await agentApi.listProjects();
          setProjects(response.data ?? []);
        } catch (reason) {
          setError(reason instanceof Error ? reason.message : "读取工程项目失败");
        }
      }, []);

      const loadProject = useCallback(async () => {
        if (!projectId) {
          setProject(undefined);
          return;
        }
        setLoading(true);
        setError(undefined);
        try {
          const detail = await agentApi.getProject(projectId);
          setProject(detail);
          setWorkspaceDraft(workspaceToDraft(detail.workspace));
          setSelectedRunIds(current => current.filter(runId => detail.runs.some(run => run.runId === runId)));
          setRunDetail(current => current && detail.runs.some(run => run.runId === current.runId) ? current : undefined);
        } catch (reason) {
          setError(reason instanceof Error ? reason.message : "读取工程项目失败");
        } finally {
          setLoading(false);
        }
      }, [projectId]);

      useEffect(() => { void loadProjects(); }, [loadProjects]);
      useEffect(() => { void loadProject(); }, [loadProject]);

      const selectedRuns = useMemo(
        () => (project?.runs ?? []).filter(run => selectedRunIds.includes(run.runId)),
        [project?.runs, selectedRunIds]
      );

      const createProject = async () => {
        const name = newName.trim();
        if (!name || creating) return;
        setCreating(true);
        setError(undefined);
        try {
          const created = await agentApi.createProject(name, newDescription.trim());
          setNewName("");
          setNewDescription("");
          await loadProjects();
          navigate(`/projects/${created.projectId}`);
        } catch (reason) {
          setError(reason instanceof Error ? reason.message : "创建工程项目失败");
        } finally {
          setCreating(false);
        }
      };

      const saveWorkspace = async () => {
        if (!project || saving) return;
        setSaving(true);
        setError(undefined);
        try {
          await agentApi.updateProjectWorkspace(project.projectId, workspaceDraftToPatch(workspaceDraft));
          await Promise.all([loadProject(), loadProjects()]);
        } catch (reason) {
          setError(reason instanceof Error ? reason.message : "保存 Workspace 失败");
        } finally {
          setSaving(false);
        }
      };

      const openChat = async (sessionId: string, prompt?: string) => {
        if (!project) return;
        const params = new URLSearchParams({ session: sessionId, project: project.projectId });
        if (prompt) params.set("prompt", prompt);
        navigate(`/?${params.toString()}`);
      };

      const newProjectChat = async () => {
        if (!project) return;
        setError(undefined);
        try {
          const session = await agentApi.createProjectSession(project.projectId, `${project.name} · 工程对话`);
          await Promise.all([loadProject(), loadProjects()]);
          await openChat(session.sessionId);
        } catch (reason) {
          setError(reason instanceof Error ? reason.message : "创建 Project 会话失败");
        }
      };

      const openRunInquiry = async (run: EngineeringProjectRunSummary) => {
        await openChat(run.sessionId, buildRunInquiryPrompt(run.runId));
      };

      const openComparison = async () => {
        if (!project) return;
        try {
          const prompt = buildComparisonPrompt(selectedRunIds);
          const preferredSessionId = selectedRuns[0]?.sessionId || project.sessions[0]?.sessionId;
          const sessionId = preferredSessionId
            ?? (await agentApi.createProjectSession(project.projectId, `${project.name} · Run 比较`)).sessionId;
          await openChat(sessionId, prompt);
        } catch (reason) {
          setError(reason instanceof Error ? reason.message : "无法发起 Run 比较");
        }
      };

      const toggleRun = (runId: string) => {
        setSelectedRunIds(current => {
          if (current.includes(runId)) return current.filter(item => item !== runId);
          if (current.length >= 8) {
            setError("一次最多选择 8 个 Run 进行比较");
            return current;
          }
          return [...current, runId];
        });
      };

      const inspectRun = async (runId: string) => {
        setError(undefined);
        try {
          setRunDetail(await agentApi.getRun(runId));
        } catch (reason) {
          setError(reason instanceof Error ? reason.message : "读取 Run 详情失败");
        }
      };

      const artifactIds = useMemo(() => {
        if (!runDetail) return [];
        return Array.from(new Set([
          ...(runDetail.reportArtifactId ? [runDetail.reportArtifactId] : []),
          ...(runDetail.artifactIds ?? []),
          ...(runDetail.figureArtifactIds ?? [])
        ]));
      }, [runDetail]);

      return (
        <div style={styles.page}>
          <header style={styles.pageHeader}>
            <div>
              <div style={styles.eyebrow}>ENGINEERING PROJECTS</div>
              <h1 style={styles.title}>工程项目工作台</h1>
              <p style={styles.subtitle}>把 Workspace、Run 历史与 Chat 放到同一 Project 作用域内。工程数字仍以 Artifact / Evidence 为真源。</p>
            </div>
            <button type="button" style={styles.secondaryButton} onClick={() => void Promise.all([loadProjects(), loadProject()])}>
              <RefreshCw size={14} />刷新
            </button>
          </header>

          {error && <div style={styles.error}>{error}</div>}

          <div style={styles.layout}>
            <aside style={styles.projectRail}>
              <section style={styles.card}>
                <div style={styles.sectionTitle}><Plus size={14} />新建工程</div>
                <input value={newName} onChange={event => setNewName(event.target.value)} placeholder="工程名称" style={styles.input} />
                <textarea value={newDescription} onChange={event => setNewDescription(event.target.value)} placeholder="工程说明（可选）" rows={3} style={styles.textarea} />
                <button type="button" style={styles.primaryButton} disabled={!newName.trim() || creating} onClick={() => void createProject()}>
                  <Plus size={14} />{creating ? "创建中…" : "创建 Project"}
                </button>
              </section>

              <section style={styles.card}>
                <div style={styles.sectionTitle}><FolderKanban size={14} />Project</div>
                <div style={styles.projectList}>
                  {projects.length === 0 && <div style={styles.muted}>暂无工程项目</div>}
                  {projects.map(item => (
                    <button
                      key={item.projectId}
                      type="button"
                      onClick={() => navigate(`/projects/${item.projectId}`)}
                      style={{ ...styles.projectItem, ...(item.projectId === projectId ? styles.projectItemActive : {}) }}
                    >
                      <span style={styles.projectName}>{item.name}</span>
                      <span style={styles.projectMeta}>{item.runCount} Runs · {item.sessionCount} Sessions</span>
                    </button>
                  ))}
                </div>
              </section>
            </aside>

            <main style={styles.main}>
              {!projectId ? (
                <section style={styles.emptyCard}>
                  <FolderKanban size={28} />
                  <strong>选择一个工程项目</strong>
                  <span>左侧选择已有 Project，或创建新 Project 后进入工程工作台。</span>
                </section>
              ) : loading && !project ? (
                <section style={styles.emptyCard}>正在加载工程项目…</section>
              ) : project ? (
                <>
                  <section style={styles.projectHeaderCard}>
                    <div>
                      <div style={styles.projectHeadingRow}>
                        <h2 style={styles.projectTitle}>{project.name}</h2>
                        <span style={styles.statusBadge}>{project.status}</span>
                      </div>
                      <p style={styles.description}>{project.description || "暂无工程说明"}</p>
                      <div style={styles.projectStats}>
                        <span>Workspace rev. {project.workspaceRevision}</span>
                        <span>{project.runCount} Runs</span>
                        <span>{project.sessionCount} Sessions</span>
                      </div>
                    </div>
                    <button type="button" style={styles.primaryButton} onClick={() => void newProjectChat()}>
                      <MessageSquare size={14} />新建项目对话
                    </button>
                  </section>

                  <div style={styles.twoColumns}>
                    <section style={styles.card}>
                      <div style={styles.sectionTitle}><Boxes size={14} />Workspace</div>
                      <div style={styles.artifactBox}>
                        <span style={styles.fieldLabel}>模型</span>
                        <strong>{project.workspace.modelFileName || "未绑定"}</strong>
                        <code style={styles.code}>{project.workspace.modelArtifactId || "modelArtifactId: —"}</code>
                      </div>
                      <div style={styles.artifactBox}>
                        <span style={styles.fieldLabel}>荷载 Artifact</span>
                        <code style={styles.code}>{project.workspace.loadArtifactId || "loadArtifactId: —"}</code>
                      </div>
                      <div style={styles.formGrid}>
                        <label style={styles.field}><span style={styles.fieldLabel}>Solver</span><select value={workspaceDraft.solver} onChange={event => setWorkspaceDraft({ ...workspaceDraft, solver: event.target.value })} style={styles.input}><option value="">未指定</option><option value="OPENSEESPY_INPROC">OpenSeesPy</option><option value="ANSYS">ANSYS</option></select></label>
                        <label style={styles.field}><span style={styles.fieldLabel}>Load</span><select value={workspaceDraft.loadKind} onChange={event => setWorkspaceDraft({ ...workspaceDraft, loadKind: event.target.value })} style={styles.input}><option value="">未指定</option><option value="EARTHQUAKE">Earthquake</option><option value="WIND">Wind</option><option value="TRAFFIC">Traffic</option><option value="GENERIC_NODAL">Generic nodal</option></select></label>
                        <label style={styles.field}><span style={styles.fieldLabel}>Damper</span><select value={workspaceDraft.damperType} onChange={event => setWorkspaceDraft({ ...workspaceDraft, damperType: event.target.value })} style={styles.input}><option value="">未指定</option><option value="VISCOUS">Viscous</option><option value="FRICTION">Friction</option><option value="EDDY_CURRENT">Eddy current</option></select></label>
                        <label style={styles.field}><span style={styles.fieldLabel}>Optimization</span><select value={workspaceDraft.optimizationProfile} onChange={event => setWorkspaceDraft({ ...workspaceDraft, optimizationProfile: event.target.value })} style={styles.input}><option value="STANDARD">STANDARD</option><option value="FULL">FULL</option><option value="CUSTOM">CUSTOM</option></select></label>
                        <label style={styles.field}><span style={styles.fieldLabel}>Layout</span><input value={workspaceDraft.selectedLayoutId} onChange={event => setWorkspaceDraft({ ...workspaceDraft, selectedLayoutId: event.target.value })} placeholder="例如 TWO_PER_TOWER" style={styles.input} /></label>
                        <label style={{ ...styles.field, gridColumn: "1 / -1" }}><span style={styles.fieldLabel}>Responses</span><textarea value={workspaceDraft.responseIdsText} onChange={event => setWorkspaceDraft({ ...workspaceDraft, responseIdsText: event.target.value })} placeholder="每行一个 responseId" rows={4} style={styles.textarea} /></label>
                      </div>
                      <div style={styles.workspaceFooter}>
                        <span style={styles.muted}>模型/荷载 Artifact 不在这里手填，避免绕过文件映射与证据链。</span>
                        <button type="button" style={styles.primaryButton} disabled={saving} onClick={() => void saveWorkspace()}><Save size={14} />{saving ? "保存中…" : "保存 Workspace"}</button>
                      </div>
                    </section>

                    <section style={styles.card}>
                      <div style={styles.sectionTitle}><MessageSquare size={14} />Project Sessions</div>
                      {project.sessions.length === 0 ? <div style={styles.muted}>暂无 Project 会话</div> : project.sessions.map(session => (
                        <div key={session.sessionId} style={styles.sessionRow}>
                          <div><strong>{session.title}</strong><div style={styles.projectMeta}>{session.runCount ?? 0} Runs · {formatTime(session.updatedAt || session.createdAt)}</div></div>
                          <button type="button" style={styles.iconButton} onClick={() => void openChat(session.sessionId)}><ExternalLink size={14} />打开 Chat</button>
                        </div>
                      ))}
                    </section>
                  </div>

                  <section style={styles.card}>
                    <div style={styles.runToolbar}>
                      <div>
                        <div style={styles.sectionTitle}><CheckCircle2 size={14} />Run History</div>
                        <div style={styles.muted}>选择 2–8 个 Run 可送回 Chat 做 Evidence-backed 比较。</div>
                      </div>
                      <button type="button" style={selectedRunIds.length >= 2 ? styles.primaryButton : styles.disabledButton} disabled={selectedRunIds.length < 2} onClick={() => void openComparison()}>
                        <GitCompareArrows size={14} />比较选中 Run ({selectedRunIds.length})
                      </button>
                    </div>
                    <div style={styles.runTableWrap}>
                      <table style={styles.table}>
                        <thead><tr><th style={styles.th}>比较</th><th style={styles.th}>Run</th><th style={styles.th}>任务</th><th style={styles.th}>状态</th><th style={styles.th}>Evidence</th><th style={styles.th}>更新时间</th><th style={styles.th}>操作</th></tr></thead>
                        <tbody>
                          {project.runs.length === 0 && <tr><td colSpan={7} style={styles.emptyCell}>暂无 Run</td></tr>}
                          {project.runs.map(run => (
                            <tr key={run.runId} style={styles.tr}>
                              <td style={styles.td}><input type="checkbox" checked={selectedRunIds.includes(run.runId)} onChange={() => toggleRun(run.runId)} aria-label={`选择 ${run.runId}`} /></td>
                              <td style={styles.td}><code style={styles.code}>{run.runId}</code></td>
                              <td style={styles.td}>{run.taskType || "—"}<div style={styles.projectMeta}>{run.currentStage}</div></td>
                              <td style={{ ...styles.td, ...statusTone(run.status) }}>{run.status}</td>
                              <td style={styles.td}>{runEvidenceMode(run)}</td>
                              <td style={styles.td}>{formatTime(run.updatedAt || run.createdAt)}</td>
                              <td style={styles.td}><div style={styles.actions}><button type="button" style={styles.iconButton} onClick={() => void inspectRun(run.runId)}><FileText size={13} />详情</button><button type="button" style={styles.iconButton} onClick={() => void openRunInquiry(run)}><MessageSquare size={13} />问 Chat</button></div></td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </section>

                  {runDetail && (
                    <section style={styles.card}>
                      <div style={styles.runToolbar}>
                        <div><div style={styles.sectionTitle}><FileText size={14} />Run 详情</div><code style={styles.code}>{runDetail.runId}</code></div>
                        <button type="button" style={styles.secondaryButton} onClick={() => setRunDetail(undefined)}>关闭</button>
                      </div>
                      <div style={styles.detailGrid}>
                        <div><span style={styles.fieldLabel}>Task</span><strong>{runDetail.taskType || "—"}</strong></div>
                        <div><span style={styles.fieldLabel}>Status</span><strong>{runDetail.status}</strong></div>
                        <div><span style={styles.fieldLabel}>Solver</span><strong>{runDetail.resultMetadata?.solver || String(runDetail.intent?.solver || "—")}</strong></div>
                        <div><span style={styles.fieldLabel}>Evidence</span><strong>{runDetail.resultSummary?.evidenceMode || "—"}</strong></div>
                      </div>
                      <div style={styles.artifactsSection}>
                        <div style={styles.fieldLabel}>Registered Artifacts</div>
                        {artifactIds.length === 0 ? <div style={styles.muted}>此 Run 当前未登记可下载 Artifact。</div> : (
                          <div style={styles.artifactList}>{artifactIds.map(artifactId => <a key={artifactId} href={agentApi.artifactDownloadUrl(artifactId)} style={styles.artifactLink}><FileText size={13} />{artifactId}</a>)}</div>
                        )}
                      </div>
                    </section>
                  )}
                </>
              ) : null}
            </main>
          </div>
        </div>
      );
    }

    const styles: Record<string, React.CSSProperties> = {
      page: { display: "flex", flexDirection: "column", gap: 16, minHeight: "100%" },
      pageHeader: { display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 20 },
      eyebrow: { fontSize: 10, letterSpacing: "0.14em", color: "var(--primary-color)", fontWeight: 700 },
      title: { margin: "4px 0 6px", fontSize: 24 },
      subtitle: { margin: 0, maxWidth: 760, color: "var(--text-secondary)", fontSize: 12, lineHeight: 1.7 },
      layout: { display: "grid", gridTemplateColumns: "260px minmax(0, 1fr)", gap: 16, alignItems: "start" },
      projectRail: { display: "flex", flexDirection: "column", gap: 12, position: "sticky", top: 0 },
      main: { minWidth: 0, display: "flex", flexDirection: "column", gap: 14 },
      card: { background: "var(--bg-secondary)", border: "1px solid var(--border-color)", borderRadius: 10, padding: 14 },
      emptyCard: { minHeight: 260, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 10, color: "var(--text-secondary)", background: "var(--bg-secondary)", border: "1px solid var(--border-color)", borderRadius: 10 },
      projectHeaderCard: { display: "flex", alignItems: "center", justifyContent: "space-between", gap: 20, padding: 16, background: "var(--bg-secondary)", border: "1px solid var(--border-color)", borderRadius: 10 },
      sectionTitle: { display: "flex", alignItems: "center", gap: 7, fontSize: 13, fontWeight: 700, marginBottom: 12 },
      projectList: { display: "flex", flexDirection: "column", gap: 6 },
      projectItem: { display: "flex", flexDirection: "column", alignItems: "stretch", gap: 3, textAlign: "left", padding: "9px 10px", borderRadius: 7, border: "1px solid transparent", background: "transparent", color: "var(--text-primary)", cursor: "pointer" },
      projectItemActive: { background: "var(--primary-soft)", borderColor: "var(--primary-color)" },
      projectName: { fontSize: 12, fontWeight: 650, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" },
      projectMeta: { color: "var(--text-muted)", fontSize: 10, marginTop: 3 },
      primaryButton: { display: "inline-flex", alignItems: "center", justifyContent: "center", gap: 6, border: "none", borderRadius: 6, padding: "8px 11px", background: "var(--primary-color)", color: "var(--btn-primary-ink)", cursor: "pointer", fontSize: 11, fontWeight: 650 },
      secondaryButton: { display: "inline-flex", alignItems: "center", justifyContent: "center", gap: 6, border: "1px solid var(--border-color)", borderRadius: 6, padding: "7px 10px", background: "var(--bg-secondary)", color: "var(--text-secondary)", cursor: "pointer", fontSize: 11 },
      disabledButton: { display: "inline-flex", alignItems: "center", justifyContent: "center", gap: 6, border: "none", borderRadius: 6, padding: "8px 11px", background: "var(--bg-tertiary)", color: "var(--text-muted)", cursor: "not-allowed", fontSize: 11 },
      iconButton: { display: "inline-flex", alignItems: "center", gap: 5, border: "1px solid var(--border-color)", borderRadius: 5, padding: "5px 7px", background: "transparent", color: "var(--text-secondary)", cursor: "pointer", fontSize: 10 },
      input: { width: "100%", boxSizing: "border-box", border: "1px solid var(--border-color)", borderRadius: 6, background: "var(--bg-primary)", color: "var(--text-primary)", padding: "7px 8px", fontSize: 11, marginBottom: 8 },
      textarea: { width: "100%", boxSizing: "border-box", border: "1px solid var(--border-color)", borderRadius: 6, background: "var(--bg-primary)", color: "var(--text-primary)", padding: "7px 8px", fontSize: 11, resize: "vertical", marginBottom: 8, fontFamily: "inherit" },
      muted: { color: "var(--text-muted)", fontSize: 10, lineHeight: 1.6 },
      error: { padding: "9px 12px", border: "1px solid var(--error-color)", color: "var(--error-color)", borderRadius: 7, fontSize: 11 },
      projectHeadingRow: { display: "flex", alignItems: "center", gap: 9 },
      projectTitle: { margin: 0, fontSize: 18 },
      statusBadge: { padding: "2px 7px", borderRadius: 999, background: "var(--primary-soft)", color: "var(--primary-color)", fontSize: 9, fontWeight: 700 },
      description: { margin: "6px 0", color: "var(--text-secondary)", fontSize: 11 },
      projectStats: { display: "flex", flexWrap: "wrap", gap: 12, color: "var(--text-muted)", fontSize: 10 },
      twoColumns: { display: "grid", gridTemplateColumns: "minmax(0, 1.35fr) minmax(280px, .65fr)", gap: 14, alignItems: "start" },
      artifactBox: { display: "flex", flexDirection: "column", gap: 3, padding: 9, background: "var(--bg-primary)", borderRadius: 6, marginBottom: 8 },
      fieldLabel: { display: "block", color: "var(--text-muted)", fontSize: 9, textTransform: "uppercase", letterSpacing: ".06em", marginBottom: 4 },
      code: { fontSize: 10, overflowWrap: "anywhere", color: "var(--text-secondary)" },
      formGrid: { display: "grid", gridTemplateColumns: "repeat(2, minmax(0, 1fr))", gap: "4px 10px" },
      field: { display: "block", minWidth: 0 },
      workspaceFooter: { display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, marginTop: 4 },
      sessionRow: { display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, padding: "9px 0", borderBottom: "1px solid var(--border-color)", fontSize: 11 },
      runToolbar: { display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, marginBottom: 10 },
      runTableWrap: { overflowX: "auto" },
      table: { width: "100%", borderCollapse: "collapse", fontSize: 10 },
      th: { textAlign: "left", padding: "8px 7px", color: "var(--text-muted)", borderBottom: "1px solid var(--border-color)", whiteSpace: "nowrap" },
      tr: { borderBottom: "1px solid var(--border-color)" },
      td: { padding: "9px 7px", verticalAlign: "middle" },
      emptyCell: { padding: 20, textAlign: "center", color: "var(--text-muted)" },
      actions: { display: "flex", gap: 5, whiteSpace: "nowrap" },
      detailGrid: { display: "grid", gridTemplateColumns: "repeat(4, minmax(0, 1fr))", gap: 10, padding: "10px 0" },
      artifactsSection: { marginTop: 8, paddingTop: 10, borderTop: "1px solid var(--border-color)" },
      artifactList: { display: "flex", flexWrap: "wrap", gap: 7, marginTop: 7 },
      artifactLink: { display: "inline-flex", alignItems: "center", gap: 5, color: "var(--primary-color)", fontSize: 10, textDecoration: "none", padding: "5px 7px", border: "1px solid var(--border-color)", borderRadius: 5 }
    };
    ''',
)

print("PR10 project workspace UI migration applied")
