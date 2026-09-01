import type {
  DamperConfig,
  DamperConnectionNodePair,
  DamperMaterialType,
  DamperMaterialParameters,
  User300MaterialType,
  AnalysisScenarioType,
  TaskExecutionTarget,
  ModuleStatus,
  DoeMethod,
  ResultObjectType,
  ResultResponseType,
  ResultStatisticType,
  ResultExtractionMetricConfig,
  ExperimentDesignVariable,
  ExperimentDesignVariableId,
  ExperimentDesignExecutionGoal,
  ExperimentDesignScenarioType,
  LoadCurveExportFormat,
  OptimizationExportFormat,
  OptimizationExportKind,
  OptimizationObjectiveMode,
  ResponseTarget,
  ResponseTargetId,
  SurrogateModelFamily,
  VehicleLibraryOption
} from "./types";

export const RESPONSE_TARGETS: ResponseTarget[] = [
  {
    id: "beamEndDisplacement",
    label: "梁端位移",
    unit: "m",
    category: "SEISMIC",
    defaultFor: ["SEISMIC"]
  },
  {
    id: "towerBaseShear",
    label: "塔底剪力",
    unit: "kN",
    category: "SEISMIC",
    defaultFor: ["SEISMIC"]
  },
  {
    id: "towerBaseMoment",
    label: "塔底弯矩",
    unit: "kN*m",
    category: "SEISMIC",
    defaultFor: ["SEISMIC"]
  },
  {
    id: "beamEndCumulativeDisplacement",
    label: "梁端累计位移",
    unit: "m",
    category: "OPERATION",
    defaultFor: ["OPERATION"]
  },
  {
    id: "damperCost",
    label: "阻尼器造价",
    unit: "CNY",
    category: "OPERATION",
    defaultFor: ["OPERATION"]
  },
  {
    id: "damperStroke",
    label: "阻尼器行程",
    unit: "m",
    category: "DAMPER",
    defaultFor: []
  },
  {
    id: "damperForce",
    label: "阻尼器力",
    unit: "kN",
    category: "DAMPER",
    defaultFor: []
  },
  {
    id: "midspanDisplacement",
    label: "跨中位移",
    unit: "m",
    category: "DIAGNOSTIC",
    defaultFor: []
  },
  {
    id: "midspanAcceleration",
    label: "跨中加速度",
    unit: "m/s2",
    category: "DIAGNOSTIC",
    defaultFor: []
  }
];

export const DEFAULT_SEISMIC_TARGETS: ResponseTargetId[] = [
  "beamEndDisplacement",
  "towerBaseShear",
  "towerBaseMoment"
];

export const DEFAULT_OPERATION_TARGETS: ResponseTargetId[] = [
  "beamEndCumulativeDisplacement"
];

export const DEFAULT_OVERALL_TARGETS: ResponseTargetId[] = [
  ...DEFAULT_SEISMIC_TARGETS,
  ...DEFAULT_OPERATION_TARGETS
];

export const DEFAULT_SURROGATE_TARGET_METRIC_IDS = [
  "metric_beam_end_ux_peak",
  "metric_tower_base_shear",
  "metric_tower_base_moment",
  "metric_beam_end_ux_cumulative"
] as const;

export const getSurrogateResponseMetricOptions = (
  metrics: ResultExtractionMetricConfig[]
): ResultExtractionMetricConfig[] => {
  const enabledMetrics = new Map(
    metrics.filter(metric => metric.enabled).map(metric => [metric.id, metric])
  );
  return DEFAULT_SURROGATE_TARGET_METRIC_IDS
    .map(metricId => enabledMetrics.get(metricId))
    .filter((metric): metric is ResultExtractionMetricConfig => metric !== undefined);
};

export const SURROGATE_MODEL_OPTIONS: Array<{
  id: SurrogateModelFamily;
  label: string;
  note: string;
}> = [
  { id: "GPR", label: "高斯过程回归 (GPR)", note: "小样本非线性响应与不确定度估计" },
  { id: "KRIGING", label: "克里金 (Kriging)", note: "适合有限元小样本空间插值" },
  { id: "RBF", label: "径向基函数 (RBF)", note: "平滑插值与快速代理" },
  { id: "RESPONSE_SURFACE", label: "响应面 (Response Surface)", note: "可解释低阶趋势模型" },
  { id: "SVR", label: "支持向量回归 (SVR)", note: "小样本稳健回归候选" },
  { id: "PCE", label: "多项式混沌 (PCE)", note: "低维不确定性传播候选" },
  { id: "MARS", label: "自适应样条 (MARS)", note: "分段非线性响应候选" }
];

export const USER300_MATERIAL_OPTIONS: Array<{
  id: DamperMaterialType;
  label: string;
  note: string;
}> = [
  { id: "VISCOUS", label: "黏滞阻尼器", note: "参数为阻尼系数和速度指数" },
  { id: "EDDY_CURRENT", label: "电涡流阻尼器", note: "参数为最大出力和临界速度" },
  { id: "FRICTION", label: "摩擦阻尼器", note: "参数为最大出力" }
];

export const DAMPER_MATERIAL_OPTIONS = USER300_MATERIAL_OPTIONS;

export const DEFAULT_DAMPER_MATERIALS: Record<DamperMaterialType, DamperMaterialParameters> = {
  VISCOUS: {
    materialType: "VISCOUS",
    dampingCoefficient: 1200,
    velocityExponent: 0.35,
    unitCost: 180000
  },
  EDDY_CURRENT: {
    materialType: "EDDY_CURRENT",
    maxOutputForce: 1200,
    criticalVelocity: 0.08,
    unitCost: 220000
  },
  FRICTION: {
    materialType: "FRICTION",
    maxOutputForce: 1000,
    unitCost: 150000
  }
};

export const DEFAULT_DAMPER_CONNECTION_NODE_PAIRS: DamperConnectionNodePair[] = [
  { id: "north_tower_36_517", tower: "NORTH", nodeI: 36, nodeJ: 517, label: "北塔自定义节点 36-517" },
  { id: "north_tower_36_518", tower: "NORTH", nodeI: 36, nodeJ: 518, label: "北塔自定义节点 36-518" },
  { id: "south_tower_107_520", tower: "SOUTH", nodeI: 107, nodeJ: 520, label: "南塔自定义节点 107-520" },
  { id: "south_tower_107_521", tower: "SOUTH", nodeI: 107, nodeJ: 521, label: "南塔自定义节点 107-521" }
];

export const DEFAULT_DAMPER_CONFIG: DamperConfig = {
  enabled: true,
  elementType: "USER300",
  material: DEFAULT_DAMPER_MATERIALS.VISCOUS,
  placement: {
    layoutId: "tower_girder_end_pair",
    southTowerCount: 2,
    northTowerCount: 2,
    connectionNodePairIds: DEFAULT_DAMPER_CONNECTION_NODE_PAIRS.map(pair => pair.id)
  }
};

const VARIABLE_DEFINITIONS: Record<ExperimentDesignVariableId, Omit<ExperimentDesignVariable, "id" | "enabled">> = {
  dampingCoefficient: {
    label: "阻尼系数 c",
    unit: "kN*s/m",
    min: 1000,
    max: 10000
  },
  velocityExponent: {
    label: "速度指数 alpha",
    unit: "-",
    min: 0.3,
    max: 1.0
  },
  maxOutputForce: {
    label: "最大出力",
    unit: "kN",
    min: 600,
    max: 2400
  },
  criticalVelocity: {
    label: "临界速度",
    unit: "m/s",
    min: 0.02,
    max: 0.2
  }
};

const MATERIAL_VARIABLES: Record<DamperMaterialType, ExperimentDesignVariableId[]> = {
  VISCOUS: ["dampingCoefficient", "velocityExponent"],
  EDDY_CURRENT: ["maxOutputForce", "criticalVelocity"],
  FRICTION: ["maxOutputForce"]
};

export const getExperimentDesignVariablesForMaterial = (
  materialType: User300MaterialType
): ExperimentDesignVariable[] =>
  MATERIAL_VARIABLES[materialType].map(id => ({
    id,
    ...VARIABLE_DEFINITIONS[id],
    enabled: true
  }));

export const DEFAULT_EXPERIMENT_DESIGN_VARIABLES: ExperimentDesignVariable[] = getExperimentDesignVariablesForMaterial("VISCOUS");

export const ANALYSIS_SCENARIO_OPTIONS: Array<{
  id: AnalysisScenarioType;
  label: string;
  note: string;
}> = [
  { id: "EARTHQUAKE", label: "地震工况", note: "检查地震荷载、梁端位移、塔底剪力和弯矩" },
  { id: "WIND", label: "风荷载工况", note: "检查风荷载配置，目前 FEM 施加竖向力" },
  { id: "TRAFFIC", label: "车流工况", note: "检查车辆库或既有车流时程" },
  { id: "WIND_TRAFFIC", label: "风-车组合工况", note: "同时检查风荷载和车流荷载" }
];

export const TASK_EXECUTION_TARGET_OPTIONS: Array<{
  id: TaskExecutionTarget;
  label: string;
  note: string;
}> = [
  { id: "DESIGN_SAMPLES", label: "仅生成设计样本", note: "输出 DOE 设计矩阵和 case set" },
  { id: "COMMAND_STREAM", label: "仅生成命令流文件", note: "完成阻尼器布置和命令流拼装预览" },
  { id: "BATCH_SOLVE", label: "执行批量计算", note: "提交批处理求解并保存原始结果" },
  { id: "RESULT_EXTRACTION", label: "执行至结果提取", note: "生成结构响应和阻尼器响应数据文件" },
  { id: "SURROGATE_TRAINING", label: "执行至代理模型训练", note: "基于样本结果训练代理模型" },
  { id: "OPTIMIZATION_DECISION", label: "执行至多目标优化决策", note: "输出 Pareto、熵权和 TOPSIS 推荐" }
];

export const DOE_METHOD_OPTIONS: Array<{
  id: DoeMethod;
  label: string;
  note: string;
}> = [
  { id: "LHS", label: "拉丁超立方 LHS", note: "默认小样本空间填充方法" },
  { id: "FULL_FACTORIAL", label: "全因子", note: "适合低维少水平设计" },
  { id: "CENTRAL_COMPOSITE", label: "中心复合", note: "响应面二次项识别" },
  { id: "SOBOL", label: "Sobol 序列", note: "低差异序列采样" },
  { id: "RANDOM", label: "随机采样", note: "快速生成基线样本" }
];

export const MODULE_CATALOG: Array<Pick<ModuleStatus, "moduleId" | "label" | "route">> = [
  { moduleId: "DAMPER_BASE", label: "阻尼器基准配置", route: "/damper-base" },
  { moduleId: "DOE", label: "试验设计", route: "/experiment-design" },
  { moduleId: "LOADS", label: "荷载配置", route: "/loads" },
  { moduleId: "SOLVER_BATCH", label: "求解器批处理设置", route: "/solver" },
  { moduleId: "RESULT_EXTRACTION", label: "结果提取配置", route: "/results" },
  { moduleId: "SURROGATE_LEARNING", label: "代理模型与主动学习配置", route: "/surrogate" },
  { moduleId: "OPTIMIZATION_DECISION", label: "多目标优化与决策配置", route: "/optimization" }
];

export const RESULT_OBJECT_TYPE_OPTIONS: Array<{ id: ResultObjectType; label: string }> = [
  { id: "NODE", label: "节点" },
  { id: "ELEMENT", label: "单元" },
  { id: "SUPPORT", label: "支座" },
  { id: "DAMPER", label: "阻尼器" },
  { id: "SECTION", label: "截面" }
];

export const RESULT_RESPONSE_TYPE_OPTIONS: Array<{ id: ResultResponseType; label: string }> = [
  { id: "DISPLACEMENT", label: "位移" },
  { id: "VELOCITY", label: "速度" },
  { id: "ACCELERATION", label: "加速度" },
  { id: "REACTION", label: "节点反力" },
  { id: "ELEMENT_FORCE", label: "单元内力" },
  { id: "DAMPER_FORCE", label: "阻尼器出力" },
  { id: "DAMPER_STROKE", label: "阻尼器行程" },
  { id: "ENERGY", label: "耗能" }
];

export const RESULT_STATISTIC_OPTIONS: Array<{ id: ResultStatisticType; label: string }> = [
  { id: "PEAK", label: "峰值" },
  { id: "ABS_PEAK", label: "绝对峰值" },
  { id: "RMS", label: "均方根" },
  { id: "MEAN", label: "均值" },
  { id: "CUMULATIVE", label: "累计行程" },
  { id: "ENVELOPE", label: "包络值" },
  { id: "TIME_HISTORY", label: "时程输出" }
];

export const getResultObjectTypeLabel = (id: ResultObjectType) =>
  RESULT_OBJECT_TYPE_OPTIONS.find(item => item.id === id)?.label ?? id;

export const getResultResponseTypeLabel = (id: ResultResponseType) =>
  RESULT_RESPONSE_TYPE_OPTIONS.find(item => item.id === id)?.label ?? id;

export const getResultStatisticLabel = (id: ResultStatisticType) =>
  RESULT_STATISTIC_OPTIONS.find(item => item.id === id)?.label ?? id;

export const DEFAULT_STRUCTURAL_RESULT_METRICS: ResultExtractionMetricConfig[] = [
  {
    id: "metric_beam_end_ux_peak",
    name: "梁端纵向位移",
    objectType: "NODE",
    objectId: "481, 518",
    component: "UX",
    responseType: "DISPLACEMENT",
    statistic: "PEAK",
    enabled: true,
    autoGenerated: false,
    sourceDescription: "主梁两端代表节点 UX 峰值"
  },
  {
    id: "metric_beam_end_ux_cumulative",
    name: "梁端累计位移",
    objectType: "NODE",
    objectId: "梁端代表节点 481、518",
    component: "UX",
    responseType: "DISPLACEMENT",
    statistic: "CUMULATIVE",
    enabled: true,
    autoGenerated: false,
    sourceDescription: "节点在整个分析阶段累计走过的行程，单位 m"
  },
  {
    id: "metric_beam_end_vx_peak",
    name: "梁端纵向速度",
    objectType: "NODE",
    objectId: "481, 518",
    component: "VX",
    responseType: "VELOCITY",
    statistic: "PEAK",
    enabled: true,
    autoGenerated: false,
    sourceDescription: "主梁两端代表节点 VX 峰值"
  },
  {
    id: "metric_tower_base_shear",
    name: "塔底剪力",
    objectType: "SECTION",
    objectId: "南塔塔底截面、北塔塔底截面",
    component: "FY",
    responseType: "ELEMENT_FORCE",
    statistic: "ABS_PEAK",
    enabled: true,
    autoGenerated: false,
    sourceDescription: "南北塔底截面 FY 绝对峰值"
  },
  {
    id: "metric_tower_base_moment",
    name: "塔底弯矩",
    objectType: "SECTION",
    objectId: "南塔塔底截面、北塔塔底截面",
    component: "MZ",
    responseType: "ELEMENT_FORCE",
    statistic: "ABS_PEAK",
    enabled: true,
    autoGenerated: false,
    sourceDescription: "南北塔底截面 MZ 绝对峰值"
  },
  {
    id: "metric_tower_top_acc",
    name: "塔顶加速度",
    objectType: "NODE",
    objectId: "201, 301",
    component: "AX",
    responseType: "ACCELERATION",
    statistic: "ABS_PEAK",
    enabled: false,
    autoGenerated: false,
    sourceDescription: "塔顶代表节点 AX 绝对峰值"
  },
  {
    id: "metric_support_reaction",
    name: "支座反力",
    objectType: "SUPPORT",
    objectId: "B01-B08",
    component: "FZ",
    responseType: "REACTION",
    statistic: "ENVELOPE",
    enabled: false,
    autoGenerated: false,
    sourceDescription: "支座竖向反力包络"
  }
];

export const LOAD_CURVE_EXPORT_FORMAT_OPTIONS: Array<{
  id: LoadCurveExportFormat;
  label: string;
  note: string;
}> = [
  { id: "PNG", label: "PNG", note: "300 dpi 位图预览与汇报插图" },
  { id: "TIFF", label: "TIFF", note: "600 dpi 论文级位图" },
  { id: "SVG", label: "SVG", note: "矢量图，保留可编辑文本" },
  { id: "CUSTOM", label: "自定义", note: "按后端支持的扩展名导出" }
];

export const VEHICLE_LIBRARY_OPTIONS: VehicleLibraryOption[] = [
  {
    id: "vehicle_library_wim_2021_01",
    label: "2021-01 WIM 实测车流库",
    description: "由 2021.1.txt 一车一行监测记录派生，日均约 89521 辆，默认仅加载主梁并采用双向车流。",
    totalVehicles24h: 89521,
    heavyVehicleRatio: 0.2092,
    hourlyFlow: [
      { hour: 0, vehiclesPerHour: 1582, heavyVehicleRatio: 0.4252 },
      { hour: 1, vehiclesPerHour: 1271, heavyVehicleRatio: 0.4954 },
      { hour: 2, vehiclesPerHour: 1058, heavyVehicleRatio: 0.5108 },
      { hour: 3, vehiclesPerHour: 1041, heavyVehicleRatio: 0.5394 },
      { hour: 4, vehiclesPerHour: 1168, heavyVehicleRatio: 0.4827 },
      { hour: 5, vehiclesPerHour: 1555, heavyVehicleRatio: 0.4087 },
      { hour: 6, vehiclesPerHour: 2503, heavyVehicleRatio: 0.3361 },
      { hour: 7, vehiclesPerHour: 3559, heavyVehicleRatio: 0.2362 },
      { hour: 8, vehiclesPerHour: 4523, heavyVehicleRatio: 0.168 },
      { hour: 9, vehiclesPerHour: 5285, heavyVehicleRatio: 0.1443 },
      { hour: 10, vehiclesPerHour: 5393, heavyVehicleRatio: 0.1414 },
      { hour: 11, vehiclesPerHour: 5214, heavyVehicleRatio: 0.159 },
      { hour: 12, vehiclesPerHour: 5153, heavyVehicleRatio: 0.1572 },
      { hour: 13, vehiclesPerHour: 5748, heavyVehicleRatio: 0.1474 },
      { hour: 14, vehiclesPerHour: 6031, heavyVehicleRatio: 0.147 },
      { hour: 15, vehiclesPerHour: 6166, heavyVehicleRatio: 0.1472 },
      { hour: 16, vehiclesPerHour: 5965, heavyVehicleRatio: 0.1497 },
      { hour: 17, vehiclesPerHour: 5665, heavyVehicleRatio: 0.1623 },
      { hour: 18, vehiclesPerHour: 4693, heavyVehicleRatio: 0.1899 },
      { hour: 19, vehiclesPerHour: 4107, heavyVehicleRatio: 0.2114 },
      { hour: 20, vehiclesPerHour: 3881, heavyVehicleRatio: 0.2306 },
      { hour: 21, vehiclesPerHour: 3348, heavyVehicleRatio: 0.2589 },
      { hour: 22, vehiclesPerHour: 2655, heavyVehicleRatio: 0.3107 },
      { hour: 23, vehiclesPerHour: 1957, heavyVehicleRatio: 0.3647 }
    ]
  }
];

export const EXPERIMENT_DESIGN_SCENARIO_OPTIONS: Array<{
  id: ExperimentDesignScenarioType;
  label: string;
  note: string;
}> = [
  { id: "EARTHQUAKE", label: "地震工况", note: "梁端位移、塔底剪力、塔底弯矩为默认响应" },
  { id: "WIND", label: "风工况", note: "竖向风荷载下的阻尼器与结构响应样本" },
  { id: "TRAFFIC", label: "车工况", note: "随机车流或已有车辆数据驱动的样本计算" },
  { id: "OPERATION", label: "运营工况（风+车）", note: "运营期梁端累计位移为默认响应" }
];

export const EXPERIMENT_DESIGN_EXECUTION_GOAL_OPTIONS: Array<{
  id: ExperimentDesignExecutionGoal;
  label: string;
  note: string;
}> = [
  { id: "BATCH_CALCULATION", label: "执行至批量计算", note: "输出 DOE 设计矩阵、case set 和原始响应训练数据" },
  { id: "OPTIMIZATION_RECOMMENDATION", label: "直接得到优化推荐方案", note: "继续生成代理模型、Pareto、熵权 TOPSIS 推荐制品" }
];

export const OPTIMIZATION_EXPORT_KIND_OPTIONS: Array<{
  id: OptimizationExportKind;
  label: string;
}> = [
  { id: "PARETO_FRONT", label: "Pareto 前沿" },
  { id: "ENTROPY_WEIGHTS", label: "熵权结果" },
  { id: "TOPSIS_RECOMMENDATION", label: "TOPSIS 推荐方案" }
];

export const OPTIMIZATION_EXPORT_FORMAT_OPTIONS: Array<{
  id: OptimizationExportFormat;
  label: string;
}> = [
  { id: "CSV", label: "CSV" },
  { id: "JSON", label: "JSON" },
  { id: "PNG", label: "PNG" },
  { id: "SVG", label: "SVG" }
];

export const TRAFFIC_DATA_ARTIFACT_OPTIONS = [
  { id: "art_traffic_profile_weekday", label: "工作日分时车流数据" },
  { id: "art_traffic_load_demo", label: "24h 演示车流荷载" }
];

export const LOAD_SCENARIO_OPTIONS = [
  { id: "traffic_weekday_profile", label: "工作日分时车流" },
  { id: "vertical_wind_2026_07_01_10min", label: "10 分钟竖向风荷载" },
  { id: "peer_scaled_to_code_spectrum", label: "PEER 调幅地震动" }
];

export const WIND_START_TIME_OPTIONS = [
  { id: "2026-07-01T00:00:00+08:00", label: "2026-07-01 00:00 CST" },
  { id: "2026-07-01T06:00:00+08:00", label: "2026-07-01 06:00 CST" },
  { id: "2026-07-01T12:00:00+08:00", label: "2026-07-01 12:00 CST" }
];

export const EARTHQUAKE_CODE_OPTIONS = [
  { id: "JTG/T 2231-01", label: "JTG/T 2231-01 公路桥梁抗震设计规范" },
  { id: "GB 50011", label: "GB 50011 建筑抗震设计规范" }
];

export const EARTHQUAKE_SITE_CLASS_OPTIONS = [
  { id: "I", label: "I 类场地" },
  { id: "II", label: "II 类场地" },
  { id: "III", label: "III 类场地" },
  { id: "IV", label: "IV 类场地" }
];

export const PEER_RECORD_OPTIONS = [
  { id: "RSN1106", eventName: "Northridge-01", stationName: "Sylmar Converter Sta", label: "RSN1106 Northridge-01 / Sylmar Converter Sta" },
  { id: "RSN1602", eventName: "Duzce Turkey", stationName: "Bolu", label: "RSN1602 Duzce Turkey / Bolu" },
  { id: "RSN767", eventName: "Loma Prieta", stationName: "Gilroy Array #3", label: "RSN767 Loma Prieta / Gilroy Array #3" }
];

export const SURROGATE_RUN_OPTIONS = [
  { id: "sur_20260701_0001", label: "sur_20260701_0001 小样本代理模型" },
  { id: "sur_doe_user300_latest", label: "最新 USER300 DOE 训练模型" }
];

export const OPTIMIZATION_OBJECTIVE_MODE_OPTIONS: Array<{
  id: OptimizationObjectiveMode;
  label: string;
  note: string;
}> = [
  { id: "SEISMIC", label: "地震工况优化", note: "梁端位移、塔底剪力、塔底弯矩" },
  { id: "OPERATION", label: "运营工况优化", note: "梁端累计位移" },
  { id: "OVERALL", label: "整体优化", note: "同时考虑地震三项响应和运营梁端累计位移" },
  { id: "CUSTOM", label: "自定义目标", note: "从响应目标目录中手动选择" }
];

export const getOptimizationTargetsByMode = (mode: OptimizationObjectiveMode): ResponseTargetId[] => {
  if (mode === "SEISMIC") return DEFAULT_SEISMIC_TARGETS;
  if (mode === "OPERATION") return DEFAULT_OPERATION_TARGETS;
  if (mode === "OVERALL") return DEFAULT_OVERALL_TARGETS;
  return DEFAULT_OVERALL_TARGETS;
};

export const DEFAULT_LEGACY_EXPERIMENT_DESIGN_VARIABLES: ExperimentDesignVariable[] = [
  {
    id: "dampingCoefficient",
    label: "阻尼系数 c",
    unit: "kN*s/m",
    min: 1000,
    max: 10000,
    enabled: true
  },
  {
    id: "velocityExponent",
    label: "速度指数 alpha",
    unit: "-",
    min: 0.3,
    max: 1.0,
    enabled: true
  },
];

export const getTargetLabel = (id: ResponseTargetId) =>
  RESPONSE_TARGETS.find(target => target.id === id)?.label || id;
