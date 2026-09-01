import React, { useMemo, useState } from "react";
import { api } from "../../api/client";
import { useCapabilityAvailability } from "../../hooks/useCapabilityAvailability";
import { useJobStore } from "../../stores/jobStore";
import { useEngineeringConfigStore } from "../../stores/engineeringConfigStore";
import { StatusBadge } from "../../components/feedback/StatusBadge";
import { ProgressBar } from "../../components/feedback/ProgressBar";
import { ErrorPanel } from "../../components/feedback/ErrorPanel";
import { ArtifactPreview } from "../../components/artifact/ArtifactPreview";
import type {
  Artifact,
  OptimizationConstraint,
  OptimizationConstraintSource,
  OptimizationExportFormat,
  OptimizationExportKind,
  OptimizationObjective,
  OptimizationObjectiveMode,
  ResponseTargetId
} from "../../api/types";
import {
  OPTIMIZATION_EXPORT_FORMAT_OPTIONS,
  OPTIMIZATION_EXPORT_KIND_OPTIONS,
  OPTIMIZATION_OBJECTIVE_MODE_OPTIONS,
  RESPONSE_TARGETS,
  SURROGATE_RUN_OPTIONS,
  getOptimizationTargetsByMode
} from "../../api/engineeringOptions";
import {
  ScatterChart,
  Scatter,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Cell
} from "recharts";
import { Sliders, Award, Star, Activity, AlertTriangle, Download, Eye } from "lucide-react";
import { CHART_AXIS, CHART_GRID, CHART_HIGHLIGHT_STROKE } from "../../theme/chart";
import { useManagedIntervals } from "../../hooks/useManagedIntervals";
import { errorMessage } from "../../utils/errors";

interface Candidate {
  id: string;
  params: { alpha: number; beta: number };
  objectives: {
    beamEndDisplacement: number;
    towerBaseShear: number;
    towerBaseMoment: number;
    beamEndCumulativeDisplacement: number;
    damperCost: number;
  };
  constraintsPassed: boolean;
  femReviewed: boolean;
  topsisScore: number;
  rank: number;
}

const UNCONTROLLED_LIMITS: Record<ResponseTargetId, number> = {
  beamEndDisplacement: 0.12,
  towerBaseShear: 68000,
  towerBaseMoment: 3200000,
  beamEndCumulativeDisplacement: 28,
  damperCost: 1200000,
  damperStroke: 0.5,
  damperForce: 1800,
  midspanDisplacement: 0.18,
  midspanAcceleration: 2.5
};

const createObjectiveConstraints = (
  objectives: OptimizationObjective[],
  previous: OptimizationConstraint[] = []
): OptimizationConstraint[] => objectives.map(objective => {
  const target = RESPONSE_TARGETS.find(item => item.id === objective.name);
  const existing = previous.find(item => item.targetId === objective.name);
  return existing || {
    targetId: objective.name,
    name: `${target?.label ?? objective.name}不超过无控状态`,
    operator: "<=",
    value: UNCONTROLLED_LIMITS[objective.name],
    unit: target?.unit ?? "-",
    source: "UNCONTROLLED"
  };
});

export const OptimizationPage: React.FC = () => {
  const optimizationCapability = useCapabilityAvailability("MULTI_OBJECTIVE_OPTIMIZATION");
  const decisionCapability = useCapabilityAvailability("ENTROPY_TOPSIS_DECISION");
  const exportCapability = useCapabilityAvailability("OPTIMIZATION_EXPORT");
  const { submitJob, activeJobs, error: jobError, clearError, reportError } = useJobStore();
  const { setManagedInterval, clearManagedInterval } = useManagedIntervals();
  const {
    optimizationDecisionConfig,
    updateOptimizationDecisionConfig,
    validateModule
  } = useEngineeringConfigStore();
  const [currentJobId, setCurrentJobId] = useState<string | null>(null);
  const [topsisJobId, setTopsisJobId] = useState<string | null>(null);
  const [exportJobId, setExportJobId] = useState<string | null>(null);

  // Form states - Optimization
  const [optForm, setOptForm] = useState<{
    surrogateRunId: string;
    objectiveMode: OptimizationObjectiveMode;
    objectives: OptimizationObjective[];
    constraints: OptimizationConstraint[];
  }>(() => {
    const defaultObjectives = optimizationDecisionConfig.objectives.length > 0
      ? optimizationDecisionConfig.objectives
      : getOptimizationTargetsByMode("OVERALL").map(name => ({ name, direction: "MIN" as const }));
    return {
    surrogateRunId: "sur_20260701_0001",
    objectiveMode: optimizationDecisionConfig.objectiveMode,
    objectives: defaultObjectives,
    constraints: createObjectiveConstraints(defaultObjectives, optimizationDecisionConfig.constraints)
  };
  });

  // Form states - Topsis
  const [topsisForm, setTopsisForm] = useState({
    optimizationRunId: "opt_20260701_0001",
    requiresAcceptedFemReview: true
  });

  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [entropyWeights, setEntropyWeights] = useState<Record<string, number>>({});
  const [recommendation, setRecommendation] = useState<{ candidateId: string; explanation: string } | null>(null);
  const [decisionArtifact, setDecisionArtifact] = useState<Artifact | null>(null);
  const [exportArtifacts, setExportArtifacts] = useState<Artifact[]>([]);
  const [selectedArtifact, setSelectedArtifact] = useState<Artifact | null>(null);
  const [selectedCandidateId, setSelectedCandidateId] = useState<string | null>(null);
  const [exportForm, setExportForm] = useState<{
    optimizationRunId: string;
    exportKinds: OptimizationExportKind[];
    formats: OptimizationExportFormat[];
  }>({
    optimizationRunId: "opt_20260701_0001",
    exportKinds: ["PARETO_FRONT", "ENTROPY_WEIGHTS", "TOPSIS_RECOMMENDATION"],
    formats: ["CSV", "JSON", "PNG", "SVG"]
  });

  const optimizationTargets = RESPONSE_TARGETS.filter(target => target.category !== "DIAGNOSTIC");

  const handleObjectiveModeChange = (mode: OptimizationObjectiveMode) => {
    const objectives = getOptimizationTargetsByMode(mode).map(name => ({ name, direction: "MIN" as const }));
    setOptForm(prev => ({
      ...prev,
      objectiveMode: mode,
      objectives,
      constraints: createObjectiveConstraints(objectives, prev.constraints)
    }));
  };

  const handleObjectiveToggle = (targetId: ResponseTargetId, checked: boolean) => {
    setOptForm(prev => {
      const objectives = checked
        ? [...prev.objectives, { name: targetId, direction: "MIN" as const }]
        : prev.objectives.filter(objective => objective.name !== targetId);
      return {
        ...prev,
        objectives,
        constraints: createObjectiveConstraints(objectives, prev.constraints)
      };
    });
  };

  const handleObjectiveDirectionChange = (targetId: ResponseTargetId, direction: "MIN" | "MAX") => {
    setOptForm(prev => ({
      ...prev,
      objectives: prev.objectives.map(objective => objective.name === targetId ? { ...objective, direction } : objective)
    }));
  };

  const updateConstraint = (targetId: ResponseTargetId, patch: Partial<OptimizationConstraint>) => {
    setOptForm(prev => ({
      ...prev,
      constraints: prev.constraints.map(item => {
        if (item.targetId !== targetId) return item;
        const target = RESPONSE_TARGETS.find(option => option.id === targetId);
        const source = patch.source ?? item.source;
        return {
          ...item,
          ...patch,
          name: source === "UNCONTROLLED"
            ? `${target?.label ?? targetId}不超过无控状态`
            : `${target?.label ?? targetId}不超过用户阈值`,
          value: source === "UNCONTROLLED" ? UNCONTROLLED_LIMITS[targetId] : patch.value ?? item.value,
          source
        };
      })
    }));
  };

  const handleOptSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    clearError();
    setCandidates([]);
    setEntropyWeights({});
    setRecommendation(null);
    setDecisionArtifact(null);
    setExportArtifacts([]);
    setSelectedCandidateId(null);
    updateOptimizationDecisionConfig({
      objectiveMode: optForm.objectiveMode,
      objectives: optForm.objectives,
      constraints: optForm.constraints,
      validated: false
    });
    validateModule("OPTIMIZATION_DECISION");

    try {
      const job = await submitJob("MULTI_OBJECTIVE_OPTIMIZATION", optForm);
      setCurrentJobId(job.jobId);
      setTopsisForm(prev => ({ ...prev, optimizationRunId: job.jobId }));
      setExportForm(prev => ({ ...prev, optimizationRunId: job.jobId }));

      const check = setManagedInterval(async () => {
        try {
          const updated = await api.getJob(job.jobId);
          if (updated.status === "SUCCEEDED") {
            clearManagedInterval(check);
            const results = await api.getTopsisResult(job.jobId);
            setCandidates(results.candidates);
            setEntropyWeights(results.entropyWeights);
            setRecommendation(results.recommendation);
            setSelectedCandidateId(results.recommendation?.candidateId || results.candidates[0]?.id || null);
          } else if (updated.status === "FAILED" || updated.status === "CANCELLED") {
            clearManagedInterval(check);
          }
        } catch (error) {
          clearManagedInterval(check);
          reportError(errorMessage(error, "读取优化任务状态失败"));
        }
      }, 1500);
    } catch (error) {
      reportError(errorMessage(error, "启动多目标优化失败"));
    }
  };

  const handleExportKindChange = (kind: OptimizationExportKind, checked: boolean) => {
    setExportForm(prev => ({
      ...prev,
      exportKinds: checked ? [...prev.exportKinds, kind] : prev.exportKinds.filter(item => item !== kind)
    }));
  };

  const handleExportFormatChange = (format: OptimizationExportFormat, checked: boolean) => {
    setExportForm(prev => ({
      ...prev,
      formats: checked ? [...prev.formats, format] : prev.formats.filter(item => item !== format)
    }));
  };

  const handleOptimizationExport = async (e: React.FormEvent) => {
    e.preventDefault();
    clearError();
    setExportArtifacts([]);
    try {
      const job = await submitJob("OPTIMIZATION_EXPORT", {
        ...exportForm,
        optimizationRunId: currentJobId || exportForm.optimizationRunId
      });
      setExportJobId(job.jobId);

      const check = setManagedInterval(async () => {
        try {
          const updated = await api.getJob(job.jobId);
          if (updated.status === "SUCCEEDED") {
            clearManagedInterval(check);
            const arts = await api.getArtifacts({ jobId: job.jobId });
            setExportArtifacts(arts.data);
          } else if (updated.status === "FAILED" || updated.status === "CANCELLED") {
            clearManagedInterval(check);
          }
        } catch (error) {
          clearManagedInterval(check);
          reportError(errorMessage(error, "读取优化导出任务状态失败"));
        }
      }, 1500);
    } catch (error) {
      reportError(errorMessage(error, "启动优化结果导出失败"));
    }
  };

  const handleTopsisSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    clearError();
    try {
      const job = await submitJob("ENTROPY_TOPSIS_DECISION", {
        optimizationRunId: topsisForm.optimizationRunId,
        candidateFilter: {
          requiresAcceptedFemReview: topsisForm.requiresAcceptedFemReview
        }
      });
      setTopsisJobId(job.jobId);

      const check = setManagedInterval(async () => {
        try {
          const updated = await api.getJob(job.jobId);
          if (updated.status === "SUCCEEDED") {
            clearManagedInterval(check);
            const arts = await api.getArtifacts({ jobId: job.jobId });
            if (arts.data.length > 0) {
              setDecisionArtifact(arts.data[0]);
            }
          } else if (updated.status === "FAILED" || updated.status === "CANCELLED") {
            clearManagedInterval(check);
          }
        } catch (error) {
          clearManagedInterval(check);
          reportError(errorMessage(error, "读取 TOPSIS 任务状态失败"));
        }
      }, 1500);
    } catch (error) {
      reportError(errorMessage(error, "启动 TOPSIS 决策失败"));
    }
  };

  const activeJob = currentJobId ? activeJobs[currentJobId] : null;
  const isRunning = activeJob?.status === "RUNNING" || activeJob?.status === "QUEUED";

  const activeTopsisJob = topsisJobId ? activeJobs[topsisJobId] : null;
  const isTopsisRunning = activeTopsisJob?.status === "RUNNING" || activeTopsisJob?.status === "QUEUED";
  const activeExportJob = exportJobId ? activeJobs[exportJobId] : null;
  const isExportRunning = activeExportJob?.status === "RUNNING" || activeExportJob?.status === "QUEUED";

  // Prepare chart series from candidates
  const scatterData = candidates.map(c => ({
    x: c.objectives.beamEndCumulativeDisplacement,
    y: c.objectives.damperCost / 10000,
    id: c.id,
    rank: c.rank,
    topsisScore: c.topsisScore,
    isRecommended: recommendation?.candidateId === c.id,
    femReviewed: c.femReviewed,
    isSelected: selectedCandidateId === c.id
  }));
  const selectedCandidate = useMemo(
    () => candidates.find(candidate => candidate.id === selectedCandidateId) || null,
    [candidates, selectedCandidateId]
  );

  return (
    <div className="page-container">
      {/* Page Header */}
      <div style={styles.header}>
        <div>
          <h1 style={styles.title}>多目标优化与熵权 TOPSIS 决策</h1>
          <p style={styles.subtitle}>
            定义分析约束与目标函数，基于 Pareto 前沿以熵权 TOPSIS 算法求解最佳减震设计折中方案
          </p>
        </div>
      </div>

      <div style={styles.mainGrid}>
        {/* Left Column: Form & Chart */}
        <div style={{ flex: 3, display: "flex", flexDirection: "column", gap: 16 }}>
          {/* Form */}
          <div className="panel">
            <form onSubmit={handleOptSubmit}>
              <div className="panel-header">
                <span style={styles.panelTitle}>
                  <Sliders size={16} color="var(--primary-color)" />
                  <span>多目标 Pareto 优化配置</span>
                </span>
              </div>

              {jobError && <ErrorPanel message={jobError} />}

              <div style={styles.formRow}>
                <div className="form-group" style={{ flex: 2 }}>
                  <label className="form-label">拟合模型批次 (Surrogate Run ID)</label>
                  <select
                    className="form-control"
                    value={optForm.surrogateRunId}
                    onChange={e => setOptForm({ ...optForm, surrogateRunId: e.target.value })}
                    required
                  >
                    {SURROGATE_RUN_OPTIONS.map(option => (
                      <option key={option.id} value={option.id}>{option.label}</option>
                    ))}
                  </select>
                </div>
                <div className="form-group" style={{ flex: 1 }}>
                  <label className="form-label">优化目标模式</label>
                  <select
                    className="form-control"
                    value={optForm.objectiveMode}
                    onChange={e => handleObjectiveModeChange(e.target.value as OptimizationObjectiveMode)}
                  >
                    {OPTIMIZATION_OBJECTIVE_MODE_OPTIONS.map(option => (
                      <option key={option.id} value={option.id}>{option.label}</option>
                    ))}
                  </select>
                </div>
              </div>

              {/* Targets */}
              <div className="form-group">
                <label className="form-label">优化目标函数选择与寻优方向</label>
                <div style={styles.objectiveGrid}>
                  {(optForm.objectiveMode === "CUSTOM" ? optimizationTargets : optForm.objectives.map(objective => RESPONSE_TARGETS.find(target => target.id === objective.name)).filter(Boolean)).map(target => {
                    const current = optForm.objectives.find(objective => objective.name === target!.id);
                    const checked = Boolean(current);
                    return (
                      <div key={target!.id} style={styles.objectiveItem}>
                        <label style={styles.objectiveLabel}>
                          <input
                            type="checkbox"
                            checked={checked}
                            disabled={optForm.objectiveMode !== "CUSTOM"}
                            onChange={e => handleObjectiveToggle(target!.id, e.target.checked)}
                          />
                          <span>{target!.label} ({target!.id})</span>
                          <code style={styles.code}>{target!.unit}</code>
                        </label>
                        <select
                          className="form-control"
                          style={{ width: "120px" }}
                          value={current?.direction || "MIN"}
                          disabled={!checked || optForm.objectiveMode !== "CUSTOM"}
                          onChange={e => handleObjectiveDirectionChange(target!.id, e.target.value as "MIN" | "MAX")}
                        >
                          <option value="MIN">极小化</option>
                          <option value="MAX">极大化</option>
                        </select>
                      </div>
                    );
                  })}
                </div>
                <div style={styles.modeHint}>
                  {OPTIMIZATION_OBJECTIVE_MODE_OPTIONS.find(option => option.id === optForm.objectiveMode)?.note}
                </div>
              </div>

              {/* Constraints */}
              <div className="form-group" style={{ marginTop: 12 }}>
                <label className="form-label">优化约束条件</label>
                <div style={styles.constraintList}>
                  {optForm.constraints.map(constraint => {
                    const target = RESPONSE_TARGETS.find(item => item.id === constraint.targetId);
                    return (
                      <div key={constraint.targetId} style={styles.constraintItem}>
                        <div style={styles.constraintTarget}>
                          <span style={{ fontWeight: 600 }}>{target?.label ?? constraint.targetId}</span>
                          <span style={{ color: "var(--text-muted)", fontSize: 11 }}>优化目标值不得超过约束值</span>
                        </div>
                        <select
                          className="form-control"
                          style={{ width: 160 }}
                          value={constraint.source}
                          onChange={e => updateConstraint(constraint.targetId, { source: e.target.value as OptimizationConstraintSource })}
                        >
                          <option value="UNCONTROLLED">无控状态值</option>
                          <option value="CUSTOM">用户阈值</option>
                        </select>
                        <span style={{ color: "var(--text-secondary)" }}>{"<="}</span>
                        <input
                          type="number"
                          step="0.01"
                          className="form-control"
                          style={{ width: 110, padding: "4px 8px" }}
                          value={constraint.value}
                          disabled={constraint.source === "UNCONTROLLED"}
                          onChange={e => updateConstraint(constraint.targetId, { value: parseFloat(e.target.value) || 0 })}
                        />
                        <span style={{ color: "var(--text-muted)", width: 48 }}>{constraint.unit}</span>
                      </div>
                    );
                  })}
                </div>
                <div style={styles.modeHint}>默认采用无阻尼器状态对应响应值作为上限，也可切换为用户指定阈值。</div>
              </div>

              <button type="submit" disabled={isRunning || optForm.objectives.length === 0 || !optimizationCapability.available} className="btn btn-primary" style={{ marginTop: 12 }}>
                <span>启动多目标非支配 Pareto 寻优</span>
              </button>
            </form>
          </div>

          <div className="panel">
            <div className="panel-header">
              <span style={styles.panelTitle}>
                <Activity size={16} color="var(--primary-color)" />
                <span>Pareto 多维响应散点交互图 (梁端累计位移 vs 阻尼器造价)</span>
              </span>
            </div>

            <div style={styles.chartWrapper}>
              {candidates.length > 0 ? (
                <ResponsiveContainer width="100%" height={260}>
                  <ScatterChart margin={{ top: 10, right: 30, bottom: 10, left: 10 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke={CHART_GRID} />
                    <XAxis type="number" dataKey="x" name="梁端累计位移" unit="m" stroke={CHART_AXIS} domain={["auto", "auto"]} />
                    <YAxis type="number" dataKey="y" name="阻尼器造价" unit="万元" stroke={CHART_AXIS} domain={["auto", "auto"]} />
                    <Tooltip
                      cursor={{ strokeDasharray: "3 3" }}
                      content={({ active, payload }) => {
                        if (active && payload && payload.length) {
                          const item = payload[0].payload;
                          return (
                            <div style={styles.chartTooltip}>
                              <div style={{ fontWeight: 600, color: "var(--text-primary)" }}>算例: {item.id}</div>
                              <div style={{ fontSize: "11px", color: "var(--text-secondary)", marginTop: 4 }}>
                                <div>梁端累计位移: {item.x.toFixed(3)} m</div>
                                <div>阻尼器造价: {item.y.toFixed(1)} 万元</div>
                                <div>TOPSIS得分: {item.topsisScore.toFixed(3)}</div>
                                <div>综合排名: #{item.rank}</div>
                                {item.isRecommended && <div style={{ color: "var(--warning-color)", fontWeight: 600 }}>★ TOPSIS 推荐方案</div>}
                              </div>
                            </div>
                          );
                        }
                        return null;
                      }}
                    />
                    <Scatter name="Pareto Candidates" data={scatterData} onClick={(data: any) => setSelectedCandidateId(data?.id || null)}>
                      {scatterData.map((entry, index) => {
                        let fill = "var(--text-muted)";
                        if (entry.isRecommended) fill = "var(--warning-color)";
                        else if (entry.isSelected) fill = "var(--success-color)";
                        else if (entry.femReviewed) fill = "var(--primary-color)";
                        
                        return (
                          <Cell
                            key={`cell-${index}`}
                            fill={fill}
                            r={entry.isRecommended || entry.isSelected ? 8 : 5}
                            stroke={entry.isRecommended || entry.isSelected ? CHART_HIGHLIGHT_STROKE : "none"}
                            strokeWidth={2}
                            style={{ cursor: "pointer" }}
                          />
                        );
                      })}
                    </Scatter>
                  </ScatterChart>
                </ResponsiveContainer>
              ) : (
                <div style={styles.emptyChart}>
                  <div style={styles.emptyChartTitle}>等待优化结果</div>
                  <div style={styles.emptyChartText}>启动 Pareto 寻优后，这里将实时展示候选点、推荐点和 FEM 复核状态。</div>
                </div>
              )}

              <div style={styles.chartLegend}>
                <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  <div style={{ width: 10, height: 10, borderRadius: "50%", backgroundColor: "var(--warning-color)" }} />
                    <span>★ TOPSIS 最优推荐方案</span>
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  <div style={{ width: 10, height: 10, borderRadius: "50%", backgroundColor: "var(--success-color)" }} />
                  <span>当前选中方案</span>
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  <div style={{ width: 10, height: 10, borderRadius: "50%", backgroundColor: "var(--primary-color)" }} />
                  <span>已通过实体有限元 (FEM) 验算</span>
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  <div style={{ width: 10, height: 10, borderRadius: "50%", backgroundColor: "var(--text-muted)" }} />
                  <span>代理拟合点 (待计算复核)</span>
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* Right Column: Decisions & Candidates Table */}
        <div style={{ flex: 2, display: "flex", flexDirection: "column", gap: 16 }}>
          {/* Optimization Job Status */}
          {currentJobId && (
            <div className="panel">
              <div className="panel-header">
                <span style={{ fontWeight: 600 }}>Pareto 寻优进度</span>
                <StatusBadge status={activeJob?.status || "QUEUED"} />
              </div>

              {isRunning ? (
                <ProgressBar
                  percent={activeJob?.progress?.percent}
                  phase={activeJob?.progress?.phase}
                  message={activeJob?.progress?.message}
                />
              ) : (
                <div style={{ padding: "4px 0", fontSize: "12px", color: "var(--text-secondary)" }}>
                  {activeJob?.status === "SUCCEEDED" ? (
                    <span style={{ color: "var(--success-color)", fontWeight: 500 }}>✓ 多维响应求解结束</span>
                  ) : (
                    <span>优化任务结束</span>
                  )}
                </div>
              )}
            </div>
          )}

          <div className="panel">
            <div className="panel-header">
              <span style={{ fontWeight: 600 }}>候选方案实时详情</span>
            </div>
            {selectedCandidate ? (
              <div style={styles.detailBlock}>
                <div style={styles.detailHeader}>
                  <div>
                    <div style={styles.detailTitle}>
                      方案 {selectedCandidate.id}
                      {recommendation?.candidateId === selectedCandidate.id && <span style={styles.bestTag}>TOPSIS 推荐</span>}
                    </div>
                    <div style={styles.detailSub}>排名 #{selectedCandidate.rank} / TOPSIS {selectedCandidate.topsisScore.toFixed(3)}</div>
                  </div>
                  <span style={{
                    ...styles.reviewTag,
                    color: selectedCandidate.femReviewed ? "var(--success-color)" : "var(--text-muted)"
                  }}>
                    {selectedCandidate.femReviewed ? "FEM 已复核" : "待 FEM 复核"}
                  </span>
                </div>
                <div style={styles.detailGrid}>
                  <Metric label="梁端位移" value={`${selectedCandidate.objectives.beamEndDisplacement.toFixed(4)} m`} />
                  <Metric label="塔底剪力" value={`${(selectedCandidate.objectives.towerBaseShear / 1000).toFixed(1)} kN`} />
                  <Metric label="塔底弯矩" value={`${(selectedCandidate.objectives.towerBaseMoment / 1000).toFixed(1)} kN*m`} />
                  <Metric label="梁端累计位移" value={`${selectedCandidate.objectives.beamEndCumulativeDisplacement.toFixed(3)} m`} />
                  <Metric label="阻尼器造价" value={`${(selectedCandidate.objectives.damperCost / 10000).toFixed(1)} 万`} />
                  <Metric label="约束状态" value={selectedCandidate.constraintsPassed ? "通过" : "未通过"} />
                </div>
              </div>
            ) : (
              <div style={styles.emptySide}>点击 Pareto 点或候选方案行后查看目标值、排名和复核状态。</div>
            )}
          </div>

          {candidates.length > 0 && (
            <div className="panel">
              <form onSubmit={handleOptimizationExport}>
                <div className="panel-header">
                  <span style={styles.panelTitle}>
                    <Download size={16} color="var(--primary-color)" />
                    <span>优化结果导出</span>
                  </span>
                </div>

                <div style={styles.exportGrid}>
                  {OPTIMIZATION_EXPORT_KIND_OPTIONS.map(option => (
                    <label key={option.id} style={styles.exportItem}>
                      <input type="checkbox" checked={exportForm.exportKinds.includes(option.id)} onChange={e => handleExportKindChange(option.id, e.target.checked)} />
                      <span>{option.label}</span>
                    </label>
                  ))}
                </div>
                <div style={styles.exportGrid}>
                  {OPTIMIZATION_EXPORT_FORMAT_OPTIONS.map(option => (
                    <label key={option.id} style={styles.exportItem}>
                      <input type="checkbox" checked={exportForm.formats.includes(option.id)} onChange={e => handleExportFormatChange(option.id, e.target.checked)} />
                      <span>{option.label}</span>
                    </label>
                  ))}
                </div>

                <button
                  type="submit"
                  disabled={isExportRunning || exportForm.exportKinds.length === 0 || exportForm.formats.length === 0 || !exportCapability.available}
                  className="btn btn-secondary"
                  style={{ width: "100%", justifyContent: "center" }}
                >
                  <Download size={14} />
                  <span>{isExportRunning ? "导出中..." : "导出 Pareto / 熵权 / TOPSIS"}</span>
                </button>
              </form>

              {exportArtifacts.length > 0 && (
                <div style={styles.exportArtifactList}>
                  {exportArtifacts.map(artifact => (
                    <div key={artifact.artifactId} style={styles.artCard}>
                      <div>
                        <div style={styles.artName}>{artifact.name}</div>
                        <div style={{ fontSize: "11px", color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
                          {artifact.path}
                        </div>
                      </div>
                      {artifact.canPreview && (
                        <button
                          onClick={() => setSelectedArtifact(artifact)}
                          className="btn btn-secondary"
                          style={{ padding: "4px 8px", fontSize: "11px" }}
                        >
                          <Eye size={12} />
                        </button>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* TOPSIS Decision Panel */}
          {candidates.length > 0 && (
            <div className="panel">
              <div className="panel-header">
                <span style={styles.panelTitle}>
                  <Award size={16} color="var(--primary-color)" />
                  <span>熵权 TOPSIS 多指标推荐决策</span>
                </span>
              </div>

              {/* Entropy weights values */}
              {Object.keys(entropyWeights).length > 0 && (
                <div style={styles.weightsBlock}>
                  <div style={styles.weightsTitle}>自适应物理目标权重分配 (熵权计算结果)</div>
                  <div style={styles.weightsGrid}>
                    <div style={styles.weightItem}>
                      <span>梁端位移:</span> <strong style={{ color: "var(--primary-color)" }}>{(entropyWeights.beamEndDisplacement * 100).toFixed(0)}%</strong>
                    </div>
                    <div style={styles.weightItem}>
                      <span>塔底内力:</span> <strong style={{ color: "var(--primary-color)" }}>{((entropyWeights.towerBaseShear + entropyWeights.towerBaseMoment) * 100).toFixed(0)}%</strong>
                    </div>
                    <div style={styles.weightItem}>
                      <span>运营与造价:</span> <strong style={{ color: "var(--primary-color)" }}>{((entropyWeights.beamEndCumulativeDisplacement + entropyWeights.damperCost) * 100).toFixed(0)}%</strong>
                    </div>
                  </div>
                </div>
              )}

              {/* Recommended scheme box */}
              {recommendation && (
                <div style={styles.recommendBox}>
                  <div style={styles.recommendHeader}>
                    <Star size={16} color="var(--warning-color)" />
                    <span style={{ fontWeight: 700, color: "var(--warning-color)" }}>
                      决策最佳推荐方案: 方案 {recommendation.candidateId}
                    </span>
                  </div>

                  <div style={{ marginTop: 8, fontSize: "12px", color: "var(--text-primary)", lineHeight: 1.5 }}>
                    {recommendation.explanation}
                  </div>
                </div>
              )}

              {/* Decision validation notice */}
              <div style={styles.decisionWarning}>
                <AlertTriangle size={16} color="var(--warning-color)" style={{ flexShrink: 0 }} />
                <div>
                  <div style={{ fontWeight: 600, color: "var(--text-primary)", fontSize: "11px" }}>
                    决策合理性安全警告 (Decision Safety Gate)
                  </div>
                  <div style={{ fontSize: "11px", color: "var(--text-secondary)", marginTop: 2 }}>
                    熵权 TOPSIS 算法推荐结果仅用于多方案多维度比选参考，<strong>不作为未经真实有限元 (FEM) 精细模型演算核对的最终工程决策结论</strong>。
                  </div>
                </div>
              </div>

              {/* Run Topsis job Form */}
              <form onSubmit={handleTopsisSubmit} style={{ marginTop: 12 }}>
                <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
                  <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: "11px" }}>
                    <input
                      type="checkbox"
                      checked={topsisForm.requiresAcceptedFemReview}
                      onChange={e => setTopsisForm({ ...topsisForm, requiresAcceptedFemReview: e.target.checked })}
                    />
                    <span>过滤掉未通过真实有限元(FEM)复核的算例</span>
                  </label>
                  
                  <button
                    type="submit"
                    disabled={isTopsisRunning || !decisionCapability.available}
                    className="btn btn-secondary"
                    style={{ padding: "4px 8px", fontSize: "11px" }}
                  >
                    {isTopsisRunning ? "计算中..." : "导出决策报告"}
                  </button>
                </div>
              </form>

              {decisionArtifact && (
                <div style={styles.artCard}>
                  <div>
                    <div style={styles.artName}>{decisionArtifact.name}</div>
                    <div style={{ fontSize: "11px", color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
                      {decisionArtifact.path}
                    </div>
                  </div>
                  <button
                    onClick={() => setSelectedArtifact(decisionArtifact)}
                    className="btn btn-secondary"
                    style={{ padding: "4px 8px", fontSize: "11px" }}
                  >
                    <span>查看决策报告</span>
                  </button>
                </div>
              )}
            </div>
          )}

          {/* Candidates table */}
          {candidates.length > 0 && (
            <div className="panel" style={{ flex: 1 }}>
              <div className="panel-header">
                <span style={{ fontWeight: 600 }}>Pareto 优化候选方案集清单</span>
              </div>
              
              <div style={{ overflowX: "auto" }}>
                <table className="data-table" style={{ fontSize: "12px" }}>
                  <thead>
                    <tr>
                      <th>方案 ID</th>
                      <th>阻尼参数 (α, β)</th>
                      <th>梁端位移 (cm)</th>
                      <th>塔底剪力 (kN)</th>
                      <th>塔底弯矩 (kN*m)</th>
                      <th>累计位移</th>
                      <th>阻尼器造价</th>
                      <th>验证</th>
                    </tr>
                  </thead>
                  <tbody>
                    {candidates.map(c => {
                      const isBest = recommendation?.candidateId === c.id;
                      return (
                        <tr
                          key={c.id}
                          onClick={() => setSelectedCandidateId(c.id)}
                          style={{
                            backgroundColor: selectedCandidateId === c.id
                              ? "var(--success-soft)"
                              : isBest
                                ? "var(--warning-soft)"
                                : "transparent",
                            cursor: "pointer"
                          }}
                        >
                          <td style={{ fontWeight: 600, display: "flex", alignItems: "center", gap: 4 }}>
                            {isBest && <Star size={12} color="var(--warning-color)" />}
                            <span>{c.id}</span>
                          </td>
                          <td style={{ fontFamily: "var(--font-mono)" }}>
                            ({c.params.alpha.toFixed(2)}, {c.params.beta.toFixed(2)})
                          </td>
                          <td>{(c.objectives.beamEndDisplacement * 100).toFixed(2)}</td>
                          <td>{(c.objectives.towerBaseShear / 1000).toFixed(1)}</td>
                          <td>{(c.objectives.towerBaseMoment / 1000).toFixed(1)}</td>
                          <td>{c.objectives.beamEndCumulativeDisplacement.toFixed(3)}</td>
                          <td>{(c.objectives.damperCost / 10000).toFixed(1)}万</td>
                          <td>
                            <span style={{
                              color: c.femReviewed ? "var(--success-color)" : "var(--text-muted)",
                              fontWeight: 500
                            }}>
                              {c.femReviewed ? "✓ FEM已校验" : "待校验"}
                            </span>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Artifact Preview Modal */}
      {selectedArtifact && (
        <ArtifactPreview
          artifact={selectedArtifact}
          onClose={() => setSelectedArtifact(null)}
        />
      )}
    </div>
  );
};

const Metric: React.FC<{ label: string; value: string }> = ({ label, value }) => (
  <div style={styles.metric}>
    <span style={styles.metricLabel}>{label}</span>
    <strong style={styles.metricValue}>{value}</strong>
  </div>
);

const styles: Record<string, React.CSSProperties> = {
  header: {
    borderBottom: "1px solid var(--border-color)",
    paddingBottom: "14px"
  },
  title: {
    fontSize: "18px",
    fontWeight: 700,
    color: "var(--text-primary)"
  },
  subtitle: {
    fontSize: "12px",
    color: "var(--text-secondary)",
    marginTop: "4px"
  },
  mainGrid: {
    display: "flex",
    gap: "16px"
  },
  panelTitle: {
    display: "flex",
    alignItems: "center",
    gap: 8,
    fontWeight: 600
  },
  formRow: {
    display: "flex",
    gap: "16px",
    marginBottom: "12px"
  },
  objectiveGrid: {
    display: "flex",
    flexDirection: "column",
    gap: 8,
    marginTop: "8px"
  },
  objectiveItem: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    backgroundColor: "var(--bg-primary)",
    border: "1px solid var(--border-color)",
    padding: "8px 12px",
    borderRadius: 4,
    fontSize: "12px"
  },
  objectiveLabel: {
    display: "flex",
    alignItems: "center",
    gap: 8,
    minWidth: 0
  },
  modeHint: {
    marginTop: 8,
    fontSize: "11px",
    color: "var(--text-muted)"
  },
  constraintList: {
    display: "flex",
    flexDirection: "column",
    gap: 8,
    marginTop: 8
  },
  constraintItem: {
    display: "grid",
    gridTemplateColumns: "minmax(180px, 1fr) 160px auto 110px 48px",
    alignItems: "center",
    gap: 8,
    backgroundColor: "var(--bg-primary)",
    border: "1px solid var(--border-color)",
    borderRadius: 4,
    padding: "8px 10px",
    fontSize: 12
  },
  constraintTarget: {
    display: "flex",
    flexDirection: "column",
    gap: 2
  },
  chartWrapper: {
    backgroundColor: "var(--bg-primary)",
    border: "1px solid var(--border-color)",
    borderRadius: 4,
    padding: "16px 12px 10px 10px"
  },
  emptyChart: {
    height: 260,
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    justifyContent: "center",
    border: "1px dashed var(--border-color)",
    borderRadius: 4,
    color: "var(--text-muted)"
  },
  emptyChartTitle: {
    fontSize: "13px",
    fontWeight: 700,
    color: "var(--text-secondary)",
    marginBottom: 6
  },
  emptyChartText: {
    fontSize: "12px"
  },
  chartTooltip: {
    backgroundColor: "var(--bg-secondary)",
    border: "1px solid var(--border-color)",
    padding: "8px 10px",
    borderRadius: 4
  },
  chartLegend: {
    display: "flex",
    justifyContent: "center",
    flexWrap: "wrap",
    gap: 16,
    fontSize: "11px",
    color: "var(--text-secondary)",
    marginTop: 8
  },
  detailBlock: {
    display: "flex",
    flexDirection: "column",
    gap: 10
  },
  detailHeader: {
    display: "flex",
    justifyContent: "space-between",
    gap: 12,
    alignItems: "flex-start"
  },
  detailTitle: {
    display: "flex",
    alignItems: "center",
    gap: 8,
    fontSize: "13px",
    fontWeight: 700,
    color: "var(--text-primary)"
  },
  bestTag: {
    color: "var(--warning-color)",
    border: "1px solid var(--warning-color)",
    borderRadius: 4,
    padding: "1px 5px",
    fontSize: "10px",
    fontWeight: 700
  },
  detailSub: {
    fontSize: "11px",
    color: "var(--text-muted)",
    marginTop: 3
  },
  reviewTag: {
    fontSize: "11px",
    fontWeight: 700
  },
  detailGrid: {
    display: "grid",
    gridTemplateColumns: "repeat(2, minmax(0, 1fr))",
    gap: 8
  },
  metric: {
    backgroundColor: "var(--bg-primary)",
    border: "1px solid var(--border-color)",
    borderRadius: 4,
    padding: "8px 10px"
  },
  metricLabel: {
    display: "block",
    fontSize: "10px",
    color: "var(--text-muted)",
    marginBottom: 3
  },
  metricValue: {
    fontSize: "12px",
    color: "var(--text-primary)"
  },
  emptySide: {
    fontSize: "12px",
    color: "var(--text-muted)",
    textAlign: "center",
    padding: "24px 12px"
  },
  exportGrid: {
    display: "grid",
    gridTemplateColumns: "repeat(2, minmax(0, 1fr))",
    gap: 8,
    marginBottom: 10
  },
  exportItem: {
    display: "flex",
    alignItems: "center",
    gap: 6,
    backgroundColor: "var(--bg-primary)",
    border: "1px solid var(--border-color)",
    borderRadius: 4,
    padding: "7px 9px",
    fontSize: "12px"
  },
  exportArtifactList: {
    display: "flex",
    flexDirection: "column",
    gap: 8,
    marginTop: 10
  },
  weightsBlock: {
    backgroundColor: "var(--bg-primary)",
    border: "1px solid var(--border-color)",
    borderRadius: 4,
    padding: "10px 12px",
    margin: "8px 0"
  },
  weightsTitle: {
    fontSize: "11px",
    color: "var(--text-muted)",
    fontWeight: 500,
    marginBottom: "6px"
  },
  weightsGrid: {
    display: "flex",
    justifyContent: "space-between",
    fontSize: "12px"
  },
  weightItem: {
    display: "flex",
    gap: 4
  },
  recommendBox: {
    backgroundColor: "var(--warning-soft)",
    border: "1px solid var(--warning-color)",
    padding: "12px",
    borderRadius: 4,
    margin: "8px 0"
  },
  recommendHeader: {
    display: "flex",
    alignItems: "center",
    gap: 6,
    fontSize: "12px"
  },
  decisionWarning: {
    backgroundColor: "var(--error-soft)",
    border: "1px solid var(--error-color)",
    borderRadius: 4,
    padding: "8px 10px",
    margin: "8px 0",
    display: "flex",
    alignItems: "flex-start",
    gap: 8
  },
  artCard: {
    backgroundColor: "var(--bg-primary)",
    border: "1px solid var(--border-color)",
    borderRadius: "4px",
    padding: "8px 10px",
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    marginTop: "8px"
  },
  artName: {
    fontWeight: 600,
    fontSize: "12px",
    color: "var(--text-primary)"
  }
};
export default OptimizationPage;
