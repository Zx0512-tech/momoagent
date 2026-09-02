import type React from "react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { AlertTriangle, X } from "lucide-react";

import { POLL_INTERVAL_MS, isPollingStatus, useChatStore } from "../../stores/chatStore";
import { MessageBubble } from "./MessageBubble";
import { Composer } from "./Composer";
import { EmptyState } from "./EmptyState";
import { PlanCard } from "./cards/PlanCard";
import { TaskProposalCard } from "./cards/TaskProposalCard";
import MappingCard from "./cards/MappingCard";
import { ApprovalCard } from "./cards/ApprovalCard";
import { ProgressCard } from "./cards/ProgressCard";
import { ResultCard } from "./cards/ResultCard";
import { isTerminalStatus } from "./chatStatus";

export const ChatPage = () => {
  const {
    messages, run, runHistory, loadImport, mapping, busy, switchingSession, error,
    sessions, activeSessionId,
    loadSessions, switchSession, send, updateMapping, submitMapping, decide, cancel, refreshRun, dismissError
  } = useChatStore();

  const [draft, setDraft] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);
  const [searchParams] = useSearchParams();
  const requestedSessionId = searchParams.get("session");
  const requestedProjectId = searchParams.get("project");
  const requestedPrompt = searchParams.get("prompt");
  const appliedSessionLinkRef = useRef<string | undefined>(undefined);
  const appliedPromptRef = useRef<string | undefined>(undefined);

  useEffect(() => {
    void loadSessions();
  }, [loadSessions]);

  // Project Workspace 深链只在 URL 变化时应用一次，避免侧栏切换会话后被旧 query 强行切回。
  useEffect(() => {
    if (!requestedSessionId || appliedSessionLinkRef.current === requestedSessionId) return;
    appliedSessionLinkRef.current = requestedSessionId;
    void switchSession(requestedSessionId);
  }, [requestedSessionId, switchSession]);

  useEffect(() => {
    if (!requestedPrompt || appliedPromptRef.current === requestedPrompt) return;
    appliedPromptRef.current = requestedPrompt;
    setDraft(requestedPrompt);
  }, [requestedPrompt]);

  // 轮询：仅在 run 处于等待求解/复核时开启。
  useEffect(() => {
    if (!isPollingStatus(run?.status)) return;
    const timer = window.setInterval(() => void refreshRun(), POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [run?.runId, run?.status, refreshRun]);

  // 新消息或阶段变化时滚到底部。
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [messages.length, run?.status, run?.currentStage]);

  const activeTitle = useMemo(
    () => sessions.find(item => item.sessionId === activeSessionId)?.title,
    [sessions, activeSessionId]
  );

  const renderRunCards = (cardRun: typeof run, current: boolean) => {
    if (!cardRun) return null;
    const resultSummary = cardRun.resultSummary;
    const showResult = Boolean(
      resultSummary?.checks
      || resultSummary?.caseResults
      || resultSummary?.objectives
      || resultSummary?.baselineObjectives
      || resultSummary?.recommendedObjectives
      || resultSummary?.objectiveChanges
      || resultSummary?.responseComparison
      || resultSummary?.sampleResponses
      || resultSummary?.figures
      || resultSummary?.inquiryMetrics?.length
      || resultSummary?.inquiryTopsis?.length
      || resultSummary?.inquiryRunComparison
      || cardRun.figureArtifactIds?.length
      || resultSummary?.narrativeSummary,
    );
    const showProgress = current
      && (Boolean(cardRun.jobId) || (cardRun.taskType === "INQUIRY" && cardRun.status === "PLANNING"))
      && !isTerminalStatus(cardRun.status);
    return (
      <>
        {cardRun.taskProposal
          ? <TaskProposalCard proposal={cardRun.taskProposal} />
          : cardRun.workflowContract || cardRun.plan || cardRun.intent
            ? <PlanCard run={cardRun} />
            : null}
        {current && loadImport && mapping && (
          <MappingCard
            loadImport={loadImport}
            mapping={mapping}
            busy={busy}
            onChange={updateMapping}
            onSubmit={() => void submitMapping()}
          />
        )}
        {showProgress && <ProgressCard run={cardRun} busy={busy} onCancel={() => void cancel()} />}
        {showResult && <ResultCard run={cardRun} />}
      </>
    );
  };

  const firstMessageIndexByRun = useMemo(() => {
    const indexes: Record<string, number> = {};
    messages.forEach((message, index) => {
      if (message.role === "ASSISTANT" && message.runId && indexes[message.runId] === undefined) {
        indexes[message.runId] = index;
      }
    });
    return indexes;
  }, [messages]);

  return (
    <div style={styles.page}>
      {activeSessionId && (
        <header style={styles.header}>
          <div style={styles.headerContext}>
            {requestedProjectId && (
              <Link to={`/projects/${requestedProjectId}`} style={styles.projectLink}>
                返回工程项目
              </Link>
            )}
            <span style={styles.headerTitle}>{activeTitle ?? "工程智能体"}</span>
          </div>
          {run && <span style={styles.headerBadge}>{run.status}</span>}
        </header>
      )}

      <div style={styles.scroll}>
        {messages.length === 0 && !busy ? (
          <EmptyState onPick={setDraft} />
        ) : (
          <div style={styles.thread}>
            {switchingSession && (
              <div style={styles.sessionLoading} role="status" aria-live="polite">
                <span style={styles.thinkingDot} className="pulse" />
                正在加载会话…
              </div>
            )}
            {messages.map((message, index) => (
              <MessageBubble
                key={message.id}
                role={message.role}
                content={
                  message.approval || message.messageType === "APPROVAL"
                    ? ""
                    : message.role === "ASSISTANT"
                      && message.runId
                      && runHistory[message.runId]?.taskType === "INQUIRY"
                      && Boolean(
                        runHistory[message.runId]?.resultSummary?.inquiryMetrics?.length
                        || runHistory[message.runId]?.resultSummary?.inquiryTopsis?.length
                        || runHistory[message.runId]?.resultSummary?.inquiryRunComparison
                      )
                        ? ""
                        : message.content
                }
              >
                {message.approval && (
                  <ApprovalCard
                    approval={message.approval}
                    busy={busy}
                    onDecide={approved => void decide(message.approval!.approvalId, approved)}
                  />
                )}
                {message.role === "ASSISTANT" && message.runId
                  && firstMessageIndexByRun[message.runId] === index
                  ? renderRunCards(
                    runHistory[message.runId] ?? (run?.runId === message.runId ? run : undefined),
                    message.runId === run?.runId,
                  )
                  : null}
              </MessageBubble>
            ))}
            {busy && messages[messages.length - 1]?.role === "USER" && (
              <MessageBubble role="ASSISTANT" content="">
                {run?.taskType === "INQUIRY" && (
                  run.resultSummary?.inquiryMetrics?.length
                  || run.resultSummary?.inquiryTopsis?.length
                  || run.resultSummary?.inquiryRunComparison
                ) ? (
                  renderRunCards(run, true)
                ) : (
                  <div style={styles.thinking}>
                    <span style={styles.thinkingDot} className="pulse" />
                    {run?.taskType === "INQUIRY"
                      ? run.resultSummary?.queryProgress?.message ?? "正在读取已完成结果…"
                      : "正在解析工程意图…"}
                  </div>
                )}
              </MessageBubble>
            )}
            <div ref={bottomRef} />
          </div>
        )}
      </div>

      {error && (
        <div style={styles.error} role="alert">
          <AlertTriangle size={14} />
          <span style={{ flex: 1 }}>{error}</span>
          <button type="button" style={styles.errorClose} onClick={dismissError} aria-label="关闭错误提示">
            <X size={14} />
          </button>
        </div>
      )}

      <Composer
        busy={busy}
        attachmentDisabled={Boolean(loadImport) || Boolean(run?.jobId)}
        presetValue={draft}
        onSend={(content, file) => void send(content, file)}
      />
    </div>
  );
};

const styles: Record<string, React.CSSProperties> = {
  page: { display: "flex", flexDirection: "column", height: "100%", minHeight: 0 },
  header: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 10,
    padding: "10px 20px",
    borderBottom: "1px solid var(--border-color)",
    flexShrink: 0
  },
  headerContext: { display: "flex", alignItems: "center", gap: 10, minWidth: 0 },
  projectLink: { color: "var(--primary-color)", fontSize: 11, textDecoration: "none", whiteSpace: "nowrap" },
  headerTitle: { fontSize: 13, fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" },
  headerBadge: {
    padding: "2px 8px",
    borderRadius: 999,
    background: "var(--bg-tertiary)",
    color: "var(--primary-color)",
    fontSize: 11,
    fontWeight: 600
  },
  scroll: { flex: 1, overflowY: "auto", minHeight: 0 },
  thread: {
    maxWidth: 860,
    margin: "0 auto",
    padding: "20px",
    display: "flex",
    flexDirection: "column",
    gap: 18
  },
  thinking: {
    display: "flex",
    alignItems: "center",
    gap: 8,
    color: "var(--text-secondary)",
    fontSize: 12,
    paddingTop: 4
  },
  sessionLoading: {
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
    minHeight: 120,
    color: "var(--text-secondary)",
    fontSize: 13
  },
  thinkingDot: {
    width: 7,
    height: 7,
    borderRadius: "50%",
    background: "var(--primary-color)",
    display: "inline-block"
  },
  error: {
    display: "flex",
    alignItems: "center",
    gap: 8,
    margin: "0 20px 8px",
    padding: "9px 12px",
    borderRadius: 6,
    border: "1px solid var(--error-color)",
    background: "var(--error-soft)",
    color: "var(--error-color)",
    fontSize: 12
  },
  errorClose: {
    background: "transparent",
    border: "none",
    color: "var(--error-color)",
    cursor: "pointer",
    display: "flex",
    padding: 0
  }
};
