const API_BASE = (import.meta.env.VITE_API_BASE || "").replace(/\/$/, "");

export interface LoadColumnProfile {
  name: string;
  numericCount: number;
  missingCount: number;
  min: number | null;
  max: number | null;
  timeCandidate: boolean;
}

/** 单位是读出来的还是猜出来的。界面要如实告诉用户这个区别。 */
export type UnitSource =
  | "DECLARED_IN_HEADER"
  | "DECLARED_IN_COLUMN_NAME"
  | "DECLARED_IN_FILE_NAME"
  | "DECLARED_IN_PREAMBLE"
  | "GUESSED_FROM_MAGNITUDE"
  | "UNKNOWN";

/** 单位来源的中文说明。两处映射表单共用一份，措辞不能各自漂移。 */
export const UNIT_SOURCE_TEXT: Record<UnitSource, string> = {
  DECLARED_IN_HEADER: "文件头声明",
  DECLARED_IN_COLUMN_NAME: "列名声明",
  DECLARED_IN_FILE_NAME: "文件名声明（非文件内容，请核对）",
  DECLARED_IN_PREAMBLE: "文件说明文字声明（已回验原文与量级）",
  GUESSED_FROM_MAGNITUDE: "按数值量级推断，文件未声明，请核对",
  UNKNOWN: "无法判定，请手动指定"
};

/** 后端推断出的候选映射。只用于预填表单，生效值仍由用户提交后冻结。 */
export interface SuggestedMapping {
  mapping: LoadMappingV2Payload;
  confidence: "HIGH" | "MEDIUM" | "LOW";
  reasons: string[];
  warnings: string[];
  alternatives: { valueColumns?: string[]; sourceUnits?: string[] };
  unitSource: UnitSource;
  standardizeDecision: "AUTO" | "ASK";
}

export interface LoadInspection {
  fileName: string;
  format: string;
  rowCount: number;
  columnCount: number;
  columns: LoadColumnProfile[];
  sampleRows: Array<Record<string, string>>;
  sheetName: string | null;
  encoding: string | null;
  delimiter: string | null;
  /** 后端推断的候选映射；旧后端不返回，前端必须容忍缺失。 */
  suggestedMapping?: SuggestedMapping;
}

export interface LoadImport {
  importId: string;
  fileId: string;
  fileArtifactId: string;
  fileName: string;
  sourceSha256: string;
  status: string;
  inspection: LoadInspection;
}

export interface AgentApproval {
  approvalId: string;
  runId: string;
  action: "STANDARDIZE_LOAD" | "RUN_SOLVER" | "RUN_FULL_OPTIMIZATION" | "RUN_ENGINEERING_WORKFLOW" | "RUN_DAMPER_COMPARISON" | "RUN_DAMPER_PARAMETER_SWEEP";
  status: string;
  summary: string;
  narrativeMode?: "LLM" | "TEMPLATE_FALLBACK" | null;
  narrativeFallbackReason?: string | null;
  frozenAction?: Record<string, unknown>;
  frozenActionSha256?: string;
}

export interface ApprovalUpdatePayload {
  damperType?: "VISCOUS" | "FRICTION" | "EDDY_CURRENT";
  damperKind?: "VISCOUS" | "FRICTION" | "EDDY_CURRENT";
  damperTypes?: Array<"VISCOUS" | "FRICTION" | "EDDY_CURRENT">;
  selectedLayoutId?: string;
  responseIds?: string[];
  budget?: { doeDesignCount?: number };
}

export type AgentTaskType = "AUTO" | "ANALYSIS" | "DAMPER_OPTIMIZATION" | "DAMPER_COMPARISON" | "DAMPER_PARAMETER_SWEEP" | "LOAD_IMPORT" | "FULL_OPTIMIZATION";
export type AgentRunTaskType = AgentTaskType | "CONVERSATION" | "INQUIRY" | "UNSUPPORTED" | "LLM_UNAVAILABLE" | "WORKFLOW_HARNESS";

export type AgentInputSource = "USER_DECISION" | "VERIFIED_TEMPLATE" | "FILE_DERIVED" | "OPERATIONAL_DEFAULT";

export interface AgentInputProvenanceItem {
  field: string;
  source: AgentInputSource;
  value: unknown;
}

export interface SolverVersionProfile {
  schemaVersion: "1.0";
  solver: { name: string; version: string; versionSource: string };
  sdk: { package: string; version: string };
  responseContract: { id: string; version: string; description: string };
  userElement?: {
    name: string;
    module?: string;
    calibrationStatus?: string;
    calibrationSha256?: string;
    calibrationHashVerified?: boolean;
  };
}

export interface FullOptimizationPreflight {
  passed: boolean;
  readiness: {
    status: string;
    blockingComponents: string[];
    components: Record<string, { status: string; required: boolean; detail: string }>;
  };
  config: Record<string, unknown>;
  solverVersionProfile?: SolverVersionProfile;
}

export interface AgentResultArtifact {
  artifactId: string;
  name: string;
  kind: string;
  sizeBytes?: number;
}

export interface AgentRuntimeMetadata {
  name: "MOMO_TYPED_AGENT";
  version: "1";
  recentMessageCount: number;
  tools: string[];
}

export interface WorkflowStepSnapshot {
  stepId: string;
  title: string;
  allowedTools: string[];
  prerequisites?: string[];
  successGate?: string;
  onSuccess?: string | null;
  failureRoutes?: Record<string, string>;
}

export interface WorkflowSnapshot {
  workflowId: string;
  version: string;
  initialStep: string;
  terminalSteps: string[];
  steps: WorkflowStepSnapshot[];
  limits?: Record<string, number>;
}

export interface AgentToolCallTrace {
  toolCallId: string;
  toolName: string;
  stepId: string;
  status: string;
  jobId?: string | null;
  artifactIds?: string[];
  error?: Record<string, unknown> | null;
}

export interface AgentRun {
  runId: string;
  sessionId: string;
  goal: string;
  taskType?: AgentRunTaskType;
  plannerMode?: "LLM" | "LLM_TOOL_CALL";
  agentRuntime?: AgentRuntimeMetadata;
  runtimeMode?: "LEGACY" | "WORKFLOW_HARNESS";
  workflowId?: string;
  workflowVersion?: string;
  workflowSha256?: string;
  workflowSnapshot?: WorkflowSnapshot;
  currentStep?: string;
  completedSteps?: string[];
  stepAttempt?: number;
  activeToolCallId?: string | null;
  toolCalls?: AgentToolCallTrace[];
  status: string;
  currentStage: string;
  importId?: string;
  jobId?: string;
  artifactIds: string[];
  reportArtifactId?: string;
  llmFailure?: { stage: string; reason: string; detail?: string | null };
  pendingApproval?: AgentApproval | null;
  intent?: Record<string, unknown>;
  plan?: string[];
  preflight?: FullOptimizationPreflight;
  workflowContract?: Record<string, unknown>;
  solverVersionProfile?: SolverVersionProfile;
  inputProvenance?: AgentInputProvenanceItem[];
  outputManifestArtifactId?: string;
  figureArtifactIds?: string[];
  resultArtifacts?: AgentResultArtifact[];
  jobProgress?: {
    phase: string;
    message: string;
    percent?: number | null;
    completedCases?: number | null;
    totalCases?: number | null;
    activeCases?: Array<{
      caseId: string;
      percent: number;
      step?: number | null;
      totalSteps?: number | null;
      phase?: string | null;
    }> | null;
  };
  /** 客户端保留上一份完整求解进度时的瞬时轮询状态。 */
  jobProgressRefreshing?: boolean;
  resultSummary?: {
    message?: string;
    evidenceMode?: string;
    accepted?: boolean;
    validationStatus?: Record<string, unknown>;
    reviewStatus?: Record<string, unknown>;
    finalRecommendationStatus?: string;
    checks?: Record<string, boolean>;
    caseResults?: Array<Record<string, unknown>>;
    objectives?: Record<string, number>;
    baselineObjectives?: Record<string, number>;
    recommendedObjectives?: Record<string, number>;
    recommendedParameters?: Record<string, number>;
    objectiveChanges?: Record<string, Record<string, number | null>>;
    responseComparison?: Record<string, unknown>;
    sampleResponses?: Array<Record<string, unknown>>;
    figures?: Array<{ claim: string; artifactId: string; metrics: string[] }>;
    figureArtifactIds?: string[];
    narrativeSummary?: string;
    narrativeMode?: "LLM" | "TEMPLATE_FALLBACK" | "DETERMINISTIC";
    narrativeFallbackReason?: string | null;
    inquiryMetrics?: InquiryMetric[];
    inquiryTopsis?: InquiryTopsisRow[];
    inquiryTopsisWeights?: InquiryTopsisWeights;
    inquiryRunComparison?: RunComparisonResult;
    queryProgress?: { completed: number; message: string };
  };
  resultMetadata?: {
    runId: string;
    sessionId: string;
    taskType: string;
    status: string;
    condition?: string | null;
    model?: string | null;
    solver?: string | null;
    hasDamper: boolean;
    damperTypes: string[];
    damperParameters: Record<string, unknown>;
    updatedAt?: string | null;
  };
}

export interface InquiryMetric {
  metricId: string;
  label: string;
  sourceColumn: string;
  peakAbsolute: number;
  peakSigned: number;
  unit: string;
  peakTimeS: number | null;
  sampleCount: number;
}

export interface InquiryTopsisRow {
  rank: number;
  paretoIndex: number;
  score: number;
  parameters: Record<string, number>;
  objectives: Record<string, number>;
}

export interface InquiryTopsisWeights {
  objectiveNames: string[];
  weights: number[];
}

export type RunComparisonCompatibility = "DIRECT" | "CROSS_SOLVER" | "LIMITED" | "NOT_COMPARABLE";

export interface RunComparisonMetric {
  value: number;
  unit: string;
  label: string;
  direction: "LOWER_IS_BETTER" | "HIGHER_IS_BETTER";
  evidence: Record<string, unknown>;
}

export interface RunComparisonRun {
  targetKey: string;
  runId: string;
  taskType: string;
  solver?: string | null;
  loadKind?: string | null;
  modelArtifactId?: string | null;
  modelSha256?: string | null;
  modelIdentity?: string | null;
  loadArtifactId?: string | null;
  loadSha256?: string | null;
  loadIdentity?: string | null;
  responseIds: string[];
  reportArtifactId?: string | null;
  caseId?: string;
  candidateRank?: number;
  metrics: Record<string, RunComparisonMetric>;
}

export interface RunComparisonDelta {
  baseline: number;
  candidate: number;
  difference: number;
  relativeChange: number | null;
  relativeChangePercent: number | null;
  unit: string;
  interpretation: "PERFORMANCE_CHANGE" | "SOLVER_DIFFERENCE";
}

export interface RunComparisonPair {
  baselineTargetKey: string;
  targetKey: string;
  baselineRunId: string;
  runId: string;
  caseId?: string;
  candidateRank?: number;
  compatibility: RunComparisonCompatibility;
  metrics: Record<string, RunComparisonDelta>;
}

export interface RunComparisonResult {
  schemaVersion: "1.0";
  sessionId: string;
  projectId: string | null;
  baselineRunId: string | null;
  compatibility: RunComparisonCompatibility;
  metricIds: string[];
  runs: RunComparisonRun[];
  comparisons: RunComparisonPair[];
  rankings: Array<{
    metricId: string;
    direction: string;
    rows: Array<{ rank: number; targetKey: string; runId: string; caseId?: string; candidateRank?: number; value: number }>;
  }>;
  warnings: string[];
  interpretationLimit: string;
}

export type AgentMessageStreamEvent =
  | { type: "accepted"; sessionId: string }
  | { type: "run" | "progress" | "complete"; run: AgentRun }
  | { type: "error"; error: { code: string; message: string } };

export interface TimeseriesPeak {
  column: string;
  peakAbsolute: number;
  peakSigned: number;
  peakTime: number | null;
  sampleCount: number;
}

export interface TimeseriesResponse {
  runId: string;
  availableColumns: string[];
  columns: string[];
  series: Record<string, Array<number | null>>;
  peaks: Record<string, TimeseriesPeak>;
}

export interface TimeseriesComparisonCase {
  caseId: string;
  parameters: Record<string, number | string>;
  label: string;
  availableColumns: string[];
  columns: string[];
  series: Record<string, Array<number | null>>;
  peaks: Record<string, TimeseriesPeak>;
}

export interface TimeseriesComparisonResponse {
  runId: string;
  availableColumns: string[];
  columns: string[];
  cases: TimeseriesComparisonCase[];
}

export interface LoadMappingPayload {
  runId: string;
  timeColumn: string | null;
  valueColumn: string;
  timeStepS?: number;
  timeUnit: "s" | "ms";
  targetType: "NODE" | "NODE_GROUP";
  targetId: string;
  component: "UX" | "UY" | "UZ";
  quantity: "FORCE" | "ACCELERATION";
  sourceUnit: "N" | "kN" | "g" | "m/s2";
  solver: "ANSYS" | "OPENSEESPY_INPROC";
}

/** 加速度输入单位。
 *
 * 界面只主动提供 g 与 m/s²，但文件自己声明 gal 或 mm/s² 时（PEER 头就会这么写）
 * 那个声明必须能提交，否则读得懂的单位反而被界面挡住。空串表示"还没定"——读不出
 * 单位时宁可留空让用户选，也不要默认一个，默认值会被当成系统的判断结果。
 */
export type LoadSourceUnit = "N" | "kN" | "g" | "m/s2" | "cm/s2" | "mm/s2" | "";

export interface LoadChannelMappingPayload {
  valueColumn: string;
  applicationType: "UNIFORM_EXCITATION" | "NODAL_FORCE";
  targetType?: "NODE" | "NODE_GROUP";
  targetId?: string;
  component: "UX" | "UY" | "UZ";
  quantity: "FORCE" | "ACCELERATION";
  sourceUnit: LoadSourceUnit;
  scale: number;
}

export interface LoadMappingV2Payload {
  runId: string;
  loadKind: "EARTHQUAKE" | "WIND" | "TRAFFIC" | "GENERIC_NODAL";
  time: {
    column: string | null;
    stepS?: number;
    unit: "s" | "ms";
  };
  channels: LoadChannelMappingPayload[];
  solver: "ANSYS" | "OPENSEESPY_INPROC";
}

async function request<T>(method: string, path: string, body?: unknown, raw = false): Promise<T> {
  const response = await fetch(`${API_BASE}/api/v1${path}`, {
    method,
    headers: raw ? { "Content-Type": "application/octet-stream" } : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : raw ? body as BodyInit : JSON.stringify(body)
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(payload?.error?.message || `请求失败（HTTP ${response.status}）`);
  }
  return response.json() as Promise<T>;
}

export function suggestLoadMapping(inspection: LoadInspection): Pick<LoadMappingPayload, "timeColumn" | "valueColumn"> {
  const timeColumn = inspection.columns.find(column => column.timeCandidate)?.name ?? null;
  const valueColumn = inspection.columns.find(column => column.name !== timeColumn && column.numericCount > 0)?.name
    ?? inspection.columns[0]?.name
    ?? "";
  return { timeColumn, valueColumn };
}

export interface AgentSessionSummary {
  sessionId: string;
  title: string;
  status: string;
  createdAt: string;
  updatedAt: string;
}

export interface AgentSessionMessage {
  messageId: string;
  sessionId: string;
  role: "USER" | "ASSISTANT";
  content: string;
  messageType?: "TEXT" | "APPROVAL";
  approvalId?: string | null;
  approval?: AgentApproval | null;
  runId?: string | null;
  fileId?: string | null;
  createdAt: string;
}

export interface AgentSessionDetail extends AgentSessionSummary {
  messages: AgentSessionMessage[];
  runs: AgentRun[];
}

export interface AgentSessionDeleteResult {
  sessionId: string;
  deleted: true;
  retainedRunCount: number;
  cancelledRunCount: number;
}

export const agentApi = {
  createSession(title: string): Promise<{ sessionId: string }> {
    return request("POST", "/agent/sessions", { title });
  },

  listSessions(): Promise<{ data: AgentSessionSummary[] }> {
    return request("GET", "/agent/sessions");
  },

  getSession(sessionId: string): Promise<AgentSessionDetail> {
    return request("GET", `/agent/sessions/${sessionId}`);
  },

  deleteSession(sessionId: string): Promise<AgentSessionDeleteResult> {
    return request("DELETE", `/agent/sessions/${sessionId}`);
  },

  uploadFile(file: File): Promise<LoadImport> {
    return request("POST", `/load-files?fileName=${encodeURIComponent(file.name)}`, file, true);
  },

  sendMessage(
    sessionId: string,
    content: string,
    fileId?: string,
    taskType: AgentTaskType = "AUTO"
  ): Promise<AgentRun> {
    return request("POST", `/agent/sessions/${sessionId}/messages`, {
      content,
      ...(fileId ? { fileId } : {}),
      taskType
    });
  },

  async streamMessage(
    sessionId: string,
    content: string,
    fileId: string | undefined,
    taskType: AgentTaskType,
    onEvent: (event: AgentMessageStreamEvent) => void
  ): Promise<AgentRun> {
    const response = await fetch(`${API_BASE}/api/v1/agent/sessions/${sessionId}/messages/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content, ...(fileId ? { fileId } : {}), taskType })
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => null);
      throw new Error(payload?.error?.message || `请求失败（HTTP ${response.status}）`);
    }
    if (!response.body) throw new Error("浏览器未提供流式响应通道");

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let completedRun: AgentRun | undefined;
    const consumeLine = (line: string) => {
      if (!line.trim()) return;
      const event = JSON.parse(line) as AgentMessageStreamEvent;
      onEvent(event);
      if (event.type === "complete") completedRun = event.run;
      if (event.type === "error") throw new Error(event.error.message);
    };

    while (true) {
      const { done, value } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";
      lines.forEach(consumeLine);
      if (done) break;
    }
    consumeLine(buffer);
    if (!completedRun) throw new Error("流式响应在任务完成前中断");
    return completedRun;
  },

  setMapping(importId: string, mapping: LoadMappingPayload | LoadMappingV2Payload): Promise<{ run: AgentRun; approval: AgentApproval }> {
    return request("POST", `/load-imports/${importId}/mapping`, mapping);
  },

  decideApproval(approvalId: string, approved: boolean): Promise<{ run: AgentRun; approval: AgentApproval }> {
    return request("POST", `/agent/approvals/${approvalId}/decision`, { approved });
  },

  updateApproval(runId: string, payload: ApprovalUpdatePayload): Promise<{ run: AgentRun; approval: AgentApproval }> {
    return request("PATCH", `/agent/runs/${runId}/approval`, payload);
  },

  getRun(runId: string): Promise<AgentRun> {
    return request("GET", `/agent/runs/${runId}`);
  },

  getRunTimeseries(runId: string, columns: string[] = [], maxPoints = 1000): Promise<TimeseriesResponse> {
    const params = new URLSearchParams({ maxPoints: String(maxPoints) });
    if (columns.length > 0) params.set("columns", columns.join(","));
    return request("GET", `/agent/runs/${runId}/timeseries?${params.toString()}`);
  },

  getRunTimeseriesComparison(
    runId: string,
    columns: string[] = [],
    caseIds: string[] = [],
    maxPoints = 1000
  ): Promise<TimeseriesComparisonResponse> {
    const params = new URLSearchParams({ maxPoints: String(maxPoints) });
    if (columns.length > 0) params.set("columns", columns.join(","));
    if (caseIds.length > 0) params.set("caseIds", caseIds.join(","));
    return request("GET", `/agent/runs/${runId}/timeseries/compare?${params.toString()}`);
  },

  getArtifact(artifactId: string): Promise<AgentResultArtifact> {
    return request("GET", `/artifacts/${artifactId}`);
  },

  cancelRun(runId: string): Promise<AgentRun> {
    return request("POST", `/agent/runs/${runId}/cancel`);
  },

  artifactDownloadUrl(artifactId: string): string {
    return `${API_BASE}/api/v1/artifacts/${artifactId}/download`;
  }
};
