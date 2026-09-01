import { mockDb } from "./mockDb";
import type {
  DashboardSummary,
  Template,
  PreflightResponse,
  Job,
  Artifact,
  ParityReport,
  PaginatedResponse,
  JobType,
  JobStatus,
  TrafficLoadSourceMode,
  EarthquakeSourceMode,
  SurrogateModelFamily,
  CommandStreamModuleConfig,
  SolverResourceConfig,
  ExperimentDesignRequest,
  SurrogateTrainingRequest,
  LoadCurveExportRequest,
  OptimizationExportRequest,
  OptimizationConstraint,
  OptimizationObjective,
  OptimizationObjectiveMode,
  EngineeringProjectConfig,
  DamperInstanceRegistryEntry,
  ResultExtractionMetricConfig,
  ModuleStatus,
  RealOptimizationSyncResult,
  LoadArtifactUpload,
  CapabilityCatalog
} from "./types";

const API_BASE = (import.meta.env.VITE_API_BASE || "").replace(/\/$/, "");
export const API_MODE = import.meta.env.VITE_API_MODE || "mock"; // "mock" or "live"
export const IS_MOCK_MODE = API_MODE === "mock";
const APP_VERSION = import.meta.env.VITE_APP_VERSION || "development";

const MOCK_CAPABILITIES: CapabilityCatalog = {
  version: "1.0.0",
  data: ["ANALYSIS", "DAMPER_COMPARISON", "DAMPER_PARAMETER_SWEEP", "DAMPER_OPTIMIZATION", "RESULT_INQUIRY", "SOLVER_BATCH", "RESULT_EXTRACTION", "EXPERIMENT_DESIGN", "SURROGATE_TRAINING", "ACTIVE_LEARNING", "MULTI_OBJECTIVE_OPTIMIZATION", "ENTROPY_TOPSIS_DECISION", "OPTIMIZATION_EXPORT"].map(jobType => ({
    jobType,
    mode: "MOCK",
    status: "MOCK_ONLY",
    handler: "mockDb",
    solvers: ["ANSYS", "OPENSEESPY_INPROC"],
    scenarios: ["EARTHQUAKE"],
    solverScenarios: {},
    inputArtifacts: [],
    outputArtifacts: [],
    supportsCancel: true,
    supportsResume: false,
    reason: "当前为模拟数据，仅用于演示。",
    unlockRequirements: []
  }))
};

const LIVE_GATED_JOB_TYPES = new Set<JobType>([
  "SOLVER_BATCH",
  "RESULT_EXTRACTION",
  "EXPERIMENT_DESIGN",
  "SURROGATE_TRAINING",
  "ACTIVE_LEARNING",
  "MULTI_OBJECTIVE_OPTIMIZATION",
  "ENTROPY_TOPSIS_DECISION",
  "OPTIMIZATION_EXPORT"
]);

export function resolveApiUrl(pathOrUrl: string): string {
  if (/^(https?:|blob:|data:)/.test(pathOrUrl)) {
    return pathOrUrl;
  }
  if (!API_BASE || !pathOrUrl.startsWith("/")) {
    return pathOrUrl;
  }
  return `${API_BASE}${pathOrUrl}`;
}

class ApiClientError extends Error {
  status: number;
  code: string;
  details?: any;

  constructor(status: number, error: { code: string; message: string; details?: any }) {
    super(error.message);
    this.status = status;
    this.code = error.code;
    this.details = error.details;
    this.name = "ApiClientError";
  }
}

// Request Helper
async function request<T>(method: string, path: string, body?: any): Promise<T> {
  const url = `${API_BASE}/api/v1${path}`;
  try {
    const res = await fetch(url, {
      method,
      headers: {
        "Content-Type": "application/json"
      },
      body: body ? JSON.stringify(body) : undefined
    });

    if (!res.ok) {
      let errData;
      try {
        errData = await res.json();
      } catch {
        errData = { error: { code: "INTERNAL_ERROR", message: `HTTP ${res.status} ${res.statusText}` } };
      }
      throw new ApiClientError(res.status, errData.error || { code: "UNKNOWN_ERROR", message: "未知错误" });
    }

    return res.json() as Promise<T>;
  } catch (err: any) {
    if (err instanceof ApiClientError) {
      throw err;
    }
    // Network or connection error
    throw new ApiClientError(500, {
      code: "NETWORK_ERROR",
      message: `网络连接失败: 无法访问后端服务 (${err.message || "Connection refused"})`,
      details: err
    });
  }
}

async function uploadFile(path: string, file: File): Promise<LoadArtifactUpload> {
  const url = `${API_BASE}/api/v1${path}?fileName=${encodeURIComponent(file.name)}`;
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/octet-stream" },
    body: file
  });
  if (!res.ok) {
    const payload = await res.json().catch(() => ({
      error: { code: "INTERNAL_ERROR", message: `HTTP ${res.status} ${res.statusText}` }
    }));
    throw new ApiClientError(res.status, payload.error || { code: "UNKNOWN_ERROR", message: "上传失败" });
  }
  return res.json() as Promise<LoadArtifactUpload>;
}

// Simulated network latency helper
function delay<T>(val: T, ms = 300): Promise<T> {
  return new Promise(resolve => setTimeout(() => resolve(val), ms));
}

function readPersistedEngineeringConfig(): EngineeringProjectConfig | null {
  try {
    const raw = localStorage.getItem("momo_engineering_config_v1");
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    return parsed?.state || null;
  } catch {
    return null;
  }
}

function hasItems(value: any): value is any[] {
  return Array.isArray(value) && value.length > 0;
}

export const api = {
  async uploadLoadArtifact(file: File): Promise<LoadArtifactUpload> {
    if (!IS_MOCK_MODE) {
      return uploadFile("/artifacts/uploads", file);
    }
    const firstLine = (await file.text()).split(/\r?\n/, 1)[0] || "time,value";
    const delimiter = firstLine.includes(",") ? "," : firstLine.includes("\t") ? "\t" : " ";
    const columns = firstLine.split(delimiter).map(name => name.trim()).filter(Boolean);
    return {
      artifactId: `mock_load_${Date.now()}`,
      sha256: "SIMULATED_DATA",
      fileName: file.name,
      inspection: {
        format: file.name.split(".").pop()?.toUpperCase() || "UNKNOWN",
        rowCount: 0,
        columns: columns.map(name => ({ name, numericCount: 0, timeCandidate: /^(t|time|time_s)$/i.test(name) }))
      }
    };
  },

  // System Health
  async getHealth(): Promise<{ status: string; service: string; version: string; now: string }> {
    if (API_MODE === "mock") {
      return delay({
        status: "OK",
        service: "bridge-platform-api-mock",
        version: APP_VERSION,
        now: new Date().toISOString()
      });
    }
    return request("GET", "/health");
  },

  async getCapabilities(): Promise<CapabilityCatalog> {
    if (API_MODE === "mock") {
      return delay(MOCK_CAPABILITIES);
    }
    return request("GET", "/capabilities");
  },

  async assertLiveCapability(type: JobType, params: Record<string, any>): Promise<void> {
    if (IS_MOCK_MODE || !LIVE_GATED_JOB_TYPES.has(type)) return;
    const catalog = await this.getCapabilities();
    const controlledMode = ["REAL_AGENT_ANALYSIS", "REAL_DAMPER_COMPARISON", "REAL_DAMPER_PARAMETER_SWEEP", "REAL_BASELINE_OPTIMIZATION"].includes(params.runMode);
    const mode = controlledMode ? "CONTROLLED_AGENT" : "PLATFORM_API";
    const capability = catalog.data.find(item => item.jobType === type && item.mode === mode);
    if (!capability || capability.status !== "LIVE") {
      throw new ApiClientError(501, {
        code: "CAPABILITY_NOT_IMPLEMENTED",
        message: capability?.reason || `${type} 当前没有可用的 Live 执行能力`,
        details: { jobType: type, mode, unlockRequirements: capability?.unlockRequirements || [] }
      });
    }
  },

  // Dashboard
  async getDashboardSummary(): Promise<DashboardSummary> {
    if (API_MODE === "mock") {
      return delay(mockDb.getDashboardSummary());
    }
    return request("GET", "/dashboard/summary");
  },

  // Templates
  async getTemplates(params?: {
    solver?: string;
    workflow?: string;
    includeLegacy?: boolean;
  }): Promise<PaginatedResponse<Template>> {
    if (API_MODE === "mock") {
      const data = mockDb.getTemplates(params?.solver, params?.workflow, params?.includeLegacy);
      return delay({
        data,
        pagination: { page: 1, pageSize: 50, totalItems: data.length, totalPages: 1 }
      });
    }
    const query = new URLSearchParams();
    if (params?.solver) query.append("solver", params.solver);
    if (params?.workflow) query.append("workflow", params.workflow);
    if (params?.includeLegacy !== undefined) query.append("includeLegacy", String(params.includeLegacy));
    return request("GET", `/templates?${query.toString()}`);
  },

  async getTemplate(templateId: string): Promise<Template> {
    if (API_MODE === "mock") {
      const t = mockDb.getTemplate(templateId);
      if (!t) throw new ApiClientError(404, { code: "NOT_FOUND", message: `模板 ${templateId} 不存在` });
      return delay(t);
    }
    return request("GET", `/templates/${templateId}`);
  },

  // Preflight
  async runPreflight(configPath: string): Promise<PreflightResponse> {
    if (API_MODE === "mock") {
      return delay(mockDb.runPreflight(configPath), 800);
    }
    return request("POST", "/preflights", { configPath });
  },

  // Jobs
  async getJobs(params?: {
    status?: JobStatus;
    type?: JobType;
    page?: number;
    pageSize?: number;
  }): Promise<PaginatedResponse<Job>> {
    if (API_MODE === "mock") {
      return delay(mockDb.getJobs(params?.status, params?.type, params?.page, params?.pageSize));
    }
    const query = new URLSearchParams();
    if (params?.status) query.append("status", params.status);
    if (params?.type) query.append("type", params.type);
    if (params?.page) query.append("page", String(params.page));
    if (params?.pageSize) query.append("pageSize", String(params.pageSize));
    return request("GET", `/jobs?${query.toString()}`);
  },

  async getJob(jobId: string): Promise<Job> {
    if (API_MODE === "mock") {
      const j = mockDb.getJob(jobId);
      if (!j) throw new ApiClientError(404, { code: "NOT_FOUND", message: `任务 ${jobId} 不存在` });
      return delay(j);
    }
    return request("GET", `/jobs/${jobId}`);
  },

  async createJob(type: JobType, params: Record<string, any>): Promise<Job> {
    if (API_MODE === "mock") {
      return delay(mockDb.createJob(type, params), 400);
    }
    if (type === "LOAD_TRAFFIC_RANDOM") {
      return request("POST", "/load-cases/traffic/random", params);
    }
    if (type === "LOAD_WIND_VERTICAL") {
      return request("POST", "/load-cases/wind/vertical", params);
    }
    if (type === "LOAD_EARTHQUAKE") {
      return request("POST", "/load-cases/earthquake", params);
    }
    if (type === "LOAD_CURVE_EXPORT" && params.sourceArtifactId && params.loadKind && hasItems(params.formats)) {
      return request("POST", "/load-curves/export", params);
    }
    if (type === "COMMAND_STREAM_ASSEMBLY" && params.moduleConfig) {
      return request("POST", "/command-streams/assemble", params);
    }
    if (type === "SOLVER_BATCH" && params.caseSetId && params.resources) {
      return request("POST", "/solver-runs", params);
    }
    if (type === "RESULT_EXTRACTION" && params.solverRunId) {
      return request("POST", "/result-extractions", params);
    }
    if (type === "EXPERIMENT_DESIGN" && hasItems(params.variables) && hasItems(params.responseTargets)) {
      return request("POST", "/experiment-designs", params);
    }
    if (type === "SURROGATE_TRAINING" && hasItems(params.modelFamilies) && hasItems(params.targetMetricIds)) {
      return request("POST", "/surrogates/train", params);
    }
    if (type === "ACTIVE_LEARNING" && params.activeLearningEnabled !== undefined) {
      return request("POST", "/active-learning/infill", params);
    }
    if (type === "MULTI_OBJECTIVE_OPTIMIZATION" && hasItems(params.objectives)) {
      return request("POST", "/optimizations/multi-objective", params);
    }
    if (type === "ENTROPY_TOPSIS_DECISION" && params.optimizationRunId) {
      return request("POST", "/decisions/entropy-topsis", params);
    }
    if (type === "OPTIMIZATION_EXPORT" && params.optimizationRunId && hasItems(params.exportKinds) && hasItems(params.formats)) {
      return request("POST", "/optimizations/export", params);
    }
    return request("POST", "/jobs", { type, params });
  },

  async cancelJob(jobId: string): Promise<{ success: boolean }> {
    if (API_MODE === "mock") {
      const ok = mockDb.cancelJob(jobId);
      if (!ok) throw new ApiClientError(409, { code: "JOB_NOT_CANCELLABLE", message: "当前任务状态不允许取消或已结束" });
      return delay({ success: true });
    }
    return request("POST", `/jobs/${jobId}/cancel`);
  },

  // Artifacts
  async getArtifacts(params?: {
    jobId?: string;
    kind?: string;
    source?: "PLATFORM_JOB" | "REAL_OPTIMIZATION_HISTORY";
    page?: number;
    pageSize?: number;
  }): Promise<PaginatedResponse<Artifact>> {
    if (API_MODE === "mock") {
      const result = mockDb.getArtifacts(params?.jobId, params?.kind, params?.page, params?.pageSize);
      if (!params?.source) return delay(result);
      const data = result.data.filter(artifact => artifact.source === params.source);
      return delay({ data, pagination: { ...result.pagination, totalItems: data.length, totalPages: data.length ? 1 : 0 } });
    }
    const query = new URLSearchParams();
    if (params?.jobId) query.append("jobId", params.jobId);
    if (params?.kind) query.append("kind", params.kind);
    if (params?.source) query.append("source", params.source);
    if (params?.page) query.append("page", String(params.page));
    if (params?.pageSize) query.append("pageSize", String(params.pageSize));
    return request("GET", `/artifacts?${query.toString()}`);
  },

  async syncRealOptimizationHistory(): Promise<RealOptimizationSyncResult> {
    if (API_MODE === "mock") {
      return delay({
        scannedRuns: 0,
        importedRuns: 0,
        importedArtifacts: 0,
        updatedArtifacts: 0,
        existingArtifacts: 0,
        skippedRuns: 0,
        runs: []
      });
    }
    return request("POST", "/artifacts/sync-real-optimizations");
  },

  async getArtifact(artifactId: string): Promise<Artifact> {
    if (API_MODE === "mock") {
      const a = mockDb.getArtifact(artifactId);
      if (!a) throw new ApiClientError(404, { code: "NOT_FOUND", message: `制品 ${artifactId} 不存在` });
      return delay(a);
    }
    return request("GET", `/artifacts/${artifactId}`);
  },

  async getArtifactPreview(artifactId: string): Promise<any> {
    if (API_MODE === "mock") {
      const p = mockDb.getArtifactPreview(artifactId);
      if (p === null) throw new ApiClientError(400, { code: "PREVIEW_UNAVAILABLE", message: "该类型文件不支持预览" });
      return delay(p, 500);
    }
    return request("GET", `/artifacts/${artifactId}/preview`);
  },

  // Reports
  async getLatestParityReport(): Promise<ParityReport> {
    if (API_MODE === "mock") {
      return delay(mockDb.getLatestParityReport());
    }
    return request("GET", "/reports/parity/latest");
  },

  async getStatusReports(): Promise<PaginatedResponse<Artifact>> {
    // Standard path documents status report under status_reports
    return this.getArtifacts({ kind: "STATUS_REPORT" });
  },

  // --- Engineering Specific Actions ---
  
  // Traffic Load
  async generateTrafficLoad(params: {
    bridgeId: string;
    scenarioName: string;
    sourceMode: TrafficLoadSourceMode;
    durationS: number;
    timeStepS: number;
    seed: number;
    trafficModel: string;
    vehicleLibraryId?: string;
    existingVehicleDataArtifactId?: string;
    hourlyFlowProfile?: Array<{ hour: number; vehiclesPerHour: number }>;
    trafficScale: number;
    heavyVehicleScale: number;
    laneMode: "MAIN_GIRDER_BIDIRECTIONAL";
    appliedStructure: "MAIN_GIRDER";
    exportFormats: string[];
  }): Promise<Job> {
    if (API_MODE === "mock") {
      return this.createJob("LOAD_TRAFFIC_RANDOM", params);
    }
    return request("POST", "/load-cases/traffic/random", params);
  },

  // Wind Load
  async runWindWorkflow(requestParams: any): Promise<Job> {
    if (API_MODE === "mock") {
      return delay(mockDb.runWindWorkflow(requestParams), 500);
    }
    return request("POST", "/wind/workflows", requestParams);
  },

  async exportWind(format: string, requestParams: any): Promise<Artifact> {
    if (API_MODE === "mock") {
      return delay(mockDb.exportWind(format, requestParams), 600);
    }
    return request("POST", "/wind/exports", { format, request: requestParams });
  },

  async generateWindLoad(params: {
    bridgeId: string;
    scenarioName: string;
    appliedComponent: "VERTICAL";
    windRequest: any;
    timeHistory: {
      startTime: string;
      durationS: number;
      timeStepS: number;
      seed: number;
    };
  }): Promise<Job> {
    if (API_MODE === "mock") {
      return this.createJob("LOAD_WIND_VERTICAL", params);
    }
    return request("POST", "/load-cases/wind/vertical", params);
  },

  // Earthquake Load
  async generateEarthquakeLoad(params: {
    bridgeId: string;
    scenarioName: string;
    source: EarthquakeSourceMode;
    direction: string;
    timeStepS: number;
    durationS: number;
    codeSpectrum?: {
      code: string;
      siteClass: string;
      dampingRatio: number;
      peakGroundAcceleration: number;
    };
    peerRecord?: {
      recordId: string;
      eventName: string;
      stationName: string;
    };
    scaling: {
      method: "PGA" | "SA_T1" | "SPECTRUM_MATCH";
      targetPeriodS: number;
      periodRangeS: [number, number];
      scaleFactor: number;
    };
  }): Promise<Job> {
    if (API_MODE === "mock") {
      return this.createJob("LOAD_EARTHQUAKE", params);
    }
    return request("POST", "/load-cases/earthquake", params);
  },

  async exportLoadCurve(params: LoadCurveExportRequest): Promise<Job> {
    if (API_MODE === "mock") {
      return this.createJob("LOAD_CURVE_EXPORT", params);
    }
    return request("POST", "/load-curves/export", params);
  },

  // Engineering configuration state
  async getEngineeringConfig(): Promise<EngineeringProjectConfig | null> {
    if (API_MODE === "mock") {
      return delay(readPersistedEngineeringConfig());
    }
    return request("GET", "/engineering-config");
  },

  async saveEngineeringConfig(config: EngineeringProjectConfig): Promise<EngineeringProjectConfig> {
    if (API_MODE === "mock") {
      localStorage.setItem("momo_engineering_config_v1", JSON.stringify({ state: config, version: 0 }));
      return delay(config);
    }
    return request("PUT", "/engineering-config", config);
  },

  async validateEngineeringConfig(config: EngineeringProjectConfig): Promise<{ moduleStatus: ModuleStatus[] }> {
    if (API_MODE === "mock") {
      return delay({ moduleStatus: [] });
    }
    return request("POST", "/engineering-config/validate", config);
  },

  async getDamperInstanceRegistry(): Promise<DamperInstanceRegistryEntry[]> {
    if (API_MODE === "mock") {
      return delay(readPersistedEngineeringConfig()?.damperBaseConfig.damperInstanceRegistry || []);
    }
    return request("GET", "/engineering-config/dampers/registry");
  },

  async getDefaultResultExtractionMetrics(): Promise<ResultExtractionMetricConfig[]> {
    if (API_MODE === "mock") {
      return delay(readPersistedEngineeringConfig()?.resultExtractionConfig.structuralMetrics || []);
    }
    return request("GET", "/engineering-config/result-extraction/default-metrics");
  },

  // Command Stream Assemble
  async assembleCommandStream(params: {
    solver: string;
    bridgeId: string;
    caseSetId: string;
    moduleConfig: CommandStreamModuleConfig;
  }): Promise<Job> {
    if (API_MODE === "mock") {
      return this.createJob("COMMAND_STREAM_ASSEMBLY", params);
    }
    return request("POST", "/command-streams/assemble", params);
  },

  // Solver Runs
  async runSolverBatch(params: {
    solver: string;
    caseSetId: string;
    resources: SolverResourceConfig;
  }): Promise<Job> {
    if (API_MODE === "mock") {
      return this.createJob("SOLVER_BATCH", params);
    }
    return request("POST", "/solver-runs", params);
  },

  // Experiment Design
  async runExperimentDesign(params: ExperimentDesignRequest): Promise<Job> {
    if (API_MODE === "mock") {
      return this.createJob("EXPERIMENT_DESIGN", params);
    }
    return request("POST", "/experiment-designs", params);
  },

  // Result Extraction
  async runResultExtraction(params: {
    solverRunId: string;
    extractors: string[];
  }): Promise<Job> {
    if (API_MODE === "mock") {
      return this.createJob("RESULT_EXTRACTION", params);
    }
    return request("POST", "/result-extractions", params);
  },

  // Surrogate Training
  async trainSurrogate(params: {
    datasetId: string;
    modelFamilies: SurrogateModelFamily[];
    targetMetricIds: string[];
    targets?: string[];
    validation: { method: string; folds: number };
  } | SurrogateTrainingRequest): Promise<Job> {
    if (API_MODE === "mock") {
      return this.createJob("SURROGATE_TRAINING", params);
    }
    return request("POST", "/surrogates/train", params);
  },

  // Active Learning
  async runActiveLearningInfill(params: {
    activeLearningEnabled: boolean;
    surrogateRunId: string;
    strategy: string;
    batchSize: number;
    requiresRealFemReview: boolean;
  }): Promise<Job> {
    if (API_MODE === "mock") {
      return this.createJob("ACTIVE_LEARNING", params);
    }
    return request("POST", "/active-learning/infill", params);
  },

  // Optimization
  async runOptimization(params: {
    surrogateRunId: string;
    objectiveMode?: OptimizationObjectiveMode;
    objectives: OptimizationObjective[];
    constraints: OptimizationConstraint[];
  }): Promise<Job> {
    if (API_MODE === "mock") {
      return this.createJob("MULTI_OBJECTIVE_OPTIMIZATION", params);
    }
    return request("POST", "/optimizations/multi-objective", params);
  },

  // TOPSIS Decision
  async runTopsisDecision(params: {
    optimizationRunId: string;
    candidateFilter: { requiresAcceptedFemReview: boolean };
  }): Promise<Job> {
    if (API_MODE === "mock") {
      return this.createJob("ENTROPY_TOPSIS_DECISION", params);
    }
    return request("POST", "/decisions/entropy-topsis", params);
  },

  async exportOptimizationResult(params: OptimizationExportRequest): Promise<Job> {
    if (API_MODE === "mock") {
      return this.createJob("OPTIMIZATION_EXPORT", params);
    }
    return request("POST", "/optimizations/export", params);
  },

  async getTopsisResult(optimizationRunId: string): Promise<any> {
    if (API_MODE === "mock") {
      return delay(mockDb.getParetoAndTopsis(optimizationRunId));
    }
    return request("GET", `/optimizations/${optimizationRunId}/topsis`);
  }
};

export { ApiClientError };
