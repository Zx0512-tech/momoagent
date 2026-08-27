export interface MockTrainingMetric {
  model: string;
  target: string;
  r2: number;
  rmse: number;
  mae: number;
  cvScore: number;
}

export interface MockInfillItem {
  caseId: string;
  params: string;
  status: "QUEUED" | "RUNNING" | "SUCCEEDED";
  source: string;
}

export const SURROGATE_SIMULATION_LABEL = "模拟数据";

export function buildMockTrainingMetrics(
  targetMetricIds: string[],
  modelFamilies: string[],
  getMetricLabel: (id: string) => string
): MockTrainingMetric[] {
  return targetMetricIds.map((target, idx) => {
    const selectedModel = modelFamilies[idx % modelFamilies.length] || "GPR";
    return {
      model: selectedModel,
      target: getMetricLabel(target),
      r2: 0.96 + idx * 0.004,
      rmse: 0.018 + idx * 0.003,
      mae: 0.011 + idx * 0.002,
      cvScore: 0.95 + idx * 0.004
    };
  });
}

export function buildMockInfillQueue(): MockInfillItem[] {
  return [
    { caseId: "infill_case_001", params: "α = 0.35, β = 0.65", status: "QUEUED", source: "不确定性区域采样" },
    { caseId: "infill_case_002", params: "α = 0.42, β = 0.58", status: "RUNNING", source: "Pareto 边界膝点探索" },
    { caseId: "infill_case_003", params: "α = 0.50, β = 0.50", status: "QUEUED", source: "不确定性区域采样" }
  ];
}
