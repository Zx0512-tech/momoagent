import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import type { AgentRun } from "../../../api/agentApi";
import { ApprovalCard } from "./ApprovalCard";
import { EvidenceCard } from "./EvidenceCard";
import { PlanCard } from "./PlanCard";
import { ProgressCard } from "./ProgressCard";
import { ResultCard } from "./ResultCard";
import { buildComparisonChartData, formatComparisonCaseLabel } from "./timeseriesComparison";

/**
 * 卡片内可能含 react-router 的 Link，需在 Router 上下文中渲染。
 * renderToStaticMarkup 会把中文转成 HTML 实体，断言前先解码。
 */
function render(node: Parameters<typeof renderToStaticMarkup>[0]): string {
  return renderToStaticMarkup(<MemoryRouter>{node}</MemoryRouter>).replace(
    /&#x([0-9a-fA-F]+);/g,
    (_, hex: string) => String.fromCodePoint(Number.parseInt(hex, 16))
  );
}

const baseRun: AgentRun = {
  runId: "agr_1",
  sessionId: "ags_1",
  goal: "优化黏滞阻尼器",
  status: "WAITING_APPROVAL",
  currentStage: "WAITING_APPROVAL",
  artifactIds: []
};

describe("ApprovalCard", () => {
  it("以对话内联段落展示审批，不渲染不可读的冻结参数块", () => {
    const markup = render(
      <ApprovalCard
        busy={false}
        onDecide={() => {}}
        approval={{
          approvalId: "apv_1",
          runId: "agr_1",
          action: "RUN_FULL_OPTIMIZATION",
          status: "PENDING",
          summary: "执行 15 个真实 DOE 并复核推荐点",
          frozenAction: { solver: "OPENSEESPY_INPROC" },
          frozenActionSha256: "a".repeat(64)
        }}
      />
    );

    expect(markup).toContain("整单优化审批");
    expect(markup).toContain("执行 15 个真实 DOE 并复核推荐点");
    expect(markup).toContain("批准并执行");
    expect(markup).not.toContain("查看冻结参数与 SHA256");
    expect(markup).not.toContain("SHA256");
    expect(markup).not.toContain("OPENSEESPY_INPROC");
  });

  it("审批完成后保留历史状态，不再显示可重复操作按钮", () => {
    const markup = render(
      <ApprovalCard
        busy={false}
        onDecide={() => {}}
        approval={{
          approvalId: "apv_1",
          runId: "agr_1",
          action: "RUN_ENGINEERING_WORKFLOW",
          status: "APPROVED",
          summary: "已批准执行单次分析。"
        }}
      />
    );

    expect(markup).toContain("已批准");
    expect(markup).not.toContain("批准并执行");
  });
});

describe("EvidenceCard", () => {
  it("展示版本画像、输入来源与输出清单下载", () => {
    const markup = render(
      <EvidenceCard
        run={{
          ...baseRun,
          solverVersionProfile: {
            schemaVersion: "1.0",
            solver: { name: "OpenSees", version: "3.7.1", versionSource: "BUILTIN_RUNTIME" },
            sdk: { package: "openseespy", version: "3.7.1" },
            responseContract: { id: "OPENSEES_STBRIDGE_R1", version: "R1", description: "响应合同" },
            userElement: { name: "USER300", calibrationHashVerified: true }
          },
          inputProvenance: [
            { field: "solver", source: "USER_DECISION", value: "OPENSEESPY_INPROC" },
            { field: "loadDataset", source: "VERIFIED_TEMPLATE", value: "registered" }
          ],
          outputManifestArtifactId: "art_manifest"
        }}
      />
    );

    expect(markup).toContain("工程证据");
    expect(markup).toContain("OpenSees 3.7.1");
    expect(markup).toContain("USER300 校准哈希已验证");
    expect(markup).toContain("用户要求");
    expect(markup).toContain("已验证模板");
    expect(markup).toContain("art_manifest/download");
  });

  it("没有任何证据时不渲染，避免出现空卡片", () => {
    expect(renderToStaticMarkup(<EvidenceCard run={baseRun} />)).toBe("");
  });
});

describe("PlanCard", () => {
  it("如实标注 LLM 规划模式", () => {
    const markup = render(
      <PlanCard
        run={{
          ...baseRun,
          plannerMode: "LLM",
          intent: { solver: "OPENSEESPY_INPROC", damperType: "viscous" },
          plan: ["执行无阻尼基线", "生成 15 个真实 DOE"]
        }}
      />
    );

    expect(markup).toContain("LLM 结构化意图");
    expect(markup).toContain("生成 15 个真实 DOE");
  });

  it("单次无阻尼分析只展示一次真实求解，不显示优化 DOE 与候选点", () => {
    const markup = render(
      <PlanCard
        run={{
          ...baseRun,
          taskType: "ANALYSIS",
          plannerMode: "LLM",
          intent: { solver: "OPENSEESPY_INPROC", damperType: null },
          workflowContract: {
            executionEstimate: { mode: "SINGLE_ANALYSIS", realSolveCount: 1 },
            budget: { doeDesignCount: 15, candidateCount: 728 }
          }
        }}
      />
    );

    expect(markup).toContain("真实求解 1 次");
    expect(markup).not.toContain("DOE 15 组");
    expect(markup).not.toContain("候选 728 点");
    expect(markup).not.toContain("阻尼器布置");
  });

  it("优化计划展示已披露的真实求解最小值和最大值", () => {
    const markup = render(
      <PlanCard
        run={{
          ...baseRun,
          taskType: "FULL_OPTIMIZATION",
          workflowContract: {
            executionEstimate: {
              doeDesignCount: 15,
              candidateCount: 728,
              realSolveCount: 21,
              estimatedRealSolvesMin: 17,
              estimatedRealSolvesMax: 21
            }
          }
        }}
      />
    );

    expect(markup).toContain("预计真实求解 17–21 次");
  });
});

describe("ResultCard", () => {
  it("结果追问按中文工程指标和合理精度展示，不渲染原始 Markdown", () => {
    const markup = render(
      <ResultCard
        run={{
          ...baseRun,
          taskType: "INQUIRY",
          status: "SUCCEEDED",
          resultMetadata: {
            runId: "agr_source",
            sessionId: "ags_source",
            taskType: "ANALYSIS",
            status: "SUCCEEDED",
            condition: "EARTHQUAKE",
            model: "STbridge",
            solver: "OPENSEESPY_INPROC",
            hasDamper: false,
            damperTypes: [],
            damperParameters: {}
          },
          resultSummary: {
            narrativeMode: "DETERMINISTIC",
            message: "已读取 2 项结果指标，数值均来自已登记的只读结果文件。",
            inquiryMetrics: [
              {
                metricId: "max_girder_end_displacement",
                label: "最大梁端位移",
                sourceColumn: "displacement",
                peakAbsolute: 0.3919301166619712,
                peakSigned: 0.3919301166619712,
                unit: "m",
                peakTimeS: 20.050000000000335,
                sampleCount: 4001
              },
              {
                metricId: "max_tower_base_shear",
                label: "最大塔底剪力",
                sourceColumn: "tower_base_shear",
                peakAbsolute: 52299849.63715227,
                peakSigned: 52299849.63715227,
                unit: "N",
                peakTimeS: 22.400000000000702,
                sampleCount: 4001
              }
            ]
          }
        }}
      />
    );

    expect(markup).toContain("只读结果");
    expect(markup).toContain("最大梁端位移");
    expect(markup).toContain("0.3919");
    expect(markup).toContain("m</");
    expect(markup).toContain("最大塔底剪力");
    expect(markup).toContain("52.30");
    expect(markup).toContain("MN</");
    expect(markup).toContain("22.40 s");
    expect(markup).toContain("<ul");
    expect(markup).toContain("<li");
    expect(markup).not.toContain("52299849.63715227");
    expect(markup).not.toContain("有符号峰值");
    expect(markup).not.toContain("模型叙述未通过数字证据校验");
    expect(markup).not.toContain("未通过全部真实 FEM 门槛");
  });

  it("TOPSIS 目标指标使用中文名并保留未知标识和原始数值", () => {
    const markup = render(
      <ResultCard
        run={{
          ...baseRun,
          taskType: "INQUIRY",
          status: "SUCCEEDED",
          resultSummary: {
            inquiryTopsis: [{
              rank: 1,
              paretoIndex: 0,
              score: 0.6359,
              parameters: { c: 7600, alpha: 0.8 },
              objectives: {
                "earthquake:max_girder_end_displacement": 0.13099,
                "earthquake:max_tower_base_shear": 4.809e7,
                "earthquake:max_tower_base_moment": 1.647e9,
                "custom:unknown_metric": 42
              }
            }]
          }
        }}
      />
    );

    expect(markup).toContain("梁端位移=0.1310");
    expect(markup).toContain("塔底剪力=4.809e+7");
    expect(markup).toContain("塔底弯矩=1.647e+9");
    expect(markup).toContain("custom:unknown_metric=42.00");
    expect(markup).not.toContain("earthquake:max_girder_end_displacement");
    expect(markup).not.toContain("earthquake:max_tower_base_shear");
    expect(markup).not.toContain("earthquake:max_tower_base_moment");
  });

  it("诊断级结果必须显式标注，不能冒充最终结论", () => {
    const markup = render(
      <ResultCard
        run={{
          ...baseRun,
          status: "COMPLETED_DIAGNOSTIC",
          resultSummary: {
            evidenceMode: "DIAGNOSTIC_ONLY",
            accepted: false,
            message: "至少一个工程门槛未通过",
            checks: { realRunMode: true, reviewAccepted: false }
          }
        }}
      />
    );

    expect(markup).toContain("仅供诊断");
    expect(markup).toContain("至少一个工程门槛未通过");
  });

  it("展示没有 caseResults 的单次分析指标和自然语言结论", () => {
    const markup = render(
      <ResultCard
        run={{
          ...baseRun,
          status: "SUCCEEDED",
          taskType: "ANALYSIS",
          resultSummary: {
            accepted: true,
            evidenceMode: "REAL_FEM",
            objectives: { max_tower_base_shear: 1234567 },
            narrativeSummary: "真实求解已完成。"
          }
        }}
      />
    );

    expect(markup).toContain("塔底剪力");
    expect(markup).toContain("真实求解已完成。");
    expect(markup).toContain("1.235");
    expect(markup).toContain(">MN<");
    expect(markup).toContain("查看时程曲线");
  });

  it("标记已完成结果的工况、模型和阻尼参数", () => {
    const markup = render(
      <ResultCard
        run={{
          ...baseRun,
          status: "SUCCEEDED",
          taskType: "ANALYSIS",
          resultMetadata: {
            runId: baseRun.runId,
            sessionId: baseRun.sessionId,
            taskType: "ANALYSIS",
            status: "SUCCEEDED",
            condition: "EARTHQUAKE",
            model: "STbridge",
            solver: "OPENSEESPY_INPROC",
            hasDamper: true,
            damperTypes: ["VISCOUS"],
            damperParameters: { dampingCoefficient: 7600, velocityExponent: 0.8 },
            updatedAt: "2026-08-12T00:00:00Z"
          },
          resultSummary: { accepted: true, evidenceMode: "REAL_FEM" }
        }}
      />
    );

    expect(markup).toContain("地震");
    expect(markup).toContain("STbridge");
    expect(markup).toContain("OPENSEESPY_INPROC");
    expect(markup).toContain("VISCOUS");
    expect(markup).toContain("dampingCoefficient=7600");
    expect(markup).toContain("velocityExponent=0.8000");
  });

  it("展示优化基线、推荐值及变化百分比", () => {
    const markup = render(
      <ResultCard
        run={{
          ...baseRun,
          status: "SUCCEEDED",
          taskType: "DAMPER_OPTIMIZATION",
          resultSummary: {
            accepted: true,
            evidenceMode: "REAL_FEM",
            baselineObjectives: { max_tower_base_shear: 1000000 },
            recommendedObjectives: { max_tower_base_shear: 800000 },
            recommendedParameters: { c: 1200 }
          }
        }}
      />
    );

    expect(markup).toContain("基线");
    expect(markup).toContain("推荐");
    expect(markup).toContain("-20.00%");
    expect(markup).toContain("推荐参数 · c");
  });

  it("展示追问生成的图件并提供下载入口", () => {
    const markup = render(
      <ResultCard
        run={{
          ...baseRun,
          taskType: "INQUIRY",
          status: "SUCCEEDED",
          figureArtifactIds: ["art_plot_1"],
          resultSummary: {
            evidenceMode: "REAL_FEM",
            figures: [{ claim: "梁端位移时程", artifactId: "art_plot_1", metrics: ["max_girder_end_displacement"] }]
          }
        }}
      />
    );

    expect(markup).toContain("梁端位移时程");
    expect(markup).toContain("art_plot_1/download");
    expect(markup).toContain("按需绘图");
  });

  it("参数扫描只展示参数明细表，不重复展示原始参数、结论和柱状图", () => {
    const markup = render(
      <ResultCard
        run={{
          ...baseRun,
          status: "SUCCEEDED",
          taskType: "DAMPER_PARAMETER_SWEEP",
          resultMetadata: {
            runId: baseRun.runId,
            sessionId: baseRun.sessionId,
            taskType: "DAMPER_PARAMETER_SWEEP",
            status: "SUCCEEDED",
            condition: "EARTHQUAKE",
            model: "STbridge",
            solver: "OPENSEESPY_INPROC",
            hasDamper: true,
            damperTypes: ["VISCOUS"],
            damperParameters: { case_viscous_c1000_alpha03: { c: 1000, alpha: 0.3, vfloor: 0.001 } }
          },
          resultSummary: {
            accepted: true,
            evidenceMode: "REAL_FEM",
            message: "所有阻尼器参数案例均通过真实 FEM 证据审查。",
            caseResults: [
              {
                caseId: "case_viscous_c1000_alpha03",
                damperType: "VISCOUS",
                parameters: { c: 1000, alpha: 0.3, vfloor: 0.001 },
                objectives: {
                  max_girder_end_displacement: 0.391690,
                  max_tower_base_shear: 52291700
                }
              },
              {
                caseId: "case_viscous_c2000_alpha05",
                damperType: "VISCOUS",
                parameters: { c: 2000, alpha: 0.5, vfloor: 0.001 },
                objectives: {
                  max_girder_end_displacement: 0.391784,
                  max_tower_base_shear: 52294800
                }
              }
            ]
          }
        }}
      />
    );

    expect(markup).toContain("c=1000，α=0.3000");
    expect(markup).toContain("c=2000，α=0.5000");
    expect(markup).toContain("0.3917 m");
    expect(markup).toContain("52.29 MN");
    expect(markup).not.toContain("阻尼参数");
    expect(markup).not.toContain("vfloor=0.001000");
    expect(markup).not.toContain("case_viscous_c1000_alpha03");
    expect(markup).not.toContain("所有阻尼器参数案例均通过真实 FEM 证据审查");
    expect(markup).not.toContain("recharts-responsive-container");
  });

  it("制品下载默认收起，展开入口沿用时程曲线的折叠形式", () => {
    const markup = render(
      <ResultCard
        run={{
          ...baseRun,
          status: "SUCCEEDED",
          taskType: "ANALYSIS",
          artifactIds: ["art_ts", "art_shear"],
          resultArtifacts: [
            { artifactId: "art_ts", name: "timeseries.csv", kind: "CSV_TIMESERIES" },
            { artifactId: "art_shear", name: "tower_base_shear_components.csv", kind: "CSV_TABLE" }
          ],
          resultSummary: { accepted: true, evidenceMode: "REAL_FEM" }
        }}
      />
    );

    expect(markup).toContain("查看制品下载");
    expect(markup).not.toContain("综合响应时程（梁端位移 Node36/107 UX、加速度、塔底内力）");
    expect(markup).not.toContain("塔底剪力（截面/惯性分量）");
    expect(markup).not.toContain('role="list"');
  });

  it("接口返回空制品元数据时回退到已有制品 ID", () => {
    const markup = render(
      <ResultCard
        run={{
          ...baseRun,
          status: "SUCCEEDED",
          taskType: "ANALYSIS",
          artifactIds: ["art_legacy"],
          resultArtifacts: [],
          resultSummary: { accepted: true, evidenceMode: "REAL_FEM" }
        }}
      />
    );

    expect(markup).toContain("查看制品下载");
    expect(markup).not.toContain("art_legacy/download");
  });
});

describe("TimeseriesSection", () => {
  it("默认 vfloor 不显示在时程参数工况标签中，非默认值仍保留", () => {
    expect(formatComparisonCaseLabel({
      label: "旧标签",
      parameters: { c: 1000, alpha: 0.3, vfloor: 0.001 }
    })).toBe("c=1000，α=0.3");
    expect(formatComparisonCaseLabel({
      label: "旧标签",
      parameters: { c: 1000, alpha: 0.3, vfloor: 0.002 }
    })).toBe("c=1000，α=0.3，vfloor=0.002");
  });

  it("按响应量分别构造多参数工况曲线，避免混合物理单位", () => {
    const charts = buildComparisonChartData(
      [
        {
          caseId: "case_c1000",
          series: { time: [0, 1], displacement: [0, 0.1], tower_base_shear: [0, 10] }
        },
        {
          caseId: "case_c2000",
          series: { time: [0, 1], displacement: [0, 0.2], tower_base_shear: [0, 20] }
        }
      ],
      ["displacement", "tower_base_shear"]
    );

    expect(charts).toHaveLength(2);
    expect(charts[0].column).toBe("displacement");
    expect(charts[0].rows[1]).toMatchObject({ time: 1, case_c1000: 0.1, case_c2000: 0.2 });
    expect(charts[1].column).toBe("tower_base_shear");
    expect(charts[1].rows[1]).toMatchObject({ time: 1, case_c1000: 10, case_c2000: 20 });
  });
});

describe("ProgressCard", () => {
  it("等待审批时提供取消运行入口", () => {
    const markup = render(
      <ProgressCard busy={false} onCancel={() => {}} run={baseRun} />
    );

    expect(markup).toContain("取消运行");
  });

  it("按冻结工作流快照展示当前步骤、已完成步骤和工具轨迹", () => {
    const markup = render(
      <ProgressCard
        busy={false}
        onCancel={() => {}}
        run={{
          ...baseRun,
          runtimeMode: "WORKFLOW_HARNESS",
          currentStep: "WAITING_APPROVAL",
          completedSteps: ["REQUIREMENTS", "LOAD_PREPARATION", "PREFLIGHT"],
          workflowSnapshot: {
            workflowId: "analysis",
            version: "1.0.0",
            initialStep: "REQUIREMENTS",
            terminalSteps: ["COMPLETED", "FAILED", "CANCELLED"],
            steps: [
              { stepId: "REQUIREMENTS", title: "需求确定", allowedTools: ["analysis.plan"] },
              { stepId: "PREFLIGHT", title: "环境预检", allowedTools: ["analysis.prepare"] },
              { stepId: "WAITING_APPROVAL", title: "冻结审批", allowedTools: ["approval.request"] }
            ]
          },
          toolCalls: [
            { toolCallId: "call_1", toolName: "workflow.start", stepId: "ROUTING", status: "SUCCEEDED" }
          ]
        }}
      />
    );

    expect(markup).toContain("冻结审批");
    expect(markup).toContain("需求确定");
    expect(markup).toContain("workflow.start");
    expect(markup).toContain("WORKFLOW_HARNESS");
  });

  it("单次分析只有粗粒度进度时也照常显示阶段、百分比和说明", () => {
    const markup = render(
      <ProgressCard
        busy={false}
        onCancel={() => {}}
        run={{
          ...baseRun,
          status: "WAITING_JOB",
          currentStage: "WAITING_JOB",
          jobProgress: { phase: "求解中", message: "worker 心跳正常", percent: 30 }
        }}
      />
    );

    expect(markup).toContain("求解中");
    expect(markup).toContain("30%");
    expect(markup).toContain("worker 心跳正常");
    // 没有批量字段时不能编造算例计数。
    expect(markup).not.toContain("个算例");
  });

  it("批量求解时在粗粒度进度之上叠加完成计数与在跑算例", () => {
    const markup = render(
      <ProgressCard
        busy={false}
        onCancel={() => {}}
        run={{
          ...baseRun,
          status: "WAITING_JOB",
          currentStage: "WAITING_JOB",
          jobProgress: {
            phase: "求解中",
            message: "已完成 8/15 个算例，2 个正在求解",
            percent: 55,
            completedCases: 8,
            totalCases: 15,
            activeCases: [
              { caseId: "case_09", percent: 42, step: 420, totalSteps: 1000 },
              { caseId: "case_10", percent: 18 },
              { caseId: "case_11", percent: 7 },
              { caseId: "case_12", percent: 3 }
            ]
          }
        }}
      />
    );

    expect(markup).toContain("55%");
    expect(markup).toContain("已完成 8 / 15 个算例");
    expect(markup.match(/已完成 8\s*\/\s*15 个算例/g)).toHaveLength(1);
    expect(markup).toContain("case_09 42%");
    expect(markup).toContain("case_11 7%");
    // 最多展示 3 个，其余折叠成一行。
    expect(markup).not.toContain("case_12");
    expect(markup).toContain("还有 1 个算例正在求解");
  });

  it("保留上一帧进度时提示正在刷新", () => {
    const markup = render(
      <ProgressCard
        busy={false}
        onCancel={() => {}}
        run={{
          ...baseRun,
          status: "WAITING_JOB",
          currentStage: "WAITING_JOB",
          jobProgressRefreshing: true,
          jobProgress: {
            phase: "求解中",
            message: "已完成 8/15 个算例",
            percent: 55,
            completedCases: 8,
            totalCases: 15
          }
        }}
      />
    );

    expect(markup).toContain("正在刷新");
    expect(markup).toContain("已完成 8 / 15 个算例");
  });

  it("活动算例存在时不显示 100%", () => {
    const markup = render(
      <ProgressCard
        busy={false}
        onCancel={() => {}}
        run={{
          ...baseRun,
          status: "WAITING_JOB",
          currentStage: "WAITING_JOB",
          jobProgress: {
            phase: "求解中",
            message: "已完成 15/15 个算例，1 个正在求解",
            percent: 100,
            completedCases: 15,
            totalCases: 15,
            activeCases: [{ caseId: "case_16", percent: 6 }]
          }
        }}
      />
    );

    expect(markup).toContain("99%");
    expect(markup).not.toContain(">100%</span>");
  });

  it("求解状态仍在运行时没有活动算例也不显示 100%", () => {
    const markup = render(
      <ProgressCard
        busy={false}
        onCancel={() => {}}
        run={{
          ...baseRun,
          status: "WAITING_JOB",
          currentStage: "WAITING_JOB",
          jobProgress: {
            phase: "求解中",
            message: "已完成 15/15 个算例",
            percent: 100,
            completedCases: 15,
            totalCases: 15,
            activeCases: []
          }
        }}
      />
    );

    expect(markup).toContain("99%");
    expect(markup).not.toContain(">100%</span>");
  });

  it("优化 Job 将基线与 DOE 合并为一个求解阶段", () => {
    const markup = render(
      <ProgressCard
        busy={false}
        onCancel={() => {}}
        run={{
          ...baseRun,
          status: "WAITING_JOB",
          currentStep: "BASELINE",
          currentStage: "WAITING_JOB",
          workflowSnapshot: {
            workflowId: "full_optimization",
            version: "1.1.0",
            initialStep: "REQUIREMENTS",
            terminalSteps: ["COMPLETED", "FAILED", "CANCELLED"],
            steps: [
              { stepId: "BASELINE", title: "无控基线", allowedTools: ["optimization.run_baseline"] },
              { stepId: "DOE", title: "受控 DOE", allowedTools: ["optimization.run_doe"] }
            ]
          },
          jobProgress: {
            phase: "求解中",
            message: "已完成 1/15 个算例，4 个正在求解",
            percent: 22,
            completedCases: 1,
            totalCases: 15,
            activeCases: [{ caseId: "case_02", percent: 46 }]
          }
        }}
      />
    );

    expect(markup).toContain("求解计算");
    expect(markup).not.toContain(">无控基线</li>");
    expect(markup).not.toContain(">受控 DOE</li>");
  });

  it("缺少 currentStep 时也能根据批次进度离开无控基线", () => {
    const markup = render(
      <ProgressCard
        busy={false}
        onCancel={() => {}}
        run={{
          ...baseRun,
          status: "WAITING_JOB",
          currentStage: "WAITING_JOB",
          workflowSnapshot: {
            workflowId: "full_optimization",
            version: "1.1.0",
            initialStep: "REQUIREMENTS",
            terminalSteps: ["COMPLETED", "FAILED", "CANCELLED"],
            steps: [
              { stepId: "BASELINE", title: "无控基线", allowedTools: ["optimization.run_baseline"] },
              { stepId: "DOE", title: "受控 DOE", allowedTools: ["optimization.run_doe"] }
            ]
          },
          jobProgress: {
            phase: "求解中",
            message: "已完成 15/15 个算例，1 个正在求解",
            percent: 99,
            completedCases: 15,
            totalCases: 15,
            activeCases: [{ caseId: "case_16", percent: 6 }]
          }
        }}
      />
    );

    expect(markup).toContain("求解计算");
    expect(markup).not.toContain(">无控基线</li>");
    expect(markup).not.toContain(">受控 DOE</li>");
  });
});
