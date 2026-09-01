import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import RunComparisonCard from "./RunComparisonCard";


describe("RunComparisonCard", () => {
  it("renders server-computed comparison facts without recomputing them", () => {
    const markup = renderToStaticMarkup(<RunComparisonCard comparison={{
      schemaVersion: "1.0",
      sessionId: "ags_1",
      projectId: "agp_1",
      baselineRunId: "agr_base",
      compatibility: "DIRECT",
      metricIds: ["max_tower_base_shear"],
      runs: [
        {
          runId: "agr_base",
          taskType: "ANALYSIS",
          solver: "ANSYS",
          loadKind: "EARTHQUAKE",
          modelSha256: "a".repeat(64),
          loadSha256: "b".repeat(64),
          responseIds: ["max_tower_base_shear"],
          reportArtifactId: "art_report_base",
          metrics: {
            max_tower_base_shear: {
              value: 100,
              unit: "N",
              label: "塔底剪力",
              direction: "LOWER_IS_BETTER",
              evidence: { artifactId: "art_base" }
            }
          }
        },
        {
          runId: "agr_candidate",
          taskType: "ANALYSIS",
          solver: "ANSYS",
          loadKind: "EARTHQUAKE",
          modelSha256: "a".repeat(64),
          loadSha256: "b".repeat(64),
          responseIds: ["max_tower_base_shear"],
          reportArtifactId: "art_report_candidate",
          metrics: {
            max_tower_base_shear: {
              value: 80,
              unit: "N",
              label: "塔底剪力",
              direction: "LOWER_IS_BETTER",
              evidence: { artifactId: "art_candidate" }
            }
          }
        }
      ],
      comparisons: [{
        baselineRunId: "agr_base",
        runId: "agr_candidate",
        compatibility: "DIRECT",
        metrics: {
          max_tower_base_shear: {
            baseline: 100,
            candidate: 80,
            difference: -20,
            relativeChange: -0.2,
            relativeChangePercent: -20,
            unit: "N",
            interpretation: "PERFORMANCE_CHANGE"
          }
        }
      }],
      rankings: [],
      warnings: [],
      interpretationLimit: "DIRECT 才表示可直接比较。"
    }} />);

    expect(markup).toContain("跨 Run 工程对比");
    expect(markup).toContain("同模型同荷载，可直接比较");
    expect(markup).toContain("agr_base");
    expect(markup).toContain("agr_candidate");
    expect(markup).toContain("-20%");
    expect(markup).toContain("塔底剪力");
  });
});
