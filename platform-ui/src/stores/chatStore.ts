import { create } from "zustand";
import {
  agentApi,
  suggestLoadMapping,
  type AgentRun,
  type AgentSessionMessage,
  type AgentSessionSummary,
  type LoadImport,
  type LoadMappingV2Payload
} from "../api/agentApi";

const SESSION_TITLE = "MOMO 工程智能体";

/** run 处于这些状态时需要轮询后端。 */
const POLLING_STATUSES = ["WAITING_JOB", "REVIEWING"];

export const POLL_INTERVAL_MS = 2000;

/** 后端异步生成会话标题，首条消息完成后有限退避刷新侧边栏。 */
export const TITLE_REFRESH_DELAY_MS = 1500;
export const TITLE_REFRESH_ATTEMPTS = 5;

let sessionRequestVersion = 0;
let refreshRequestVersion = 0;
let refreshInFlightRunId: string | undefined;

export interface ChatMessage {
  id: string;
  role: "USER" | "ASSISTANT";
  content: string;
  createdAt: string;
  messageType?: "TEXT" | "APPROVAL";
  approval?: AgentSessionMessage["approval"];
  /** 该消息关联的 run；结果卡片会固定渲染在这条消息下方。 */
  runId?: string | null;
  pending?: boolean;
}

interface ChatState {
  sessions: AgentSessionSummary[];
  activeSessionId?: string;
  messages: ChatMessage[];
  run?: AgentRun;
  /** 会话内每条历史 run 的最后快照，保证后续追问不会覆盖旧结果。 */
  runHistory: Record<string, AgentRun>;
  loadImport?: LoadImport;
  mapping?: LoadMappingV2Payload;
  busy: boolean;
  switchingSession: boolean;
  deletingSessionId?: string;
  error?: string;

  loadSessions: () => Promise<void>;
  newSession: () => void;
  switchSession: (sessionId: string) => Promise<void>;
  deleteSession: (sessionId: string) => Promise<void>;
  send: (content: string, file?: File) => Promise<void>;
  updateMapping: (mapping: LoadMappingV2Payload) => void;
  submitMapping: () => Promise<void>;
  decide: (approvalId: string, approved: boolean) => Promise<void>;
  cancel: () => Promise<void>;
  refreshRun: () => Promise<void>;
  dismissError: () => void;
}

function nextId(prefix: string): string {
  return `${prefix}_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
}

function errorMessage(reason: unknown, fallback: string): string {
  return reason instanceof Error ? reason.message : fallback;
}

/** 依据智能体解析出的荷载类型给出初始映射，用户仍可在卡片里改。
 *
 * 单位优先用后端的 suggestedMapping：那是从文件头或列名里读出来的声明。写死一个
 * "g" 会让 m/s² 的记录预填成 g，用户看到 g 会以为系统读出了 g，一路确认下去就是
 * 9.8 倍荷载误差，而且带着完全合法的审批哈希。读不出来时（后端给 null）宁可留空，
 * 让用户自己选，也比默认一个更危险。
 */
export function buildInitialMapping(run: AgentRun, upload: LoadImport): LoadMappingV2Payload {
  const suggested = suggestLoadMapping(upload.inspection);
  const backendSuggestion = upload.inspection.suggestedMapping;
  const intent = run.intent ?? {};
  const loadKind = String(intent.loadKind ?? "GENERIC_NODAL") as LoadMappingV2Payload["loadKind"];
  const solver = String(intent.solver ?? "OPENSEESPY_INPROC") as LoadMappingV2Payload["solver"];
  const earthquake = loadKind === "EARTHQUAKE";
  const suggestedUnit = backendSuggestion?.mapping?.channels?.[0]?.sourceUnit ?? null;
  return {
    runId: run.runId,
    loadKind,
    time: {
      column: suggested.timeColumn,
      unit: "s",
      ...(suggested.timeColumn ? {} : { stepS: 0.02 })
    },
    channels: [{
      valueColumn: suggested.valueColumn,
      applicationType: earthquake ? "UNIFORM_EXCITATION" : "NODAL_FORCE",
      ...(earthquake ? {} : { targetType: "NODE" as const, targetId: "101" }),
      component: earthquake ? "UX" : "UZ",
      quantity: earthquake ? "ACCELERATION" : "FORCE",
      sourceUnit: earthquake ? (suggestedUnit ?? "") : "kN",
      scale: 1
    }],
    solver
  };
}

export function isPollingStatus(status?: string): boolean {
  return Boolean(status && POLLING_STATUSES.includes(status));
}

export function isPolling(run?: AgentRun): boolean {
  return isPollingStatus(run?.status);
}

function hasBatchProgress(run?: AgentRun): boolean {
  return run?.jobProgress?.completedCases != null && run.jobProgress.totalCases != null;
}

/** 运行中缺少增强字段时保留上一份完整快照，终态和新完整帧始终优先。 */
export function mergeRunProgress(previous: AgentRun, latest: AgentRun): AgentRun {
  if (
    latest.status === "WAITING_JOB"
    && hasBatchProgress(previous)
    && !hasBatchProgress(latest)
  ) {
    return {
      ...latest,
      jobProgress: previous.jobProgress,
      jobProgressRefreshing: true
    };
  }
  return { ...latest, jobProgressRefreshing: false };
}

/** 后端消息转成视图消息；历史恢复和增量追加共用。 */
function toChatMessage(message: AgentSessionMessage): ChatMessage {
  return {
    id: message.messageId,
    role: message.role,
    content: message.content,
    createdAt: message.createdAt,
    messageType: message.messageType,
    approval: message.approval,
    runId: message.runId ?? null
  };
}

export const useChatStore = create<ChatState>((set, get) => ({
  sessions: [],
  messages: [],
  runHistory: {},
  busy: false,
  switchingSession: false,

  async loadSessions() {
    try {
      const response = await agentApi.listSessions();
      set({ sessions: response.data ?? [] });
    } catch (reason) {
      set({ error: errorMessage(reason, "读取会话列表失败") });
    }
  },

  newSession() {
    sessionRequestVersion += 1;
    refreshRequestVersion += 1;
    set({
      activeSessionId: undefined,
      messages: [],
      run: undefined,
      runHistory: {},
      loadImport: undefined,
      mapping: undefined,
      busy: false,
      switchingSession: false,
      error: undefined
    });
  },

  async switchSession(sessionId: string) {
    if (get().activeSessionId === sessionId) return;
    const requestVersion = ++sessionRequestVersion;
    refreshRequestVersion += 1;
    set({
      activeSessionId: sessionId,
      messages: [],
      run: undefined,
      runHistory: {},
      loadImport: undefined,
      mapping: undefined,
      busy: true,
      switchingSession: true,
      error: undefined
    });
    try {
      const detail = await agentApi.getSession(sessionId);
      if (get().activeSessionId !== sessionId || requestVersion !== sessionRequestVersion) return;
      // 后端按 updatedAt 倒序返回，第一条才是最近的活动 run。
      const runs = detail.runs ?? [];
      set({
        messages: (detail.messages ?? []).map(toChatMessage),
        run: runs.length > 0 ? runs[0] : undefined,
        runHistory: Object.fromEntries(runs.map(item => [item.runId, item])),
        loadImport: undefined,
        mapping: undefined,
        busy: false,
        switchingSession: false
      });
    } catch (reason) {
      if (get().activeSessionId !== sessionId || requestVersion !== sessionRequestVersion) return;
      set({ busy: false, switchingSession: false, error: errorMessage(reason, "读取会话失败") });
    }
  },

  async deleteSession(sessionId: string) {
    if (get().deletingSessionId) return;
    set({ deletingSessionId: sessionId, error: undefined });
    try {
      await agentApi.deleteSession(sessionId);
      const deletingActive = get().activeSessionId === sessionId;
      if (deletingActive) {
        sessionRequestVersion += 1;
        refreshRequestVersion += 1;
      }
      set(state => ({
        sessions: state.sessions.filter(item => item.sessionId !== sessionId),
        ...(deletingActive ? {
          activeSessionId: undefined,
          messages: [],
          run: undefined,
          runHistory: {},
          loadImport: undefined,
          mapping: undefined,
          busy: false,
          switchingSession: false
        } : {}),
        deletingSessionId: undefined
      }));
    } catch (reason) {
      if (get().deletingSessionId !== sessionId) return;
      set({
        deletingSessionId: undefined,
        error: errorMessage(reason, "删除会话失败")
      });
    }
  },

  async send(content: string, file?: File) {
    const trimmed = content.trim();
    if (!trimmed || get().busy) return;

    const optimistic: ChatMessage = {
      id: nextId("local"),
      role: "USER",
      content: trimmed,
      createdAt: new Date().toISOString(),
      pending: true
    };
    set(state => ({
      busy: true,
      error: undefined,
      messages: [...state.messages, optimistic]
    }));

    const requestVersion = ++sessionRequestVersion;
    let sessionId = get().activeSessionId;
    let createdSession = false;
    try {
      if (!sessionId) {
        sessionId = (await agentApi.createSession(SESSION_TITLE)).sessionId;
        if (requestVersion !== sessionRequestVersion || get().activeSessionId !== undefined) return;
        set({ activeSessionId: sessionId });
        createdSession = true;
      }

      let upload: LoadImport | undefined;
      if (file) {
        upload = await agentApi.uploadFile(file);
      }
      if (requestVersion !== sessionRequestVersion || get().activeSessionId !== sessionId) return;

      const run = await agentApi.streamMessage(
        sessionId,
        trimmed,
        upload?.fileId,
        "AUTO",
        event => {
          if (
            event.type !== "run"
            && event.type !== "progress"
            && event.type !== "complete"
          ) return;
          if (requestVersion !== sessionRequestVersion || get().activeSessionId !== sessionId) return;
          set(state => ({
            run: event.run,
            runHistory: { ...state.runHistory, [event.run.runId]: event.run }
          }));
        }
      );
      if (requestVersion !== sessionRequestVersion || get().activeSessionId !== sessionId) return;

      // 用服务端消息替换乐观消息，确保 id/时间与后端一致。
      const detail = await agentApi.getSession(sessionId);
      if (requestVersion !== sessionRequestVersion || get().activeSessionId !== sessionId) return;
      set(state => ({
        run,
        // 某些旧服务端只返回最近一次 run，不能因此抹掉当前会话已展示的历史结果。
        runHistory: {
          ...state.runHistory,
          ...Object.fromEntries((detail.runs ?? []).map(item => [item.runId, item])),
          [run.runId]: run,
        },
        loadImport: upload,
        mapping: upload ? buildInitialMapping(run, upload) : undefined,
        messages: (detail.messages ?? []).map(toChatMessage),
        busy: false
      }));
      await get().loadSessions();
      if (createdSession) {
        const refreshGeneratedTitle = (remainingAttempts: number) => {
          const attemptIndex = TITLE_REFRESH_ATTEMPTS - remainingAttempts;
          const delayMs = TITLE_REFRESH_DELAY_MS * 2 ** attemptIndex;
          setTimeout(() => {
            if (get().activeSessionId !== sessionId) return;
            void get().loadSessions().then(() => {
              if (get().activeSessionId !== sessionId) return;
              const title = get().sessions.find(item => item.sessionId === sessionId)?.title;
              if (remainingAttempts > 1 && title === SESSION_TITLE) {
                refreshGeneratedTitle(remainingAttempts - 1);
              }
            });
          }, delayMs);
        };
        refreshGeneratedTitle(TITLE_REFRESH_ATTEMPTS);
      }
    } catch (reason) {
      if (requestVersion !== sessionRequestVersion || get().activeSessionId !== sessionId) return;
      set(state => ({
        busy: false,
        error: errorMessage(reason, "发送失败"),
        messages: state.messages.filter(message => message.id !== optimistic.id)
      }));
    }
  },

  updateMapping(mapping: LoadMappingV2Payload) {
    set({ mapping });
  },

  async submitMapping() {
    const { loadImport, mapping, busy, activeSessionId } = get();
    if (!loadImport || !mapping || busy) return;
    set({ busy: true, error: undefined });
    try {
      const response = await agentApi.setMapping(loadImport.importId, mapping);
      const detail = activeSessionId ? await agentApi.getSession(activeSessionId) : undefined;
      if (get().activeSessionId !== activeSessionId) return;
      set({
        run: response.run,
        runHistory: { ...get().runHistory, [response.run.runId]: response.run },
        messages: detail ? (detail.messages ?? []).map(toChatMessage) : get().messages,
        busy: false
      });
    } catch (reason) {
      if (get().activeSessionId !== activeSessionId) return;
      set({ busy: false, error: errorMessage(reason, "字段映射校验失败") });
    }
  },

  async decide(approvalId: string, approved: boolean) {
    const { activeSessionId, busy } = get();
    if (!approvalId || busy) return;
    set({ busy: true, error: undefined });
    try {
      const response = await agentApi.decideApproval(approvalId, approved);
      const detail = activeSessionId ? await agentApi.getSession(activeSessionId) : undefined;
      if (get().activeSessionId !== activeSessionId) return;
      set({
        run: response.run,
        runHistory: { ...get().runHistory, [response.run.runId]: response.run },
        messages: detail ? (detail.messages ?? []).map(toChatMessage) : get().messages,
        busy: false
      });
    } catch (reason) {
      if (get().activeSessionId !== activeSessionId) return;
      set({ busy: false, error: errorMessage(reason, "审批提交失败") });
    }
  },

  async cancel() {
    const { run, busy, activeSessionId } = get();
    if (!run || busy) return;
    set({ busy: true, error: undefined });
    try {
      const cancelled = await agentApi.cancelRun(run.runId);
      if (get().activeSessionId !== activeSessionId || get().run?.runId !== run.runId) return;
      set(state => ({
        run: cancelled,
        runHistory: { ...state.runHistory, [cancelled.runId]: cancelled },
        busy: false
      }));
    } catch (reason) {
      if (get().activeSessionId !== activeSessionId || get().run?.runId !== run.runId) return;
      set({ busy: false, error: errorMessage(reason, "取消运行失败") });
    }
  },

  async refreshRun() {
    const { run, activeSessionId } = get();
    if (!run) return;
    if (refreshInFlightRunId === run.runId) return;
    refreshInFlightRunId = run.runId;
    const requestVersion = ++refreshRequestVersion;
    try {
      const latest = await agentApi.getRun(run.runId);
      if (
        requestVersion !== refreshRequestVersion
        || get().activeSessionId !== activeSessionId
        || get().run?.runId !== run.runId
      ) return;
      const displayRun = mergeRunProgress(run, latest);
      set(state => ({
        run: displayRun,
        runHistory: { ...state.runHistory, [displayRun.runId]: displayRun }
      }));
      // 求解完成后后端会补写助手消息，拉一次会话把它接进对话流。
      if (!isPolling(latest) && activeSessionId) {
        const detail = await agentApi.getSession(activeSessionId);
        if (
          requestVersion !== refreshRequestVersion
          || get().activeSessionId !== activeSessionId
          || get().run?.runId !== run.runId
        ) return;
        set(state => ({
          messages: (detail.messages ?? []).map(toChatMessage),
          runHistory: {
            ...state.runHistory,
            ...Object.fromEntries((detail.runs ?? []).map(item => [item.runId, item])),
          },
        }));
      }
    } catch (reason) {
      if (
        requestVersion !== refreshRequestVersion
        || get().activeSessionId !== activeSessionId
        || get().run?.runId !== run.runId
      ) return;
      set({ error: errorMessage(reason, "读取任务状态失败") });
    } finally {
      if (refreshInFlightRunId === run.runId) refreshInFlightRunId = undefined;
    }
  },

  dismissError() {
    set({ error: undefined });
  }
}));
