import type { AgentRun } from "../../../api/agentApi";
import { cardStyles } from "./cardStyles";

const taskLabels: Record<string, string> = {
  ANALYSIS: "有限元分析",
  DAMPER_OPTIMIZATION: "阻尼器优化",
  DAMPER_COMPARISON: "阻尼器对比",
  DAMPER_PARAMETER_SWEEP: "阻尼器参数批量计算",
  FULL_OPTIMIZATION: "完整优化",
  LOAD_IMPORT: "荷载导入"
};

const solverLabels: Record<string, string> = {
  ANSYS: "ANSYS / MAPDL",
  OPENSEESPY_INPROC: "OpenSeesPy"
};

const damperLabels: Record<string, string> = {
  viscous: "黏滞阻尼器",
  friction: "摩擦阻尼器",
  eddy_current: "电涡流阻尼器",
  VISCOUS: "黏滞阻尼器",
  FRICTION: "摩擦阻尼器",
  EDDY_CURRENT: "电涡流阻尼器"
};

/** 展示智能体解析出的受控工程意图与执行计划。 */
export const PlanCard = ({ run }: { run: AgentRun }) => {
  const intent = run.intent ?? {};
  const taskType = String(run.taskType ?? "");
  const solver = String(intent.solver ?? "");
  const damper = String(intent.damperType ?? "");
  const objectives = Array.isArray(intent.responseIds) ? (intent.responseIds as string[]) : [];
  const sources = (run.workflowContract?.fieldSources ?? {}) as Record<string, string>;
  const sourceSuffix = (field: string) => sources[field] === "DEFAULT" ? "（默认，未单独确认）" : "";
  const loadKind = String(intent.loadKind ?? run.workflowContract?.loadKind ?? "");
  const hasDamper = Boolean(damper) || ["DAMPER_OPTIMIZATION", "DAMPER_COMPARISON", "DAMPER_PARAMETER_SWEEP", "FULL_OPTIMIZATION"].includes(taskType);
  const layout = hasDamper ? String(intent.selectedLayoutId ?? run.workflowContract?.selectedLayoutId ?? "") : "";
  const contract = run.workflowContract ?? {};
  const estimate = (contract.executionEstimate ?? contract) as Record<string, unknown>;
  const solveEstimate = (
    typeof estimate.estimatedRealSolvesMin === "number"
    && typeof estimate.estimatedRealSolvesMax === "number"
  )
    ? `预计真实求解 ${estimate.estimatedRealSolvesMin}–${estimate.estimatedRealSolvesMax} 次`
    : typeof estimate.realSolveCount === "number"
      ? `预计真实求解 ${estimate.realSolveCount} 次`
      : "";
  const budgetCaseCount = (contract.budget as Record<string, unknown> | undefined)?.caseCount;
  const budgetParts = taskType === "ANALYSIS"
    ? ["真实求解 1 次"]
    : taskType === "DAMPER_COMPARISON"
      ? [typeof budgetCaseCount === "number" ? `真实求解 ${budgetCaseCount} 次` : "真实求解 2–3 次"]
      : taskType === "DAMPER_PARAMETER_SWEEP"
        ? [
          typeof budgetCaseCount === "number" ? `参数案例 ${budgetCaseCount} 个` : "批量参数求解",
          solveEstimate,
        ].filter(Boolean)
    : [
      typeof estimate.doeDesignCount === "number" ? `DOE ${estimate.doeDesignCount} 组` : "",
      typeof estimate.candidateCount === "number" ? `候选 ${estimate.candidateCount} 点` : "",
      solveEstimate
    ].filter(Boolean);

  return (
    <div style={cardStyles.card}>
      <div style={cardStyles.header}>
        <span style={cardStyles.title}>受控工程计划</span>
        <span style={cardStyles.badge}>
          {run.plannerMode === "LLM_TOOL_CALL"
            ? "LLM 工具意图"
            : run.plannerMode === "LLM"
              ? "LLM 结构化意图"
              : "未提供规划模式"}
        </span>
      </div>

      <div style={cardStyles.metaGrid}>
        {run.taskType && <Meta label="任务类型" value={taskLabels[run.taskType] ?? run.taskType} />}
        {solver && <Meta label="求解器" value={`${solverLabels[solver] ?? solver}${sourceSuffix("solver")}`} />}
        {damper && <Meta label="阻尼器" value={damperLabels[damper] ?? damper} />}
        {loadKind && <Meta label="荷载类型" value={`${loadKind}${sourceSuffix("loadKind")}`} />}
        {layout && <Meta label="阻尼器布置" value={`${layout}${sourceSuffix("selectedLayoutId")}`} />}
        {objectives.length > 0 && <Meta label="关注响应" value={`${objectives.join("、")}${sourceSuffix("responseIds")}`} />}
        {budgetParts.length > 0 && <Meta label="计算预算" value={`${budgetParts.join("，")}${sourceSuffix("budget")}`} />}
      </div>

      {Array.isArray(run.plan) && run.plan.length > 0 && (
        <ol style={cardStyles.list}>
          {run.plan.map(step => <li key={step}>{step}</li>)}
        </ol>
      )}

      {run.preflight && (
        <p style={cardStyles.note}>
          环境预检：
          <strong style={{ color: run.preflight.passed ? "var(--success-color)" : "var(--error-color)" }}>
            {run.preflight.passed ? " 通过" : " 未通过"}
          </strong>
        </p>
      )}
    </div>
  );
};

const Meta = ({ label, value }: { label: string; value: string }) => (
  <div>
    <span style={cardStyles.metaLabel}>{label}</span>
    <div style={cardStyles.metaValue}>{value}</div>
  </div>
);
