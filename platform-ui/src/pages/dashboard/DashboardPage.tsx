import React, { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Activity,
  ArrowRight,
  CheckCircle2,
  Download,
  Eye,
  FileUp,
  PlayCircle,
  RefreshCw
} from "lucide-react";
import { api, resolveApiUrl } from "../../api/client";
import {
  ANALYSIS_SCENARIO_OPTIONS,
  TASK_EXECUTION_TARGET_OPTIONS
} from "../../api/engineeringOptions";
import type { Artifact, DashboardSummary, Job, ModuleStatusKind } from "../../api/types";
import {
  verificationLabel,
  workflowAcceptanceLabel
} from "../../components/artifact/earthquakeWorkflowStatus";
import {
  EarthquakeWorkflowOverviewPanel
} from "../../components/artifact/EarthquakeWorkflowOverviewPanel";
import { ArtifactPreview } from "../../components/artifact/ArtifactPreview";
import { ErrorPanel } from "../../components/feedback/ErrorPanel";
import { LoadingSpinner } from "../../components/feedback/LoadingSpinner";
import { ProgressBar } from "../../components/feedback/ProgressBar";
import { StatusBadge } from "../../components/feedback/StatusBadge";
import { useEngineeringConfigStore } from "../../stores/engineeringConfigStore";
import { useJobStore } from "../../stores/jobStore";
import { buildDashboardExecutionRequest } from "./dashboardExecution";

const STATUS_META: Record<ModuleStatusKind, { label: string; className: string; color: string }> = {
  UNCONFIGURED: { label: "未配置", className: "badge badge-error", color: "var(--error-color)" },
  INCOMPLETE: { label: "配置不完整", className: "badge badge-warning", color: "var(--warning-color)" },
  CONFIGURED: { label: "已配置", className: "badge badge-info", color: "var(--info-color)" },
  VALIDATED: { label: "已校验", className: "badge badge-success", color: "var(--success-color)" }
};

const finalWorkflowLabel = (result?: Record<string, any>) => {
  if (!result || result.mode !== "real_baseline_optimization") return null;
  return workflowAcceptanceLabel(result.validationStatus, result.reviewStatus, result.finalRecommendationStatus);
};

const latestRealWorkflowJobFrom = (jobs: Job[], activeJobs: Record<string, Job>) => {
  const merged = new Map<string, Job>();
  jobs.forEach(job => merged.set(job.jobId, job));
  Object.values(activeJobs).forEach(job => merged.set(job.jobId, job));
  return Array.from(merged.values())
    .filter(job => job.result?.mode === "real_baseline_optimization" && job.result?.earthquakeWorkflowOverviewArtifactId)
    .sort((left, right) => Date.parse(right.createdAt) - Date.parse(left.createdAt))[0];
};

export const DashboardPage: React.FC = () => {
  const navigate = useNavigate();
  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [loadingSummary, setLoadingSummary] = useState(true);
  const [summaryError, setSummaryError] = useState<string | null>(null);
  const [recentArtifacts, setRecentArtifacts] = useState<Artifact[]>([]);
  const [selectedArtifact, setSelectedArtifact] = useState<Artifact | null>(null);
  const [gateSubmitting, setGateSubmitting] = useState(false);
  const [executionSubmitting, setExecutionSubmitting] = useState(false);
  const [lastExecutionJobId, setLastExecutionJobId] = useState<string | null>(null);
  const [earthquakeOverview, setEarthquakeOverview] = useState<any | null>(null);
  const [earthquakeOverviewArtifactId, setEarthquakeOverviewArtifactId] = useState<string | null>(null);
  const [earthquakeOverviewLoading, setEarthquakeOverviewLoading] = useState(false);
  const [earthquakeOverviewError, setEarthquakeOverviewError] = useState<string | null>(null);

  const {
    projectConfig,
    globalTaskConfig,
    solverBatchConfig,
    moduleStatus,
    updateProjectConfig,
    updateGlobalTaskConfig,
    validateAllModules,
    getEngineeringConfig
  } = useEngineeringConfigStore();
  const { jobs, fetchJobs, submitJob, activeJobs } = useJobStore();
  const latestRealWorkflowJob = latestRealWorkflowJobFrom(jobs, activeJobs);

  const loadDashboardData = useCallback(async () => {
    setLoadingSummary(true);
    setSummaryError(null);
    try {
      const summaryData = await api.getDashboardSummary();
      setSummary(summaryData);
      await fetchJobs({ page: 1, pageSize: 5 });
      const artifactsData = await api.getArtifacts({ page: 1, pageSize: 5 });
      setRecentArtifacts(artifactsData.data);
    } catch (e: any) {
      setSummaryError(e.message || "未能加载控制台汇总数据");
    } finally {
      setLoadingSummary(false);
    }
  }, [fetchJobs]);

  useEffect(() => {
    loadDashboardData();
  }, [loadDashboardData]);

  useEffect(() => {
    const artifactId = latestRealWorkflowJob?.result?.earthquakeWorkflowOverviewArtifactId;
    if (!artifactId) {
      setEarthquakeOverview(null);
      setEarthquakeOverviewArtifactId(null);
      setEarthquakeOverviewError(null);
      setEarthquakeOverviewLoading(false);
      return;
    }
    if (artifactId === earthquakeOverviewArtifactId && (earthquakeOverview || earthquakeOverviewError)) return;

    let cancelled = false;
    setEarthquakeOverviewLoading(true);
    setEarthquakeOverviewError(null);
    api.getArtifactPreview(artifactId)
      .then(preview => {
        if (cancelled) return;
        setEarthquakeOverview(preview);
        setEarthquakeOverviewArtifactId(artifactId);
      })
      .catch((error: any) => {
        if (cancelled) return;
        setEarthquakeOverview(null);
        setEarthquakeOverviewArtifactId(artifactId);
        setEarthquakeOverviewError(error.message || "读取地震 workflow overview 失败");
      })
      .finally(() => {
        if (!cancelled) setEarthquakeOverviewLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [latestRealWorkflowJob, earthquakeOverviewArtifactId, earthquakeOverview, earthquakeOverviewError]);

  const handleRunFastGate = async () => {
    if (hasActiveTask) return;
    setGateSubmitting(true);
    try {
      await submitJob("PROJECT_GATE_FAST", {});
      await fetchJobs({ page: 1, pageSize: 5 });
    } finally {
      setGateSubmitting(false);
    }
  };

  const handleStartExecution = async () => {
    const missing = moduleStatus.filter(
      item => item.required && (item.status === "UNCONFIGURED" || item.status === "INCOMPLETE")
    );
    if (missing.length > 0 || hasActiveTask) return;

    setExecutionSubmitting(true);
    setSummaryError(null);
    try {
      const engineeringConfig = getEngineeringConfig();
      await api.saveEngineeringConfig(engineeringConfig);
      const executionRequest = buildDashboardExecutionRequest({
        projectName: projectConfig.projectName,
        modelFileName: projectConfig.modelFile.fileName,
        solver: globalTaskConfig.solver,
        scenario: globalTaskConfig.scenario,
        executionTarget: globalTaskConfig.executionTarget,
        requiredModules: moduleStatus.filter(item => item.required).map(item => item.moduleId),
        executionTimeoutS: solverBatchConfig.caseTimeoutS
      });
      if (!executionRequest) {
        throw new Error("当前执行目标需要在对应模块页面提交专用参数，Dashboard 不会生成通用占位任务");
      }
      const job = await submitJob(executionRequest.type, executionRequest.params);
      setLastExecutionJobId(job.jobId);
      await fetchJobs({ page: 1, pageSize: 5 });
    } catch (e: any) {
      setSummaryError(e.message || "启动当前流程执行失败");
    } finally {
      setExecutionSubmitting(false);
    }
  };

  if (loadingSummary && !summary) {
    return (
      <div style={{ display: "flex", justifyContent: "center", padding: "100px 0" }}>
        <LoadingSpinner />
        <span style={{ marginLeft: 8, color: "var(--text-secondary)" }}>正在加载流程主面板...</span>
      </div>
    );
  }

  const requiredMissing = moduleStatus.filter(
    item => item.required && (item.status === "UNCONFIGURED" || item.status === "INCOMPLETE")
  );
  const activeTask = Object.values(activeJobs).find(job => job.status === "RUNNING" || job.status === "QUEUED")
    || jobs.find(job => job.status === "RUNNING" || job.status === "QUEUED");
  const hasActiveTask = Boolean(activeTask);
  const dashboardExecutionSupported = Boolean(buildDashboardExecutionRequest({
    projectName: projectConfig.projectName,
    modelFileName: projectConfig.modelFile.fileName,
    solver: globalTaskConfig.solver,
    scenario: globalTaskConfig.scenario,
    executionTarget: globalTaskConfig.executionTarget,
    requiredModules: moduleStatus.filter(item => item.required).map(item => item.moduleId),
    executionTimeoutS: solverBatchConfig.caseTimeoutS
  }));
  const canStartExecution = dashboardExecutionSupported
    && requiredMissing.length === 0
    && !executionSubmitting
    && !hasActiveTask;
  const executionTargetMeta = TASK_EXECUTION_TARGET_OPTIONS.find(item => item.id === globalTaskConfig.executionTarget);
  const startExecutionMessage = hasActiveTask
    ? `已有任务未结束：${activeTask?.title ?? activeTask?.jobId}，请等待完成后再启动新任务`
    : !dashboardExecutionSupported
    ? "当前目标需要在对应模块页面填写专用参数后提交"
    : requiredMissing.length === 0
    ? `已满足当前目标配置，可执行：${executionTargetMeta?.label ?? globalTaskConfig.executionTarget}`
    : `仍缺少：${requiredMissing.map(item => item.label).join("、")}`;
  return (
    <div className="page-container">
      <div style={styles.header}>
        <div>
          <h1 style={styles.title}>MOMO 桥梁分析与优化平台</h1>
          <p style={styles.subtitle}>从模型文件、阻尼器布置、荷载配置到代理模型和优化决策的流程化配置总入口</p>
        </div>
        <div style={styles.headerActions}>
          <button className="btn btn-secondary" onClick={loadDashboardData}>
            <RefreshCw size={14} />
            <span>刷新</span>
          </button>
          <button className="btn btn-primary" onClick={validateAllModules}>
            <CheckCircle2 size={14} />
            <span>校验必需模块</span>
          </button>
        </div>
      </div>

      {summaryError && <ErrorPanel message={summaryError} onRetry={loadDashboardData} />}

      <div className="panel" style={styles.globalPanel}>
        <div className="panel-header">
          <span style={styles.panelTitle}>
            <Activity size={16} color="var(--primary-color)" />
            <span>全局任务配置</span>
          </span>
          <span className={requiredMissing.length === 0 ? "badge badge-success" : "badge badge-warning"}>
            {requiredMissing.length === 0 ? "必需模块已满足" : `缺少 ${requiredMissing.length} 项配置`}
          </span>
        </div>

        <div style={styles.formGrid}>
          <div className="form-group">
            <label className="form-label">项目名称</label>
            <input
              className="form-control"
              value={projectConfig.projectName}
              onChange={event => updateProjectConfig({ projectName: event.target.value })}
            />
          </div>

          <div className="form-group">
            <label className="form-label">模型文件</label>
            <div style={styles.fileInputRow}>
              <input
                className="form-control"
                value={projectConfig.modelFile.fileName}
                onChange={event => updateProjectConfig({
                  modelFile: {
                    ...projectConfig.modelFile,
                    fileName: event.target.value,
                    parseStatus: event.target.value ? "PARSED" : "NOT_UPLOADED"
                  }
                })}
              />
              <button className="btn btn-secondary" title="选择模型文件">
                <FileUp size={14} />
              </button>
            </div>
          </div>

          <div className="form-group">
            <label className="form-label">求解器类型</label>
            <select
              className="form-control"
              value={globalTaskConfig.solver}
              onChange={event => updateGlobalTaskConfig({ solver: event.target.value as typeof globalTaskConfig.solver })}
            >
              <option value="ANSYS">ANSYS MAPDL</option>
              <option value="OPENSEES">OpenSees</option>
              <option value="OPENSEESPY_INPROC">OpenSeesPy Inproc</option>
            </select>
          </div>

          <div className="form-group">
            <label className="form-label">分析工况</label>
            <select
              className="form-control"
              value={globalTaskConfig.scenario}
              onChange={event => updateGlobalTaskConfig({ scenario: event.target.value as typeof globalTaskConfig.scenario })}
            >
              {ANALYSIS_SCENARIO_OPTIONS.map(item => (
                <option key={item.id} value={item.id}>{item.label}</option>
              ))}
            </select>
          </div>

          <div className="form-group" style={{ gridColumn: "span 2" }}>
            <label className="form-label">执行目标</label>
            <select
              className="form-control"
              value={globalTaskConfig.executionTarget}
              onChange={event => updateGlobalTaskConfig({ executionTarget: event.target.value as typeof globalTaskConfig.executionTarget })}
            >
              {TASK_EXECUTION_TARGET_OPTIONS.map(item => (
                <option key={item.id} value={item.id}>{item.label} - {item.note}</option>
              ))}
            </select>
          </div>
        </div>

        {projectConfig.modelFile.summary && (
          <div style={styles.modelSummary}>
            模型解析摘要：节点 {projectConfig.modelFile.summary.nodeCount} 个，
            单元 {projectConfig.modelFile.summary.elementCount} 个，
            已识别/注册阻尼器 {projectConfig.modelFile.summary.detectedDamperCount} 个。
          </div>
        )}

        {globalTaskConfig.scenario === "EARTHQUAKE" && globalTaskConfig.executionTarget === "OPTIMIZATION_DECISION" && (
          <div style={styles.realWorkflowContract}>
            地震真实 workflow：C=1000-10000，alpha=0.3-1.0；10 个 LHS 点 + 4 个角点 + 1 个中心点 + 1 次无控 baseline，共 16 点。ANSYS 使用 4 个并行任务，OpenSeesPy in-process 保持串行。
          </div>
        )}
      </div>

      <div style={styles.statusGrid}>
        {moduleStatus.map(item => {
          const meta = STATUS_META[item.status];
          return (
            <button
              key={item.moduleId}
              type="button"
              style={{
                ...styles.statusCard,
                borderColor: item.required ? meta.color : "var(--border-color)"
              }}
              onClick={() => navigate(item.route)}
            >
              <div style={styles.statusCardHeader}>
                <span style={styles.statusTitle}>{item.label}</span>
                <span className={meta.className}>{meta.label}</span>
              </div>
              <p style={styles.statusMessage}>{item.message}</p>
              <div style={styles.statusFooter}>
                <span>{item.required ? "当前目标必需" : "当前目标可选"}</span>
                <ArrowRight size={14} />
              </div>
            </button>
          );
        })}
        <button
          type="button"
          disabled={!canStartExecution}
          style={{
            ...styles.statusCard,
            ...styles.executionCard,
            borderColor: canStartExecution ? "var(--success-color)" : "var(--warning-color)",
            cursor: canStartExecution ? "pointer" : "not-allowed",
            opacity: canStartExecution ? 1 : 0.72
          }}
          onClick={handleStartExecution}
        >
          <div style={styles.statusCardHeader}>
            <span style={styles.statusTitle}>开始执行</span>
            <span className={canStartExecution ? "badge badge-success" : "badge badge-warning"}>
              {executionSubmitting ? "提交中" : canStartExecution ? "可执行" : "等待配置"}
            </span>
          </div>
          <p style={styles.statusMessage}>{startExecutionMessage}</p>
          {lastExecutionJobId && (
            <div style={styles.executionJobHint}>最近提交：{lastExecutionJobId}</div>
          )}
          <div style={styles.statusFooter}>
            <span>{executionSubmitting ? "正在启动流程" : "提交当前执行目标"}</span>
            <PlayCircle size={14} />
          </div>
        </button>
      </div>

      <div style={styles.mainSection}>
        <div className="panel" style={{ ...styles.listPanel, flex: 3 }}>
          <div className="panel-header">
            <span style={styles.panelTitle}>最近执行的计算任务</span>
            <button
              onClick={handleRunFastGate}
              disabled={gateSubmitting || hasActiveTask}
              className="btn btn-primary"
              style={{ padding: "4px 10px", fontSize: 12 }}
            >
              <span>{hasActiveTask ? "已有任务运行中" : gateSubmitting ? "正在提交..." : "运行快速回归门槛"}</span>
            </button>
          </div>
          <div style={styles.taskListFrame}>
            <table className="data-table" style={styles.taskTable}>
              <thead>
                <tr>
                  <th>任务</th>
                  <th>类型</th>
                  <th>状态</th>
                  <th>提交时间</th>
                  <th>进度</th>
                </tr>
              </thead>
              <tbody>
                {jobs.length === 0 ? (
                  <tr>
                    <td colSpan={5} style={styles.emptyCell}>暂无运行记录</td>
                  </tr>
                ) : (
                  jobs.map(job => {
                    const activeJob = activeJobs[job.jobId] || job;
                    const isRunning = activeJob.status === "RUNNING" || activeJob.status === "QUEUED";
                    return (
                      <tr key={job.jobId}>
                        <td style={{ fontWeight: 600 }}>
                          <div>{activeJob.title}</div>
                          {activeJob.result?.mode === "real_baseline_optimization" && (
                            <div style={styles.realWorkflowStatus}>
                              <span className={finalWorkflowLabel(activeJob.result) === "最终推荐方案" ? "badge badge-success" : "badge badge-warning"}>
                                {finalWorkflowLabel(activeJob.result)}
                              </span>
                              <span>validation: {verificationLabel(activeJob.result.validationStatus)}</span>
                              <span>review: {verificationLabel(activeJob.result.reviewStatus)}</span>
                            </div>
                          )}
                        </td>
                        <td style={styles.mono}>{activeJob.type}</td>
                        <td><StatusBadge status={activeJob.status} /></td>
                        <td style={styles.muted}>{new Date(activeJob.createdAt).toLocaleString("zh-CN")}</td>
                        <td style={{ minWidth: 180 }}>
                          {isRunning ? (
                            <ProgressBar
                              percent={activeJob.progress?.percent}
                              phase={activeJob.progress?.phase}
                              message={activeJob.progress?.message}
                            />
                          ) : (
                            <span style={styles.muted}>计算结束</span>
                          )}
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
        </div>

        <div className="panel" style={{ ...styles.listPanel, flex: 2 }}>
          <div className="panel-header">
            <span style={styles.panelTitle}>最新平台制品</span>
          </div>
          <div style={styles.artifactListFrame}>
            <div style={styles.artifactList}>
              {recentArtifacts.length === 0 ? (
                <div style={styles.emptyCell}>暂无生成制品</div>
              ) : (
                recentArtifacts.map(artifact => (
                  <div key={artifact.artifactId} style={styles.artifactRow}>
                    <div>
                      <div style={styles.artName}>{artifact.name}</div>
                      <div style={styles.muted}>{artifact.kind}</div>
                    </div>
                    <div style={styles.artActions}>
                      {artifact.canPreview && (
                        <button
                          onClick={() => setSelectedArtifact(artifact)}
                          className="btn btn-secondary"
                          style={styles.iconBtn}
                          title="预览"
                        >
                          <Eye size={13} />
                        </button>
                      )}
                      <a
                        href={resolveApiUrl(artifact.downloadUrl)}
                        download
                        className="btn btn-secondary"
                        style={styles.iconBtn}
                        title="下载"
                      >
                        <Download size={13} />
                      </a>
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>
        </div>
      </div>

      {(latestRealWorkflowJob || earthquakeOverviewLoading || earthquakeOverviewError) && (
        <div className="panel" style={styles.earthquakeResultPanel}>
          <div className="panel-header">
            <span style={styles.panelTitle}>地震真实 workflow 结果</span>
            {latestRealWorkflowJob?.result && (
              <span className={finalWorkflowLabel(latestRealWorkflowJob.result) === "最终推荐方案" ? "badge badge-success" : "badge badge-warning"}>
                {finalWorkflowLabel(latestRealWorkflowJob.result)}
              </span>
            )}
          </div>

          {earthquakeOverviewLoading && (
            <div style={styles.resultMessage}>
              <LoadingSpinner />
              <span>正在读取地震 workflow overview artifact...</span>
            </div>
          )}
          {earthquakeOverviewError && <ErrorPanel message={earthquakeOverviewError} />}

          {earthquakeOverview && <EarthquakeWorkflowOverviewPanel overview={earthquakeOverview} />}
        </div>
      )}

      {selectedArtifact && (
        <ArtifactPreview artifact={selectedArtifact} onClose={() => setSelectedArtifact(null)} />
      )}
    </div>
  );
};

const styles: Record<string, React.CSSProperties> = {
  header: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    borderBottom: "1px solid var(--border-color)",
    paddingBottom: 14
  },
  title: {
    fontSize: 18,
    fontWeight: 700,
    color: "var(--text-primary)"
  },
  subtitle: {
    fontSize: 12,
    color: "var(--text-secondary)",
    marginTop: 4
  },
  headerActions: {
    display: "flex",
    gap: 8
  },
  globalPanel: {
    marginTop: 0
  },
  panelTitle: {
    display: "flex",
    alignItems: "center",
    gap: 8,
    fontWeight: 600
  },
  formGrid: {
    display: "grid",
    gridTemplateColumns: "repeat(3, minmax(0, 1fr))",
    gap: 12
  },
  fileInputRow: {
    display: "grid",
    gridTemplateColumns: "1fr 36px",
    gap: 8
  },
  modelSummary: {
    marginTop: 12,
    padding: "9px 12px",
    border: "1px solid var(--border-color)",
    borderRadius: 4,
    backgroundColor: "var(--bg-primary)",
    color: "var(--text-secondary)",
    fontSize: 12
  },
  realWorkflowContract: {
    marginTop: 8,
    padding: "9px 12px",
    border: "1px solid var(--info-color)",
    borderRadius: 4,
    backgroundColor: "var(--bg-primary)",
    color: "var(--text-secondary)",
    fontSize: 12,
    lineHeight: 1.5
  },
  realWorkflowStatus: {
    display: "flex",
    flexWrap: "wrap",
    gap: 6,
    marginTop: 6,
    color: "var(--text-muted)",
    fontSize: 11,
    fontWeight: 400
  },
  statusGrid: {
    display: "grid",
    gridTemplateColumns: "repeat(4, minmax(0, 1fr))",
    gap: 12
  },
  statusCard: {
    textAlign: "left",
    border: "1px solid var(--border-color)",
    borderRadius: 6,
    backgroundColor: "var(--bg-secondary)",
    color: "var(--text-primary)",
    padding: 14,
    cursor: "pointer",
    minHeight: 120
  },
  statusCardHeader: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    gap: 8
  },
  statusTitle: {
    fontWeight: 700,
    fontSize: 13
  },
  statusMessage: {
    minHeight: 36,
    margin: "12px 0",
    color: "var(--text-secondary)",
    fontSize: 12,
    lineHeight: 1.5
  },
  statusFooter: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    color: "var(--text-muted)",
    fontSize: 11
  },
  executionCard: {
    backgroundColor: "var(--bg-primary)"
  },
  executionJobHint: {
    marginTop: -6,
    marginBottom: 8,
    color: "var(--text-muted)",
    fontFamily: "var(--font-mono)",
    fontSize: 10
  },
  mainSection: {
    display: "flex",
    gap: 16,
    minHeight: 320
  },
  listPanel: {
    display: "flex",
    flexDirection: "column",
    minWidth: 0,
    minHeight: 0,
    overflow: "hidden"
  },
  taskListFrame: {
    flex: 1,
    minHeight: 0,
    maxHeight: 300,
    width: "100%",
    boxSizing: "border-box",
    overflow: "auto",
    border: "1px solid var(--border-color)",
    borderRadius: 4,
    backgroundColor: "var(--bg-primary)"
  },
  taskTable: {
    minWidth: 720,
    tableLayout: "fixed"
  },
  emptyCell: {
    textAlign: "center",
    color: "var(--text-muted)",
    padding: 18,
    fontSize: 12
  },
  mono: {
    fontFamily: "var(--font-mono)",
    fontSize: 11
  },
  muted: {
    color: "var(--text-muted)",
    fontSize: 12
  },
  artifactList: {
    display: "flex",
    flexDirection: "column",
    gap: 8
  },
  artifactListFrame: {
    flex: 1,
    minHeight: 0,
    maxHeight: 300,
    width: "100%",
    boxSizing: "border-box",
    overflowY: "auto",
    border: "1px solid var(--border-color)",
    borderRadius: 4,
    backgroundColor: "var(--bg-primary)",
    padding: 8
  },
  artifactRow: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    padding: "10px 12px",
    border: "1px solid var(--border-color)",
    borderRadius: 4,
    backgroundColor: "var(--bg-primary)"
  },
  artName: {
    fontWeight: 600,
    fontSize: 12
  },
  artActions: {
    display: "flex",
    gap: 6
  },
  iconBtn: {
    padding: "4px 6px"
  },
  earthquakeResultPanel: {
    marginTop: 0
  },
  earthquakeResultGrid: {
    display: "grid",
    gridTemplateColumns: "repeat(2, minmax(0, 1fr))",
    gap: 12
  },
  resultMessage: {
    display: "flex",
    alignItems: "center",
    gap: 8,
    color: "var(--text-secondary)",
    fontSize: 12
  },
  resultSummaryStrip: {
    gridColumn: "1 / -1",
    display: "flex",
    flexWrap: "wrap",
    gap: 8,
    color: "var(--text-secondary)",
    fontSize: 12
  },
  tableBlock: {
    minWidth: 0
  },
  blockTitle: {
    marginBottom: 6,
    color: "var(--text-primary)",
    fontSize: 12,
    fontWeight: 700
  },
  compactTableFrame: {
    maxHeight: 260,
    overflow: "auto",
    border: "1px solid var(--border-color)",
    borderRadius: 4,
    backgroundColor: "var(--bg-primary)"
  },
  compactTable: {
    minWidth: 420,
    tableLayout: "fixed"
  },
  responseTable: {
    minWidth: 760,
    tableLayout: "fixed"
  }
};

export default DashboardPage;
