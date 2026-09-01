import { XCircle } from "lucide-react";
import type { AgentRun } from "../../../api/agentApi";
import { cardStyles } from "./cardStyles";

/** 求解阶段标签，与后端 currentStage 对应。 */
export const stageLabels: Record<string, string> = {
  LOAD_IMPORT: "上传荷载文件",
  LOAD_MAPPING: "确认字段映射",
  LOAD_STANDARDIZATION: "荷载标准化审批",
  SOLVER_APPROVAL: "求解审批",
  SOLVER_EXECUTION: "求解计算",
  REPORT_GENERATION: "生成证据报告",
  CLARIFICATION: "等待信息补充",
  PLANNING: "生成工程计划",
  PREFLIGHT: "环境预检",
  WAITING_APPROVAL: "等待审批",
  WAITING_JOB: "求解计算中",
  REVIEWING: "确定性复核",
  SUCCEEDED: "已完成",
  COMPLETED: "已完成"
};

const orderedStages = ["PLANNING", "PREFLIGHT", "WAITING_APPROVAL", "WAITING_JOB", "REVIEWING"];

type JobProgress = NonNullable<AgentRun["jobProgress"]>;

/**
 * 求解进度。粗粒度的 phase / message / percent 由 Job 状态机始终提供，
 * 所有任务类型（含单次分析）都能看到；批量完成计数与 per-case 时程进度
 * 只有支持 case_step_progress 的 solver 才有，作为可选增强叠加在上面。
 */
const JobProgressSection = ({ progress, running }: { progress: JobProgress; running: boolean }) => {
  const activeCases = progress.activeCases ?? [];
  const hasBatchCount = progress.completedCases != null && progress.totalCases != null;
  const duplicateBatchCount = hasBatchCount && new RegExp(
    `已完成\\s*${progress.completedCases}\\s*/\\s*${progress.totalCases}\\s*个算例`
  ).test(progress.message ?? "");
  const displayedPercent = progress.percent != null && (running || activeCases.length > 0)
    ? Math.min(progress.percent, 99)
    : progress.percent;

  return (
    <div style={{ marginTop: 12, fontSize: 13, color: "var(--text-secondary)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{ fontWeight: 600, color: "var(--text-primary)" }}>{progress.phase}</span>
        {displayedPercent != null && (
          <span style={{ fontWeight: 600, color: "var(--primary-color)" }}>{displayedPercent}%</span>
        )}
      </div>
      {displayedPercent != null && (
        <div
          style={{
            marginTop: 6,
            height: 4,
            backgroundColor: "var(--bg-active)",
            borderRadius: 2,
            overflow: "hidden"
          }}
        >
          <div
            style={{
              width: `${Math.min(Math.max(displayedPercent, 0), 100)}%`,
              height: "100%",
              backgroundColor: "var(--primary-color)",
              transition: "width 0.3s ease"
            }}
          />
        </div>
      )}
      {progress.message && !duplicateBatchCount && <div style={{ marginTop: 6 }}>{progress.message}</div>}

      {hasBatchCount && (
        <div style={{ marginTop: 6 }}>
          已完成 {progress.completedCases} / {progress.totalCases} 个算例
        </div>
      )}

      {activeCases.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 4, marginTop: 6 }}>
          {activeCases.slice(0, 3).map((caseProgress) => (
            <div key={caseProgress.caseId} style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <div
                style={{
                  flexShrink: 0,
                  width: 80,
                  height: 4,
                  backgroundColor: "var(--bg-active)",
                  borderRadius: 2,
                  overflow: "hidden"
                }}
              >
                <div
                  style={{
                    width: `${caseProgress.percent}%`,
                    height: "100%",
                    backgroundColor: "var(--primary-color)",
                    transition: "width 0.3s ease"
                  }}
                />
              </div>
              <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {caseProgress.caseId} {caseProgress.percent}%
              </span>
            </div>
          ))}
          {activeCases.length > 3 && (
            <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
              还有 {activeCases.length - 3} 个算例正在求解…
            </div>
          )}
        </div>
      )}
    </div>
  );
};

type ProgressCardProps = {
  run: AgentRun;
  busy: boolean;
  onCancel: () => void;
};

/**
 * 长任务进度卡。后端为轮询模式（无 SSE），因此这里展示阶段进度
 * 而不是逐字流式输出；真实优化可能耗时 60~90 分钟。
 */
export const ProgressCard = ({ run, busy, onCancel }: ProgressCardProps) => {
  const workflowSteps = run.workflowSnapshot?.steps.filter(
    (step) => !run.workflowSnapshot?.terminalSteps.includes(step.stepId)
  );
  const workflowStages = workflowSteps?.length
    ? workflowSteps.map((step) => step.stepId)
    : orderedStages;
  const combineSolverStages = workflowStages.includes("BASELINE") && workflowStages.includes("DOE");
  const visibleStages = combineSolverStages
    ? workflowStages.reduce<string[]>((stages, stage) => {
        if (stage === "BASELINE") stages.push("SOLVER_EXECUTION");
        else if (stage !== "DOE") stages.push(stage);
        return stages;
      }, [])
    : workflowStages;
  const completedWorkflowStages = new Set(
    (run.completedSteps ?? []).filter((stage) => workflowStages.includes(stage))
  );
  const hasExplicitCompletedStages = Array.isArray(run.completedSteps);
  const stageFallbacks: Record<string, string> = {
    WAITING_JOB: workflowStages.includes("BASELINE") ? "BASELINE" : "EXECUTION",
    REVIEWING: workflowStages.includes("REVIEW") ? "REVIEW" : "EVIDENCE_REVIEW",
    SUCCEEDED: "COMPLETED",
    COMPLETED: "COMPLETED"
  };
  // baseline-first 优化把“基线 + DOE”统一为一个真实求解阶段，避免用户看到两个
  // 相互重叠的阶段标题；Job 进度仍保留底层算例明细。
  const requestedStage = run.currentStep ?? run.currentStage;
  const fallbackStage = stageFallbacks[run.currentStage];
  const hasStartedDoe = run.jobProgress?.totalCases != null
    && workflowStages.includes("DOE")
    && (requestedStage === "BASELINE" || fallbackStage === "BASELINE");
  const effectiveRequestedStage = hasStartedDoe ? "DOE" : requestedStage;
  if (hasStartedDoe) completedWorkflowStages.add("BASELINE");
  const activeWorkflowStage = workflowStages.includes(effectiveRequestedStage)
    ? effectiveRequestedStage
    : fallbackStage && workflowStages.includes(fallbackStage)
      ? fallbackStage
      : workflowStages.find((stage) => !completedWorkflowStages.has(stage)) ?? workflowStages[workflowStages.length - 1];
  const activeStage = combineSolverStages && ["BASELINE", "DOE"].includes(activeWorkflowStage)
    ? "SOLVER_EXECUTION"
    : activeWorkflowStage;
  const completedStages = new Set(
    visibleStages.filter((stage) => (
      stage === "SOLVER_EXECUTION"
        ? completedWorkflowStages.has("DOE")
        : completedWorkflowStages.has(stage)
    ))
  );
  const activeIndex = Math.max(0, visibleStages.indexOf(activeStage));
  const cancellable =
    run.status === "WAITING_APPROVAL" || run.status === "WAITING_JOB" || run.status === "REVIEWING";
  const titleByStep = Object.fromEntries(
    workflowSteps?.map((step) => [step.stepId, step.title]) ?? []
  );

  return (
    <div style={cardStyles.card}>
      <div style={cardStyles.header}>
        <span style={cardStyles.title}>
          <span className="pulse" style={cardStyles.dot} aria-hidden="true" />
          {titleByStep[activeStage] ?? stageLabels[run.currentStage] ?? activeStage}
        </span>
        <span style={cardStyles.badge}>{run.status}</span>
      </div>

      <ol style={cardStyles.stageList}>
        {visibleStages.map((stage, index) => {
          const done = completedStages.has(stage) || (!hasExplicitCompletedStages && activeIndex > index);
          const active = activeIndex === index;
          return (
            <li
              key={stage}
              style={{
                ...cardStyles.stageItem,
                color: active
                  ? "var(--primary-color)"
                  : done
                    ? "var(--success-color)"
                    : "var(--text-muted)"
              }}
            >
              {done ? "✓" : active ? "▸" : "·"} {titleByStep[stage] ?? stageLabels[stage] ?? stage}
            </li>
          );
        })}
      </ol>

      {run.jobProgress && <JobProgressSection progress={run.jobProgress} running={run.status === "WAITING_JOB"} />}
      {run.jobProgressRefreshing && (
        <div role="status" style={{ ...cardStyles.note, color: "var(--text-muted)" }}>
          正在刷新最新求解进度…
        </div>
      )}

      {run.toolCalls && run.toolCalls.length > 0 && (
        <div style={cardStyles.note} aria-label="工具调用轨迹">
          最近工具：{run.toolCalls.slice(-3).map((call) => `${call.toolName} · ${call.status}`).join("；")}
        </div>
      )}

      <p style={cardStyles.note}>
        {run.runtimeMode ? `${run.runtimeMode} · ` : ""}Run {run.runId}
        {run.jobId ? ` · Job ${run.jobId}` : ""}
      </p>

      {cancellable && (
        <button className="btn btn-secondary" disabled={busy} onClick={onCancel}>
          <XCircle size={14} /> 取消运行
        </button>
      )}
    </div>
  );
};
