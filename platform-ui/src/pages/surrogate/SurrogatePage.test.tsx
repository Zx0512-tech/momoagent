import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { SurrogatePage } from "./SurrogatePage";
import { SURROGATE_SIMULATION_LABEL, buildMockTrainingMetrics } from "./surrogateDemo";

describe("SurrogatePage mock demo", () => {
  it("mock 指标构造函数只用于演示，不冒充真实 CV 证据来源", () => {
    const metrics = buildMockTrainingMetrics(["metric_a"], ["GPR"], id => id);
    expect(metrics).toHaveLength(1);
    expect(metrics[0].r2).toBeGreaterThan(0.9);
    expect(SURROGATE_SIMULATION_LABEL).toBe("模拟数据");
  });

  it("mock 页面标题使用模拟数据而不是真实 FEM 复核", () => {
    const markup = renderToStaticMarkup(<SurrogatePage />);
    expect(markup).toContain("模拟数据");
    expect(markup).not.toContain("真实 FEM 样本复核队列");
  });
});
