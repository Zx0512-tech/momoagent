import type React from "react";
import { useEffect, useMemo, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import {
  ChevronDown,
  ChevronRight,
  FolderOpen,
  FlaskConical,
  FileBarChart2,
  LayoutDashboard,
  MessageSquarePlus,
  PlaySquare,
  Sliders,
  Brain,
  Trash2,
  Wrench,
  Zap
} from "lucide-react";

import type { AgentSessionSummary } from "../../api/agentApi";
import { useChatStore } from "../../stores/chatStore";

/** 深挖用的分析页入口，对话之外保留全部原有工程页面。 */
const analysisPages = [
  { name: "总览主面板", path: "/dashboard", icon: LayoutDashboard },
  { name: "阻尼器基准配置", path: "/damper-base", icon: Wrench },
  { name: "试验设计", path: "/experiment-design", icon: FlaskConical },
  { name: "荷载配置", path: "/loads", icon: Zap },
  { name: "求解器批处理计算", path: "/solver", icon: PlaySquare },
  { name: "结果提取与报告", path: "/results", icon: FileBarChart2 },
  { name: "代理模型与主动学习", path: "/surrogate", icon: Brain },
  { name: "多目标优化与决策", path: "/optimization", icon: Sliders },
  { name: "平台制品库浏览器", path: "/artifacts", icon: FolderOpen }
];

function groupByRecency(sessions: AgentSessionSummary[]) {
  const today: AgentSessionSummary[] = [];
  const earlier: AgentSessionSummary[] = [];
  const startOfToday = new Date();
  startOfToday.setHours(0, 0, 0, 0);
  for (const session of sessions) {
    const updated = new Date(session.updatedAt);
    if (!Number.isNaN(updated.valueOf()) && updated >= startOfToday) {
      today.push(session);
    } else {
      earlier.push(session);
    }
  }
  return { today, earlier };
}

export const ChatSidebar = () => {
  const location = useLocation();
  const {
    sessions, activeSessionId, deletingSessionId, busy,
    loadSessions, newSession, switchSession, deleteSession
  } = useChatStore();
  const [navOpen, setNavOpen] = useState(false);

  useEffect(() => {
    void loadSessions();
  }, [loadSessions]);

  const groups = useMemo(() => groupByRecency(sessions), [sessions]);

  const renderGroup = (label: string, items: typeof sessions) => {
    if (items.length === 0) return null;
    return (
      <div style={styles.group}>
        <div style={styles.groupLabel}>{label}</div>
        {items.map(session => (
          <div key={session.sessionId} style={styles.sessionRow}>
            <button
              type="button"
              style={{
                ...styles.sessionItem,
                background: session.sessionId === activeSessionId ? "var(--bg-tertiary)" : "transparent",
                color: session.sessionId === activeSessionId ? "var(--text-primary)" : "var(--text-secondary)"
              }}
              onClick={() => void switchSession(session.sessionId)}
              title={session.title}
            >
              {session.title}
            </button>
            <button
              type="button"
              style={styles.deleteButton}
              aria-label={`删除会话：${session.title}`}
              title="删除会话"
              disabled={
                deletingSessionId === session.sessionId
                || (busy && activeSessionId === session.sessionId)
              }
              onClick={() => {
                if (window.confirm(`确定删除会话“${session.title}”吗？未完成任务将先取消，工程计算证据仍会保留。`)) {
                  void deleteSession(session.sessionId);
                }
              }}
            >
              <Trash2 size={13} />
            </button>
          </div>
        ))}
      </div>
    );
  };

  return (
    <div style={styles.sidebar}>
      <button type="button" style={styles.newButton} onClick={newSession}>
        <MessageSquarePlus size={15} />
        <span>新建对话</span>
      </button>

      <div style={styles.sessionScroll}>
        {sessions.length === 0 && <p style={styles.hint}>还没有历史对话</p>}
        {renderGroup("今天", groups.today)}
        {renderGroup("更早", groups.earlier)}
      </div>

      <div style={styles.navSection}>
        <button type="button" style={styles.navToggle} onClick={() => setNavOpen(open => !open)}>
          {navOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          <span>分析页导航</span>
        </button>
        {navOpen && (
          <div style={styles.navList}>
            {analysisPages.map(page => {
              const active = location.pathname.startsWith(page.path);
              return (
                <Link
                  key={page.path}
                  to={page.path}
                  style={{
                    ...styles.navItem,
                    color: active ? "var(--primary-color)" : "var(--text-secondary)",
                    background: active ? "var(--bg-tertiary)" : "transparent"
                  }}
                >
                  <page.icon size={14} />
                  <span style={styles.navText}>{page.name}</span>
                </Link>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
};

const styles: Record<string, React.CSSProperties> = {
  sidebar: {
    width: "var(--sidebar-width)",
    background: "var(--bg-secondary)",
    borderRight: "1px solid var(--border-color)",
    display: "flex",
    flexDirection: "column",
    height: "100%",
    flexShrink: 0
  },
  newButton: {
    display: "flex",
    alignItems: "center",
    gap: 8,
    margin: 10,
    padding: "9px 11px",
    borderRadius: 6,
    border: "1px solid var(--border-color)",
    background: "transparent",
    color: "var(--text-primary)",
    cursor: "pointer",
    fontSize: 12,
    fontWeight: 500,
    flexShrink: 0
  },
  sessionScroll: { flex: 1, overflowY: "auto", padding: "0 8px", minHeight: 0 },
  hint: { color: "var(--text-muted)", fontSize: 11, padding: "6px 4px" },
  group: { marginBottom: 12 },
  groupLabel: {
    color: "var(--text-muted)",
    fontSize: 10,
    fontWeight: 600,
    textTransform: "uppercase",
    padding: "6px 4px"
  },
  sessionRow: { display: "flex", alignItems: "center", gap: 2, marginBottom: 2 },
  sessionItem: {
    display: "block",
    minWidth: 0,
    flex: 1,
    textAlign: "left",
    padding: "7px 8px",
    border: "none",
    borderRadius: 5,
    cursor: "pointer",
    fontSize: 12,
    whiteSpace: "nowrap",
    overflow: "hidden",
    textOverflow: "ellipsis"
  },
  deleteButton: {
    width: 28,
    height: 28,
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    border: "none",
    borderRadius: 5,
    background: "transparent",
    color: "var(--text-muted)",
    cursor: "pointer",
    flexShrink: 0
  },
  navSection: { borderTop: "1px solid var(--border-color)", flexShrink: 0, padding: "6px 8px 10px" },
  navToggle: {
    display: "flex",
    alignItems: "center",
    gap: 6,
    width: "100%",
    padding: "7px 4px",
    border: "none",
    background: "transparent",
    color: "var(--text-muted)",
    cursor: "pointer",
    fontSize: 11,
    fontWeight: 600
  },
  navList: { display: "flex", flexDirection: "column", gap: 2, maxHeight: 260, overflowY: "auto" },
  navItem: {
    display: "flex",
    alignItems: "center",
    gap: 8,
    padding: "6px 8px",
    borderRadius: 5,
    textDecoration: "none",
    fontSize: 12
  },
  navText: { whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }
};
