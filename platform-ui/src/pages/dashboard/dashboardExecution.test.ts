import { describe, expect, it } from "vitest";

import { buildDashboardExecutionRequest } from "./dashboardExecution";

describe("buildDashboardExecutionRequest", () => {
  it("只为已登记的地震基线优化生成严格工作流请求", () => {
    const request = buildDashboardExecutionRequest({
      projectName: "bridge",
      modelFileName: "STbridge.txt",
      solver: "ANSYS",
      scenario: "EARTHQUAKE",
      executionTarget: "OPTIMIZATION_DECISION",
      requiredModules: ["MODEL_IMPORT", "DAMPER_CONFIG"],
      executionTimeoutS: 7200
    });

    expect(request).toMatchObject({
      type: "MULTI_OBJECTIVE_OPTIMIZATION",
      params: {
        runMode: "REAL_BASELINE_OPTIMIZATION",
        scenario: "EARTHQUAKE",
        executionTarget: "OPTIMIZATION_DECISION"
      }
    });
    expect(request?.params).not.toHaveProperty("damperBaseConfig");
    expect(request?.params).not.toHaveProperty("optimizationDecisionConfig");
  });

  it("缺少专用输入的其他 Dashboard 目标不生成无效通用请求", () => {
    expect(buildDashboardExecutionRequest({
      projectName: "bridge",
      modelFileName: "STbridge.txt",
      solver: "ANSYS",
      scenario: "EARTHQUAKE",
      executionTarget: "BATCH_SOLVE",
      requiredModules: ["MODEL_IMPORT"],
      executionTimeoutS: 7200
    })).toBeNull();
  });
});
