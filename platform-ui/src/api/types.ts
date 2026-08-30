export type SolverBackend =
  | "ANSYS"
  | "OPENSEES"
  | "OPENSEESPY_INPROC";

export type LoadKind =
  | "RANDOM_TRAFFIC"
  | "VERTICAL_WIND"
  | "EARTHQUAKE";

export type ExperimentDesignScenarioType =
  | "EARTHQUAKE"
  | "WIND"
  | "TRAFFIC"
  | "OPERATION";

export type ExperimentDesignExecutionGoal =
  | "BATCH_CALCULATION"
  | "OPTIMIZATION_RECOMMENDATION";

export type AnalysisScenarioType =
  | "EARTHQUAKE"
  | "WIND"
  | "TRAFFIC"
  | "WIND_TRAFFIC";

export type TaskExecutionTarget =
  | "DESIGN_SAMPLES"
  | "COMMAND_STREAM"
  | "BATCH_SOLVE"
  | "RESULT_EXTRACTION"
  | "SURROGATE_TRAINING"
  | "OPTIMIZATION_DECISION";

export type EngineeringModuleId =
  | "DOE"
  | "DAMPER_BASE"
  | "LOADS"
  | "SOLVER_BATCH"
  | "RESULT_EXTRACTION"
  | "SURROGATE_LEARNING"
  | "OPTIMIZATION_DECISION";

export type ModuleStatusKind =
  | "UNCONFIGURED"
  | "INCOMPLETE"
  | "CONFIGURED"
  | "VALIDATED";

export interface ModuleStatus {
  moduleId: EngineeringModuleId;
  label: string;
  status: ModuleStatusKind;
  required: boolean;
  message: string;
  route: string;
}

export interface ModelFileConfig {
  fileName: string;
  filePath?: string;
  format: "APDL" | "OPENSEES_TCL" | "OPENSEESPY" | "UNKNOWN";
  parseStatus: "NOT_UPLOADED" | "UPLOADED" | "PARSED" | "FAILED";
  parsedAt?: string;
  summary?: {
    nodeCount: number;
    elementCount: number;
    detectedDamperCount: number;
  };
}

export interface GlobalTaskConfig {
  solver: SolverBackend;
  scenario: AnalysisScenarioType;
  executionTarget: TaskExecutionTarget;
}

export type TrafficLoadSourceMode =
  | "GENERATE_RANDOM"
  | "LOAD_EXISTING";

export type EarthquakeSourceMode =
  | "CODE_SPECTRUM"
  | "PEER_RECORD"
  | "LOCAL_FILE"
  | "TEMPLATE";

export type ResponseTargetId =
  | "beamEndDisplacement"
  | "towerBaseShear"
  | "towerBaseMoment"
  | "beamEndCumulativeDisplacement"
  | "damperCost"
  | "damperStroke"
  | "damperForce"
  | "midspanDisplacement"
  | "midspanAcceleration";

export interface ResponseTarget {
  id: ResponseTargetId;
  label: string;
  unit: string;
  category: "SEISMIC" | "OPERATION" | "DAMPER" | "DIAGNOSTIC";
  defaultFor: Array<"SEISMIC" | "OPERATION">;
}

export type SurrogateModelFamily =
  | "GPR"
  | "KRIGING"
  | "RBF"
  | "RESPONSE_SURFACE"
  | "SVR"
  | "PCE"
  | "MARS";

export type SurrogateDatasetSourceMode =
  | "DOE_DATASET"
  | "USER_IMPORTED_ARTIFACT";

export interface ImportedSurrogateDatasetSchema {
  featureColumns: string[];
  targetColumns: Record<string, string>;
}

export interface SurrogateTrainingRequest {
  datasetSourceMode: SurrogateDatasetSourceMode;
  datasetId?: string;
  importedDatasetArtifactId?: string;
  importedDatasetSchema?: ImportedSurrogateDatasetSchema;
  modelFamilies: SurrogateModelFamily[];
  targetMetricIds: string[];
  targets?: string[];
  validation: { method: string; folds: number };
}

export type DamperElementType = "USER300";

export type DamperMaterialType =
  | "VISCOUS"
  | "EDDY_CURRENT"
  | "FRICTION";

export type User300MaterialType = DamperMaterialType;

export interface ViscousDamperParameters {
  materialType: "VISCOUS";
  dampingCoefficient: number;
  velocityExponent: number;
  unitCost: number;
}

export interface EddyCurrentDamperParameters {
  materialType: "EDDY_CURRENT";
  maxOutputForce: number;
  criticalVelocity: number;
  unitCost: number;
}

export interface FrictionDamperParameters {
  materialType: "FRICTION";
  maxOutputForce: number;
  unitCost: number;
}

export type DamperMaterialParameters =
  | ViscousDamperParameters
  | EddyCurrentDamperParameters
  | FrictionDamperParameters;

export type BridgeTowerId = "SOUTH" | "NORTH";

export interface DamperConnectionNodePair {
  id: string;
  tower: BridgeTowerId;
  nodeI: number;
  nodeJ: number;
  label: string;
}

export interface DamperPlacementConfig {
  layoutId: string;
  southTowerCount: number;
  northTowerCount: number;
  connectionNodePairIds: string[];
  connectionNodePairs?: DamperConnectionNodePair[];
}

export type DamperDirection = "UX" | "UY" | "UZ" | "LOCAL_AXIAL";

export interface DamperInstanceRegistryEntry {
  damperId: string;
  materialType: DamperMaterialType;
  placementLabel: string;
  elementType: DamperElementType;
  elementId: number;
  nodeI: number;
  nodeJ: number;
  direction: DamperDirection;
  source: "AUTO_GENERATED" | "MODEL_DETECTED" | "USER_DEFINED";
}

export interface DamperBaseConfig {
  enabled: boolean;
  materialType: DamperMaterialType;
  southTowerCount: number;
  northTowerCount: number;
  direction: DamperDirection;
  connectionNodePairs: DamperConnectionNodePair[];
  placementValidated: boolean;
  damperInstanceRegistry: DamperInstanceRegistryEntry[];
}

export interface DamperConfig {
  enabled: boolean;
  elementType: DamperElementType;
  material: DamperMaterialParameters;
  placement: DamperPlacementConfig;
}

export interface VehicleLibraryHourFlow {
  hour: number;
  vehiclesPerHour: number;
  heavyVehicleRatio: number;
}

export interface VehicleLibraryOption {
  id: string;
  label: string;
  description: string;
  totalVehicles24h: number;
  heavyVehicleRatio: number;
  hourlyFlow: VehicleLibraryHourFlow[];
}

export interface SolverResourceConfig {
  processCount: number;
  coresPerProcess: number;
  executionTimeoutS: number;
}

export interface CommandStreamModuleConfig {
  modules: string[];
  damper: DamperConfig;
  responseTargets: ResponseTargetId[];
}

export type ExperimentDesignVariableId =
  | "dampingCoefficient"
  | "velocityExponent"
  | "maxOutputForce"
  | "criticalVelocity";

export interface ExperimentDesignVariable {
  id: ExperimentDesignVariableId;
  label: string;
  unit: string;
  min: number;
  max: number;
  enabled: boolean;
}

export type DoeMethod =
  | "LHS"
  | "FULL_FACTORIAL"
  | "CENTRAL_COMPOSITE"
  | "SOBOL"
  | "RANDOM";

export interface DoeConfig {
  method: DoeMethod;
  variables: ExperimentDesignVariable[];
  sampleCount: number;
  seed: number;
  includeCorners: boolean;
  includeCenter: boolean;
  validated: boolean;
}

export interface ExperimentDesignRequest {
  bridgeId: string;
  designName: string;
  solver: SolverBackend;
  caseSetPrefix: string;
  scenarioType: ExperimentDesignScenarioType;
  executionGoal: ExperimentDesignExecutionGoal;
  vehicleLibraryId?: string;
  damper: DamperConfig;
  variables: ExperimentDesignVariable[];
  sampling: {
    lhsSamples: number;
    includeCorners: boolean;
    includeCenter: boolean;
    seed: number;
  };
  responseTargets: ResponseTargetId[];
  resources: SolverResourceConfig;
}

export type LoadCurveExportFormat = "PNG" | "TIFF" | "SVG" | "CUSTOM";

export type LoadCurveExportKind = "TRAFFIC" | "WIND" | "EARTHQUAKE";

export interface LoadCurveExportRequest {
  sourceArtifactId: string;
  loadKind: LoadCurveExportKind;
  curveComponent: string;
  formats: LoadCurveExportFormat[];
  customFormat?: string;
  stylePreset: "PROJECT_NATURE";
}

export type OptimizationObjectiveMode = "SEISMIC" | "OPERATION" | "OVERALL" | "CUSTOM";

export type OptimizationExportKind =
  | "PARETO_FRONT"
  | "ENTROPY_WEIGHTS"
  | "TOPSIS_RECOMMENDATION";

export type OptimizationExportFormat = "CSV" | "JSON" | "PNG" | "SVG";

export interface OptimizationExportRequest {
  optimizationRunId: string;
  exportKinds: OptimizationExportKind[];
  formats: OptimizationExportFormat[];
}

export interface OptimizationObjective {
  name: ResponseTargetId;
  direction: "MIN" | "MAX";
}

export type OptimizationConstraintSource = "UNCONTROLLED" | "CUSTOM";

export interface OptimizationConstraint {
  targetId: ResponseTargetId;
  name: string;
  operator: "<=" | ">=";
  value: number;
  unit: string;
  source: OptimizationConstraintSource;
}

export type EngineeringLoadSourceMode = "INPUT_FILE" | "GENERATED";

export interface LoadArtifactUpload {
  artifactId: string;
  sha256: string;
  fileName: string;
  inspection: {
    format: string;
    rowCount: number;
    columns: Array<{ name: string; numericCount: number; timeCandidate: boolean }>;
  };
}

export interface LoadFileInputConfig {
  fileName: string;
  artifactId: string;
  sourceSha256: string;
  availableColumns: string[];
  timeColumn: string;
  valueColumn: string;
  consistentExcitation: boolean;
  fileType: "CSV" | "TXT" | "EXCEL" | "PROJECT_FORMAT";
  timeStepS: number;
  durationS: number;
  unit: string;
  direction: "UX" | "UY" | "UZ" | "X" | "Y" | "Z";
  nodeMapping: string;
}

export interface EarthquakeLoadConfig {
  sourceMode: EngineeringLoadSourceMode;
  configured: boolean;
  validated: boolean;
  fileInput: LoadFileInputConfig;
  codeGeneration: {
    startTime: string;
    durationS: number;
    timeStepS: number;
    code: string;
    intensity: string;
    siteClass: string;
    designGroup: string;
    dampingRatio: number;
    pgaScaleFactor: number;
    spectrumMatch: boolean;
    directions: Array<"LONGITUDINAL" | "TRANSVERSE" | "VERTICAL">;
  };
}

export interface WindLoadConfig {
  sourceMode: EngineeringLoadSourceMode;
  configured: boolean;
  validated: boolean;
  fileInput: LoadFileInputConfig;
  generated: {
    meanWindSpeed: number;
    attackAngleDeg: number;
    turbulenceIntensity: number;
    spectrumModel: "Davenport" | "Kaimal";
    spatialCorrelation: number;
    components: string[];
    targets: string[];
    startTime: string;
    durationS: number;
    timeStepS: number;
  };
}

export interface TrafficLoadConfig {
  sourceMode: EngineeringLoadSourceMode;
  configured: boolean;
  validated: boolean;
  fileInput: LoadFileInputConfig;
  generated: {
    laneCount: number;
    vehicleLibraryId: string;
    vehicleTypes: string[];
    speedDistribution: string;
    headwayDistribution: string;
    trafficScale: number;
    heavyVehicleScale: number;
    startTime: string;
    durationS: number;
    timeStepS: number;
    seed: number;
    laneMapping: string;
  };
}

export interface EngineeringLoadConfig {
  earthquake: EarthquakeLoadConfig;
  wind: WindLoadConfig;
  traffic: TrafficLoadConfig;
}

export type SolverAnalysisType = "STATIC" | "MODAL" | "TRANSIENT_DYNAMIC";

export type SolverBatchMode = "SERIAL" | "PARALLEL";

export type SolverCompletionAction =
  | "SAVE_ONLY"
  | "AUTO_POSTPROCESS"
  | "AUTO_TRAIN_SURROGATE";

export interface SolverBatchConfig {
  solver: SolverBackend;
  analysisType: SolverAnalysisType;
  batchMode: SolverBatchMode;
  processCount: number;
  coresPerProcess: number;
  caseTimeoutS: number;
  retryCount: number;
  outputDirectory: string;
  keepIntermediateFiles: boolean;
  autoCleanTemporaryFiles: boolean;
  completionAction: SolverCompletionAction;
  validated: boolean;
}

export type ResultObjectType =
  | "NODE"
  | "ELEMENT"
  | "SUPPORT"
  | "DAMPER"
  | "SECTION";

export type ResultComponent =
  | "UX" | "UY" | "UZ"
  | "VX" | "VY" | "VZ"
  | "AX" | "AY" | "AZ"
  | "FX" | "FY" | "FZ"
  | "MX" | "MY" | "MZ"
  | "LOCAL_AXIAL";

export type ResultResponseType =
  | "DISPLACEMENT"
  | "VELOCITY"
  | "ACCELERATION"
  | "REACTION"
  | "ELEMENT_FORCE"
  | "DAMPER_FORCE"
  | "DAMPER_STROKE"
  | "ENERGY";

export type ResultStatisticType =
  | "PEAK"
  | "ABS_PEAK"
  | "RMS"
  | "MEAN"
  | "CUMULATIVE"
  | "ENVELOPE"
  | "TIME_HISTORY";

export interface ResultExtractionMetricConfig {
  id: string;
  name: string;
  objectType: ResultObjectType;
  objectId: string;
  component: ResultComponent;
  responseType: ResultResponseType;
  statistic: ResultStatisticType;
  enabled: boolean;
  autoGenerated: boolean;
  sourceDamperId?: string;
  sourceDescription: string;
}

export interface ResultExtractionConfig {
  structuralMetrics: ResultExtractionMetricConfig[];
  autoDamperMetrics: ResultExtractionMetricConfig[];
  exportRawData: boolean;
  validated: boolean;
}

export interface SurrogateLearningConfig {
  datasetSourceMode: SurrogateDatasetSourceMode;
  datasetId: string;
  importedDatasetArtifactId?: string;
  modelFamilies: SurrogateModelFamily[];
  inputVariableIds: ExperimentDesignVariableId[];
  outputResponseIds: ResponseTargetId[];
  trainRatio: number;
  validationMethod: "HOLDOUT" | "K_FOLD";
  folds: number;
  metrics: Array<"R2" | "RMSE" | "MAE">;
  activeLearningEnabled: boolean;
  activeLearningStrategy: "MAX_UNCERTAINTY" | "MAX_ERROR" | "EI" | "TARGET_REGION";
  maxAdditionalSamples: number;
  convergenceTolerance: number;
  validated: boolean;
}

export interface OptimizationDecisionConfig {
  algorithm: "NSGA2" | "MOPSO" | "SPEA2";
  objectiveMode: OptimizationObjectiveMode;
  designVariableIds: ExperimentDesignVariableId[];
  objectives: OptimizationObjective[];
  constraints: OptimizationConstraint[];
  economics: {
    damperUnitCost: number;
    totalCostLimit: number;
    efficiencyMetric: string;
  };
  decisionMethods: Array<"TOPSIS" | "ENTROPY_WEIGHT" | "MANUAL_WEIGHT">;
  validated: boolean;
}

export interface EngineeringProjectConfig {
  projectConfig: {
    projectName: string;
    modelFile: ModelFileConfig;
  };
  globalTaskConfig: GlobalTaskConfig;
  damperBaseConfig: DamperBaseConfig;
  loadConfig: EngineeringLoadConfig;
  doeConfig: DoeConfig;
  solverBatchConfig: SolverBatchConfig;
  resultExtractionConfig: ResultExtractionConfig;
  surrogateLearningConfig: SurrogateLearningConfig;
  optimizationDecisionConfig: OptimizationDecisionConfig;
}

export type WorkflowStage =
  | "LOAD_GENERATION"
  | "COMMAND_STREAM_ASSEMBLY"
  | "SOLVER_BATCH"
  | "RESULT_EXTRACTION"
  | "SURROGATE_TRAINING"
  | "ACTIVE_LEARNING"
  | "MULTI_OBJECTIVE_OPTIMIZATION"
  | "ENTROPY_TOPSIS_DECISION"
  | "FINAL_REVIEW";

export type CapabilityState = "LIVE" | "MOCK_ONLY" | "DISABLED";
export type CapabilityMode = "AGENT" | "PLATFORM_API" | "CONTROLLED_AGENT" | "MOCK";

export interface CapabilityDescriptor {
  jobType: string;
  mode: CapabilityMode;
  status: CapabilityState;
  handler: string;
  solvers: string[];
  scenarios: string[];
  solverScenarios: Record<string, string[]>;
  inputArtifacts: string[];
  outputArtifacts: string[];
  supportsCancel: boolean;
  supportsResume: boolean;
  reason: string;
  unlockRequirements: string[];
}

export interface CapabilityCatalog {
  version: string;
  data: CapabilityDescriptor[];
}

export type JobType =
  | "PROJECT_GATE_FAST"
  | "USER300_VERIFY"
  | "PREFLIGHT_CONFIG"
  | "LOAD_TRAFFIC_RANDOM"
  | "LOAD_WIND_VERTICAL"
  | "LOAD_EARTHQUAKE"
  | "LOAD_CURVE_EXPORT"
  | "COMMAND_STREAM_ASSEMBLY"
  | "SOLVER_BATCH"
  | "RESULT_EXTRACTION"
  | "SOLVER_PARITY"
  | "BASELINE_DOE"
  | "EXPERIMENT_DESIGN"
  | "SURROGATE_TRAINING"
  | "ACTIVE_LEARNING"
  | "MULTI_OBJECTIVE_OPTIMIZATION"
  | "ENTROPY_TOPSIS_DECISION"
  | "OPTIMIZATION_EXPORT"
  | "WIND_WORKFLOW";

export type JobStatus =
  | "QUEUED"
  | "RUNNING"
  | "SUCCEEDED"
  | "FAILED"
  | "CANCELLED";

export interface Job {
  jobId: string;
  type: JobType;
  status: JobStatus;
  title: string;
  createdAt: string;
  startedAt?: string;
  finishedAt?: string;
  progress?: {
    phase: string;
    message: string;
    percent?: number;
  };
  request: Record<string, any>;
  command?: {
    executable: string;
    args: string[];
    cwd: string;
  };
  result?: Record<string, any>;
  artifacts: Artifact[];
  error?: {
    code: string;
    message: string;
    details?: any;
  };
}

export type ArtifactKind =
  | "LOAD_CASE"
  | "JSON_SUMMARY"
  | "CSV_TIMESERIES"
  | "CSV_TABLE"
  | "RAW_DATA"
  | "PARITY_REPORT"
  | "SURROGATE_MODEL"
  | "OPTIMIZATION_REPORT"
  | "DECISION_REPORT"
  | "STATUS_REPORT"
  | "LOG"
  | "COMMAND_STREAM"
  | "PLOT"
  | "FEM_MODEL"
  | "BINARY";

export interface Artifact {
  artifactId: string;
  kind: ArtifactKind;
  name: string;
  path: string;
  mimeType: string;
  sizeBytes?: number;
  sha256?: string;
  canPreview: boolean;
  downloadUrl: string;
  source?: "PLATFORM_JOB" | "REAL_OPTIMIZATION_HISTORY";
  runId?: string;
  createdAt?: string;
}

export interface RealOptimizationSyncResult {
  scannedRuns: number;
  importedRuns: number;
  importedArtifacts: number;
  updatedArtifacts: number;
  existingArtifacts: number;
  skippedRuns: number;
  runs: string[];
}

export interface ApiError {
  error: {
    code: string;
    message: string;
    details?: any;
    requestId?: string;
  };
}

export interface DashboardSummary {
  repo: {
    status?: string;
    branch: string | null;
    isClean: boolean | null;
    lastCommit: string | null;
  };
  latestGate: {
    status: string;
    summaryPath: string | null;
    finishedAt: string | null;
  };
  latestParity: {
    status: string;
    reportPath: string | null;
    maxRelativeError: number | null;
  };
  runtime: {
    status?: string;
    ansysAvailable: boolean | null;
    openseespyInprocAvailable: boolean | null;
    user300PatchPackage: string | null;
  };
}

export interface Template {
  templateId: string;
  name: string;
  solver: SolverBackend;
  workflow: string;
  path: string;
  isLegacy: boolean;
}

export interface PreflightResponse {
  status: "PASS" | "FAIL";
  configPath: string;
  solver: string;
  executionMode: string;
  pathChecks: Array<{
    name: string;
    path: string;
    exists: boolean;
  }>;
  raw: Record<string, any>;
}

export interface ParityReport {
  status: "PASS" | "FAIL";
  reportPath: string;
  referenceSolver?: string;
  candidateSolver?: string;
  relativeTolerance?: number;
  absoluteTolerance?: number;
  metrics: Array<{
    name: ResponseTargetId | string;
    label?: string;
    relativeError: number;
    accepted: boolean;
  }>;
  artifacts: Artifact[];
}

export interface PaginatedResponse<T> {
  data: T[];
  pagination: {
    page: number;
    pageSize: number;
    totalItems: number;
    totalPages: number;
  };
}
