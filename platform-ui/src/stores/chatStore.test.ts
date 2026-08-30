import { afterEach, describe, expect, it, vi } from "vitest";

import {
  agentApi,
  type AgentRun,
  type LoadImport,
  type LoadSourceUnit,
  type UnitSource
} from "../api/agentApi";
import { buildInitialMapping, isPolling, useChatStore } from "./chatStore";

const run: AgentRun = {
  runId: "agr_1",
  sessionId: "ags_1",
  goal: "标准化附件地震荷载",
  status: "WAITING_MAPPING",
  currentStage: "LOAD_MAPPING",
  artifactIds: [],
  intent: { loadKind: "EARTHQUAKE", solver: "OPENSEESPY_INPROC" }
};

const upload: LoadImport = {
  importId: "loadimp_1",
  fileId: "file_1",
  fileArtifactId: "art_1",
  fileName: "eq.csv",
  sourceSha256: "b".repeat(64),
  status: "INSPECTED",
  inspection: {
    fileName: "eq.csv",
    format: "CSV",
    rowCount: 2,
    columnCount: 2,
    columns: [
      { name: "time", numericCount: 2, missingCount: 0, min: 0, max: 0.02, timeCandidate: true },
      { name: "acc", numericCount: 2, missingCount: 0, min: -0.3, max: 0.3, timeCandidate: false }
    ],
    sampleRows: [],
    sheetName: null,
    encoding: "utf-8",
    delimiter: ","
  }
};

afterEach(() => {
  vi.restoreAllMocks();
  useChatStore.setState({
    activeSessionId: undefined,
    messages: [],
    runHistory: {},
    run: undefined,
    loadImport: undefined,
    mapping: undefined,
    busy: false,
    switchingSession: false,
    deletingSessionId: undefined,
    error: undefined
  });
});

describe("deleteSession", () => {
  it("删除当前会话后清空上下文并从历史列表移除", async () => {
    vi.spyOn(agentApi, "deleteSession").mockResolvedValue({
      sessionId: "ags_1",
      deleted: true,
      retainedRunCount: 1,
      cancelledRunCount: 0
    });
    useChatStore.setState({
      sessions: [
        { sessionId: "ags_1", title: "当前会话", status: "ACTIVE", createdAt: "2026-08-11", updatedAt: "2026-08-11" },
        { sessionId: "ags_2", title: "保留会话", status: "ACTIVE", createdAt: "2026-08-10", updatedAt: "2026-08-10" }
      ],
      activeSessionId: "ags_1",
      messages: [{ id: "msg_1", role: "USER", content: "测试", createdAt: "2026-08-11" }],
      run
    });

    await useChatStore.getState().deleteSession("ags_1");

    expect(useChatStore.getState()).toMatchObject({
      sessions: [expect.objectContaining({ sessionId: "ags_2" })],
      activeSessionId: undefined,
      messages: [],
      run: undefined,
      deletingSessionId: undefined
    });
  });

  it("删除非当前会话时保留正在查看的上下文", async () => {
    vi.spyOn(agentApi, "deleteSession").mockResolvedValue({
      sessionId: "ags_2",
      deleted: true,
      retainedRunCount: 0,
      cancelledRunCount: 0
    });
    useChatStore.setState({
      sessions: [
        { sessionId: "ags_1", title: "当前会话", status: "ACTIVE", createdAt: "2026-08-11", updatedAt: "2026-08-11" },
        { sessionId: "ags_2", title: "待删除会话", status: "ACTIVE", createdAt: "2026-08-10", updatedAt: "2026-08-10" }
      ],
      activeSessionId: "ags_1",
      messages: [{ id: "msg_1", role: "USER", content: "保留这段上下文", createdAt: "2026-08-11" }],
      run
    });

    await useChatStore.getState().deleteSession("ags_2");

    expect(useChatStore.getState()).toMatchObject({
      sessions: [expect.objectContaining({ sessionId: "ags_1" })],
      activeSessionId: "ags_1",
      messages: [expect.objectContaining({ content: "保留这段上下文" })],
      run,
      deletingSessionId: undefined
    });
  });
});

describe("isPolling", () => {
  it("仅在等待求解或复核时轮询", () => {
    expect(isPolling({ ...run, status: "WAITING_JOB" })).toBe(true);
    expect(isPolling({ ...run, status: "REVIEWING" })).toBe(true);
    expect(isPolling({ ...run, status: "SUCCEEDED" })).toBe(false);
    expect(isPolling({ ...run, status: "WAITING_APPROVAL" })).toBe(false);
    expect(isPolling(undefined)).toBe(false);
  });
});

/** 给上传件挂上后端推断结果，只改单位来源与单位本身。 */
const withSuggestedUnit = (sourceUnit: LoadSourceUnit, unitSource: UnitSource): LoadImport => ({
  ...upload,
  inspection: {
    ...upload.inspection,
    suggestedMapping: {
      mapping: {
        runId: "",
        loadKind: "EARTHQUAKE",
        time: { column: "time", unit: "s" },
        channels: [{
          valueColumn: "acc",
          applicationType: "UNIFORM_EXCITATION",
          component: "UX",
          quantity: "ACCELERATION",
          sourceUnit,
          scale: 1
        }],
        solver: "OPENSEESPY_INPROC"
      },
      confidence: "HIGH",
      reasons: [],
      warnings: [],
      alternatives: {},
      unitSource,
      standardizeDecision: "AUTO"
    }
  }
});

describe("buildInitialMapping", () => {
  it("地震荷载按一致激励和加速度给出初值", () => {
    const mapping = buildInitialMapping(run, withSuggestedUnit("g", "DECLARED_IN_HEADER"));

    expect(mapping.loadKind).toBe("EARTHQUAKE");
    expect(mapping.solver).toBe("OPENSEESPY_INPROC");
    expect(mapping.time.column).toBe("time");
    expect(mapping.channels[0]).toMatchObject({
      valueColumn: "acc",
      applicationType: "UNIFORM_EXCITATION",
      quantity: "ACCELERATION",
      sourceUnit: "g",
      component: "UX"
    });
    // 一致激励不针对单个节点，不应带 targetId。
    expect(mapping.channels[0].targetId).toBeUndefined();
  });

  it("单位取后端读出的声明，不写死 g", () => {
    // 曾经这里写死 "g"：m/s² 的记录会预填成 g，用户确认下去就是 9.8 倍荷载误差，
    // 而且带着完全合法的审批哈希。
    const mapping = buildInitialMapping(run, withSuggestedUnit("m/s2", "DECLARED_IN_HEADER"));

    expect(mapping.channels[0].sourceUnit).toBe("m/s2");
  });

  it("后端读不出单位时留空，强制用户主动选", () => {
    // upload 不带 suggestedMapping，等价于后端判不出单位。留空比默认一个安全：
    // 默认值会被当成"系统已识别"直接确认掉。
    expect(buildInitialMapping(run, upload).channels[0].sourceUnit).toBe("");
  });

  it("非地震荷载回退为节点力并给出目标节点", () => {
    const mapping = buildInitialMapping(
      { ...run, intent: { loadKind: "WIND", solver: "ANSYS" } },
      upload
    );

    expect(mapping.loadKind).toBe("WIND");
    expect(mapping.solver).toBe("ANSYS");
    expect(mapping.channels[0]).toMatchObject({
      applicationType: "NODAL_FORCE",
      quantity: "FORCE",
      sourceUnit: "kN",
      targetType: "NODE"
    });
  });

  it("没有时间列时补默认时间步，避免后端校验失败", () => {
    const noTime: LoadImport = {
      ...upload,
      inspection: {
        ...upload.inspection,
        columns: [{ name: "acc", numericCount: 2, missingCount: 0, min: -1, max: 1, timeCandidate: false }]
      }
    };

    const mapping = buildInitialMapping(run, noTime);

    expect(mapping.time.column).toBeNull();
    expect(mapping.time.stepS).toBe(0.02);
  });
});

describe("refreshRun", () => {
  it("同一运行的前一次轮询未结束时不重复请求后端", async () => {
    let resolveRun!: (value: AgentRun) => void;
    const getRun = vi.spyOn(agentApi, "getRun").mockReturnValue(new Promise(resolve => {
      resolveRun = resolve;
    }));
    useChatStore.setState({ activeSessionId: "ags_1", run: { ...run, status: "WAITING_JOB" } });

    const firstRefresh = useChatStore.getState().refreshRun();
    const overlappingRefresh = useChatStore.getState().refreshRun();

    expect(getRun).toHaveBeenCalledTimes(1);
    resolveRun({ ...run, status: "WAITING_JOB" });
    await Promise.all([firstRefresh, overlappingRefresh]);

    await useChatStore.getState().refreshRun();
    expect(getRun).toHaveBeenCalledTimes(2);
  });

  it("切换会话后丢弃旧会话的迟到轮询响应", async () => {
    let resolveRun!: (value: AgentRun) => void;
    vi.spyOn(agentApi, "getRun").mockReturnValue(new Promise(resolve => {
      resolveRun = resolve;
    }));
    useChatStore.setState({ activeSessionId: "ags_1", run: { ...run, status: "WAITING_JOB" } });

    const pendingRefresh = useChatStore.getState().refreshRun();
    const sessionTwoRun = { ...run, runId: "agr_2", sessionId: "ags_2", status: "WAITING_APPROVAL" };
    useChatStore.setState({ activeSessionId: "ags_2", run: sessionTwoRun });
    resolveRun({ ...run, status: "WAITING_JOB" });
    await pendingRefresh;

    expect(useChatStore.getState().activeSessionId).toBe("ags_2");
    expect(useChatStore.getState().run).toEqual(sessionTwoRun);
  });
});

describe("switchSession", () => {
  it("慢请求期间保留明确的会话加载状态", async () => {
    let resolveSession!: (value: Awaited<ReturnType<typeof agentApi.getSession>>) => void;
    vi.spyOn(agentApi, "getSession").mockReturnValue(new Promise(resolve => {
      resolveSession = resolve;
    }));

    const pendingSwitch = useChatStore.getState().switchSession("ags_slow");

    expect(useChatStore.getState()).toMatchObject({
      activeSessionId: "ags_slow",
      busy: true,
      switchingSession: true
    });

    resolveSession({
      sessionId: "ags_slow",
      title: "慢会话",
      status: "ACTIVE",
      createdAt: "2026-08-10T00:00:00Z",
      updatedAt: "2026-08-10T00:00:00Z",
      messages: [],
      runs: []
    });
    await pendingSwitch;

    expect(useChatStore.getState()).toMatchObject({
      activeSessionId: "ags_slow",
      busy: false,
      switchingSession: false
    });
  });
});

describe("send", () => {
  it("流式结果查询到达时立即更新运行卡片，不等待最终完成", async () => {
    const progressRun: AgentRun = {
      ...run,
      runId: "agr_inquiry",
      taskType: "INQUIRY",
      status: "PLANNING",
      currentStage: "QUERY",
      resultSummary: {
        inquiryMetrics: [{
          metricId: "max_girder_end_displacement",
          label: "最大梁端位移",
          sourceColumn: "displacement",
          peakAbsolute: 0.39193,
          peakSigned: 0.39193,
          unit: "m",
          peakTimeS: 20.05,
          sampleCount: 4001
        }],
        queryProgress: { completed: 1, message: "已读取 1 项结果指标" }
      }
    };
    const completedRun: AgentRun = {
      ...progressRun,
      status: "SUCCEEDED",
      currentStage: "COMPLETED"
    };
    let stateDuringProgress: AgentRun | undefined;
    vi.spyOn(agentApi, "streamMessage").mockImplementation(async (_sessionId, _content, _fileId, _taskType, onEvent) => {
      onEvent({ type: "progress", run: progressRun });
      stateDuringProgress = useChatStore.getState().run;
      onEvent({ type: "complete", run: completedRun });
      return completedRun;
    });
    vi.spyOn(agentApi, "getSession").mockResolvedValue({
      sessionId: "ags_1",
      title: "结果查询",
      status: "ACTIVE",
      createdAt: "2026-08-12T00:00:00Z",
      updatedAt: "2026-08-12T00:00:01Z",
      messages: [],
      runs: [completedRun]
    });
    vi.spyOn(agentApi, "listSessions").mockResolvedValue({ data: [] });
    useChatStore.setState({ activeSessionId: "ags_1" });

    await useChatStore.getState().send("给出所有峰值");

    expect(stateDuringProgress?.status).toBe("PLANNING");
    expect(stateDuringProgress?.resultSummary?.inquiryMetrics).toHaveLength(1);
    expect(useChatStore.getState()).toMatchObject({
      busy: false,
      run: { runId: "agr_inquiry", status: "SUCCEEDED" }
    });
    expect(useChatStore.getState().runHistory.agr_inquiry).toMatchObject({ status: "SUCCEEDED" });
  });

  it("连续追问时保留每个查询 run 的历史快照", async () => {
    const firstRun: AgentRun = {
      ...run,
      runId: "agr_first",
      taskType: "INQUIRY",
      status: "SUCCEEDED",
      resultSummary: { inquiryMetrics: [] }
    };
    const secondRun: AgentRun = {
      ...firstRun,
      runId: "agr_second",
      goal: "继续查询塔底剪力"
    };
    vi.spyOn(agentApi, "streamMessage")
      .mockImplementationOnce(async (_sessionId, _content, _fileId, _taskType, onEvent) => {
        onEvent({ type: "complete", run: firstRun });
        return firstRun;
      })
      .mockImplementationOnce(async (_sessionId, _content, _fileId, _taskType, onEvent) => {
        onEvent({ type: "complete", run: secondRun });
        return secondRun;
      });
    vi.spyOn(agentApi, "getSession").mockResolvedValue({
      sessionId: "ags_1",
      title: "连续追问",
      status: "ACTIVE",
      createdAt: "2026-08-12T00:00:00Z",
      updatedAt: "2026-08-12T00:00:01Z",
      messages: [
        { messageId: "msg_first", sessionId: "ags_1", role: "ASSISTANT", content: "第一项结果", runId: "agr_first", createdAt: "2026-08-12T00:00:01Z" },
        { messageId: "msg_second", sessionId: "ags_1", role: "ASSISTANT", content: "第二项结果", runId: "agr_second", createdAt: "2026-08-12T00:00:02Z" }
      ],
      runs: [secondRun, firstRun]
    });
    vi.spyOn(agentApi, "listSessions").mockResolvedValue({ data: [] });
    useChatStore.setState({ activeSessionId: "ags_1" });

    await useChatStore.getState().send("查询第一项");
    await useChatStore.getState().send("继续查询第二项");

    expect(useChatStore.getState().runHistory).toMatchObject({
      agr_first: { runId: "agr_first" },
      agr_second: { runId: "agr_second" }
    });
    expect(useChatStore.getState().messages.filter(message => message.runId)).toHaveLength(2);
  });
});
