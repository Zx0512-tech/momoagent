import React, { useCallback, useEffect, useState } from "react";
import { api, IS_MOCK_MODE } from "../../api/client";
import { useJobStore } from "../../stores/jobStore";
import { StatusBadge } from "../../components/feedback/StatusBadge";
import { ProgressBar } from "../../components/feedback/ProgressBar";
import { ErrorPanel } from "../../components/feedback/ErrorPanel";
import { ArtifactPreview } from "../../components/artifact/ArtifactPreview";
import type { Artifact, ParityReport } from "../../api/types";
import { Play, Shield, CheckCircle } from "lucide-react";
import { useCapabilityAvailability } from "../../hooks/useCapabilityAvailability";
import { useManagedIntervals } from "../../hooks/useManagedIntervals";
import { errorMessage } from "../../utils/errors";

export const SolverBatchPage: React.FC = () => {
  const solverCapability = useCapabilityAvailability("SOLVER_BATCH");
  const { jobs, fetchJobs, submitJob, cancelJob, activeJobs, error: jobError, clearError, reportError } = useJobStore();
  const { setManagedInterval, clearManagedInterval } = useManagedIntervals();
  const [currentJobId, setCurrentJobId] = useState<string | null>(null);
  
  const [form, setForm] = useState({
    solver: "ANSYS",
    caseSetId: "cases_20260701_0001",
    processCount: 4,
    coresPerProcess: 2,
    executionTimeoutS: 7200
  });

  const [parityReport, setParityReport] = useState<ParityReport | null>(null);
  const [loadingParity, setLoadingParity] = useState(true);
  const [selectedArtifact, setSelectedArtifact] = useState<Artifact | null>(null);
  const [runningParityJob, setRunningParityJob] = useState(false);

  const loadParityReport = useCallback(async () => {
    setLoadingParity(true);
    try {
      const res = await api.getLatestParityReport();
      setParityReport(res);
    } catch (e) {
      console.error("Failed to load parity report", e);
    } finally {
      setLoadingParity(false);
    }
  }, []);

  useEffect(() => {
    fetchJobs({ type: "SOLVER_BATCH" });
    loadParityReport();
  }, [fetchJobs, loadParityReport]);

  const isOpenseespyInproc = form.solver === "OPENSEESPY_INPROC";

  // Enforce single-process limit for OpenSeesPy DLL
  useEffect(() => {
    if (isOpenseespyInproc) {
      setForm(prev => ({ ...prev, processCount: 1, coresPerProcess: 1 }));
    }
  }, [form.solver, isOpenseespyInproc]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    clearError();
    try {
      const job = await submitJob("SOLVER_BATCH", {
        solver: form.solver,
        caseSetId: form.caseSetId,
        resources: {
          processCount: form.processCount,
          coresPerProcess: form.coresPerProcess,
          executionTimeoutS: form.executionTimeoutS
        }
      });
      setCurrentJobId(job.jobId);
    } catch (error) {
      reportError(errorMessage(error, "启动批处理求解失败"));
    }
  };

  const handleRunParity = async () => {
    clearError();
    setRunningParityJob(true);
    try {
      const job = await submitJob("SOLVER_PARITY", {
        configPath: "docs/examples/templates/solver_parity_baseline_workflow_template.json",
        executionTimeoutS: 1200,
        realGateTimeoutS: 1800
      });
      setCurrentJobId(job.jobId);
      
      const check = setManagedInterval(async () => {
        try {
          const updated = await api.getJob(job.jobId);
          if (updated.status === "SUCCEEDED" || updated.status === "FAILED") {
            clearManagedInterval(check);
            setRunningParityJob(false);
            loadParityReport();
          }
        } catch (error) {
          clearManagedInterval(check);
          reportError(errorMessage(error, "读取求解器一致性任务状态失败"));
          setRunningParityJob(false);
        }
      }, 2000);
    } catch (error) {
      reportError(errorMessage(error, "启动求解器一致性任务失败"));
      setRunningParityJob(false);
    }
  };

  const handleCancel = async (jobId: string) => {
    if (window.confirm("确定要取消当前正在运行的求解批处理任务吗？")) {
      try {
        await cancelJob(jobId);
      } catch {
        // Handled by store/errors
      }
    }
  };

  const activeJob = currentJobId ? activeJobs[currentJobId] : null;
  const isRunning = activeJob?.status === "RUNNING" || activeJob?.status === "QUEUED";
  const relativeTolerance = parityReport?.relativeTolerance ?? 0.1;

  return (
    <div className="page-container">
      {/* Page Header */}
      <div style={styles.header}>
        <div>
          <h1 style={styles.title}>求解器并行批量计算</h1>
          <p style={styles.subtitle}>
            调用 ANSYS / OpenSeesPy 后端并行的多工况批量动力计算，展示算例求解进度与故障诊断日志
          </p>
        </div>
      </div>

      <div style={styles.mainGrid}>
        {/* Config and form column */}
        <div style={{ flex: 3, display: "flex", flexDirection: "column", gap: 16 }}>
          <div className="panel">
            <form onSubmit={handleSubmit}>
              <div className="panel-header">
                <span style={styles.panelTitle}>
                  <Play size={16} color="var(--primary-color)" />
                  <span>批处理求解器参数配置</span>
                </span>
              </div>

              {jobError && <ErrorPanel message={jobError} />}
              {!IS_MOCK_MODE && (
                <div style={styles.concurrencyAlert}>
                  <Shield size={16} color="var(--warning-color)" style={{ flexShrink: 0 }} />
                  <div>生产模式尚未开放独立批处理求解，请从 Momo 对话发起受控分析、比较或优化工作流。</div>
                </div>
              )}

              <div style={styles.formRow}>
                <div className="form-group" style={{ flex: 1 }}>
                  <label className="form-label">求解器后端选择</label>
                  <select
                    className="form-control"
                    value={form.solver}
                    onChange={e => setForm({ ...form, solver: e.target.value })}
                  >
                    <option value="ANSYS">ANSYS (MAPDL 并行模式)</option>
                    <option value="OPENSEES">OpenSees (Subprocess 并行)</option>
                    <option value="OPENSEESPY_INPROC">OpenSeesPy (DLL 进程内串行)</option>
                  </select>
                </div>

                <div className="form-group" style={{ flex: 1 }}>
                  <label className="form-label">并行进程数</label>
                  <input
                    type="number"
                    min="1"
                    className="form-control"
                    value={form.processCount}
                    disabled={isOpenseespyInproc}
                    onChange={e => setForm({ ...form, processCount: parseInt(e.target.value) || 1 })}
                  />
                </div>
              </div>

              <div style={styles.formRow}>
                <div className="form-group" style={{ flex: 1 }}>
                  <label className="form-label">关联工况集 ID (Case Set ID)</label>
                  <input
                    className="form-control"
                    value={form.caseSetId}
                    onChange={e => setForm({ ...form, caseSetId: e.target.value })}
                    required
                  />
                </div>

                <div className="form-group" style={{ flex: 1 }}>
                  <label className="form-label">每个进程核心数</label>
                  <input
                    type="number"
                    min="1"
                    className="form-control"
                    value={form.coresPerProcess}
                    disabled={isOpenseespyInproc}
                    onChange={e => setForm({ ...form, coresPerProcess: parseInt(e.target.value) || 1 })}
                  />
                </div>

                <div className="form-group" style={{ flex: 1 }}>
                  <label className="form-label">执行超时门槛限制 (秒)</label>
                  <input
                    type="number"
                    className="form-control"
                    value={form.executionTimeoutS}
                    onChange={e => setForm({ ...form, executionTimeoutS: parseInt(e.target.value) || 3600 })}
                  />
                </div>
              </div>

              {/* OPENSEESPY DLL Safety Notice */}
              {isOpenseespyInproc && (
                <div style={styles.concurrencyAlert}>
                  <Shield size={16} color="var(--warning-color)" style={{ flexShrink: 0 }} />
                  <div>
                    <div style={{ fontWeight: 600, color: "var(--text-primary)" }}>
                      DLL 解释器状态锁硬约束 (Single Thread Lock)
                    </div>
                    <div style={{ fontSize: "11px", color: "var(--text-secondary)", marginTop: 2 }}>
                      选定 <code>OPENSEESPY_INPROC</code> 时，系统强制使用单进程串行执行。
                      ANSYS 和 OpenSees subprocess 路径可配置进程数与单进程核心数；当前 DLL 进程内路径保持 1×1。
                    </div>
                  </div>
                </div>
              )}

              <div style={styles.resourceSummary}>
                <span>资源请求:</span>
                <code style={styles.code}>{form.processCount} 进程 × {form.coresPerProcess} 核/进程 = {form.processCount * form.coresPerProcess} 核</code>
              </div>

              <button type="submit" disabled={isRunning || !solverCapability.available} className="btn btn-primary" style={{ marginTop: 12 }}>
                <span>{IS_MOCK_MODE ? "提交求解器批量任务（模拟）" : solverCapability.available ? "提交求解器批量任务" : "请使用受控智能体工作流"}</span>
              </button>
            </form>
          </div>

          {/* Finished Solver Jobs List */}
          <div className="panel" style={{ flex: 1 }}>
            <div className="panel-header">
              <span style={{ fontWeight: 600 }}>历史批量求解任务列表</span>
            </div>

            <table className="data-table">
              <thead>
                <tr>
                  <th>任务标识</th>
                  <th>求解器</th>
                  <th>关联工况</th>
                  <th>状态</th>
                  <th>耗时</th>
                  <th>产物数量</th>
                </tr>
              </thead>
              <tbody>
                {jobs.filter(j => j.type === "SOLVER_BATCH").length === 0 ? (
                  <tr>
                    <td colSpan={6} style={{ textAlign: "center", color: "var(--text-muted)", padding: 20 }}>
                      暂无历史求解记录
                    </td>
                  </tr>
                ) : (
                  jobs.filter(j => j.type === "SOLVER_BATCH").slice(0, 10).map(job => (
                    <tr key={job.jobId}>
                      <td style={{ fontWeight: 600 }}>{job.jobId}</td>
                      <td>
                        <span className="badge badge-secondary">{job.request.solver}</span>
                      </td>
                      <td>
                        <code style={styles.code}>{job.request.caseSetId}</code>
                      </td>
                      <td>
                        <StatusBadge status={job.status} />
                      </td>
                      <td style={{ fontSize: "12px", color: "var(--text-secondary)" }}>
                        {job.finishedAt ? calculateDuration(job.createdAt, job.finishedAt) : "运行中"}
                      </td>
                      <td style={{ fontSize: "12px", color: "var(--text-secondary)" }}>
                        {job.artifacts.length} 个文件
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>

        {/* Task progress and parity details column */}
        <div style={{ flex: 2, display: "flex", flexDirection: "column", gap: 16 }}>
          {/* Active Job Progress */}
          {currentJobId && (
            <div className="panel">
              <div className="panel-header">
                <span style={{ fontWeight: 600 }}>批处理求解运行状态</span>
                <StatusBadge status={activeJob?.status || "QUEUED"} />
              </div>

              {isRunning ? (
                <div>
                  <ProgressBar
                    percent={activeJob?.progress?.percent}
                    phase={activeJob?.progress?.phase}
                    message={activeJob?.progress?.message}
                  />
                  <div style={{ marginTop: 12, display: "flex", gap: 8 }}>
                    <button
                      onClick={() => handleCancel(currentJobId)}
                      className="btn btn-danger"
                      style={{ flex: 1, padding: "6px" }}
                    >
                      取消任务 (Cancel)
                    </button>
                  </div>
                </div>
              ) : (
                <div style={{ padding: "8px 0", fontSize: "12px", color: "var(--text-secondary)" }}>
                  {activeJob?.status === "SUCCEEDED" ? (
                    <div style={{ display: "flex", alignItems: "center", gap: 6, color: "var(--success-color)", fontWeight: 500 }}>
                      <CheckCircle size={16} />
                      <span>求解批计算已全部结束，数据已被安全写出。</span>
                    </div>
                  ) : activeJob?.status === "FAILED" ? (
                    <div style={{ color: "var(--error-color)" }}>
                      <span>计算出错终止: {activeJob?.error?.message}</span>
                    </div>
                  ) : (
                    <span>任务已结束</span>
                  )}
                </div>
              )}
            </div>
          )}

          {/* Solver Parity Check summary panel */}
          <div className="panel">
            <div className="panel-header">
              <span style={{ fontWeight: 600 }}>最新双求解器一致性对齐结果 (Solver Parity)</span>
              <button
                onClick={handleRunParity}
                disabled={runningParityJob || isRunning}
                className="btn btn-secondary"
                style={{ padding: "4px 8px", fontSize: "11px" }}
              >
                <span>{runningParityJob ? "运行中..." : "重新运行 Parity"}</span>
              </button>
            </div>

            {loadingParity ? (
              <div style={{ padding: "20px 0", textAlign: "center" }}>检测对齐数据中...</div>
            ) : parityReport ? (
              <div>
                <div style={styles.parityRow}>
                  <span>一致性门槛状态:</span>
                  <span style={{
                    color: parityReport.status === "PASS" ? "var(--success-color)" : "var(--error-color)",
                    fontWeight: 700
                  }}>
                    {parityReport.status === "PASS" ? "✓ 校验通过 (PASS)" : "✗ 偏差超限 (FAIL)"}
                  </span>
                </div>
                <div style={styles.parityRow}>
                  <span>Reference / Candidate:</span>
                  <span style={{ fontWeight: 600 }}>{parityReport.referenceSolver || "ANSYS/MAPDL"} / {parityReport.candidateSolver || "OpenSeesPy"}</span>
                </div>

                <div style={styles.metricsList}>
                  <div style={styles.metricItemHeader}>
                    <span>指标物理分量</span>
                    <span>最大偏差 (Relative Err)</span>
                  </div>
                  {parityReport.metrics.map((m, idx) => (
                    <div key={idx} style={styles.metricItem}>
                      <span>{m.label || m.name}</span>
                      <span style={{
                        color: m.relativeError <= relativeTolerance ? "var(--success-color)" : "var(--warning-color)",
                        fontWeight: 600
                      }}>
                        {(m.relativeError * 100).toFixed(2)}%
                      </span>
                    </div>
                  ))}
                </div>

                <div style={{ fontSize: "11px", color: "var(--text-muted)", marginTop: 10 }}>
                  当前 workflow 采用 ANSYS/MAPDL 作为 reference、OpenSeesPy 作为 candidate；相对误差门槛为 <strong>{(relativeTolerance * 100).toFixed(1)}%</strong>。
                </div>
              </div>
            ) : (
              <div style={{ color: "var(--text-muted)", padding: 20, textAlign: "center" }}>
                暂无对齐报告，请点击上方按钮测试。
              </div>
            )}
          </div>
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

const calculateDuration = (start: string, end: string) => {
  const diff = new Date(end).getTime() - new Date(start).getTime();
  if (diff < 0) return "0秒";
  const seconds = Math.floor(diff / 1000);
  if (seconds < 60) return `${seconds}秒`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes}分${seconds % 60}秒`;
};

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
  concurrencyAlert: {
    backgroundColor: "var(--warning-soft)",
    border: "1px solid var(--warning-color)",
    borderRadius: "4px",
    padding: "10px 12px",
    margin: "12px 0",
    display: "flex",
    alignItems: "flex-start",
    gap: 10
  },
  resourceSummary: {
    backgroundColor: "var(--bg-primary)",
    border: "1px solid var(--border-color)",
    borderRadius: "4px",
    padding: "8px 10px",
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    fontSize: "12px",
    color: "var(--text-secondary)",
    marginTop: "4px"
  },
  code: {
    fontFamily: "var(--font-mono)",
    backgroundColor: "var(--bg-primary)",
    padding: "1px 4px",
    borderRadius: "2px",
    border: "1px solid var(--border-color)",
    fontSize: "11px"
  },
  parityRow: {
    display: "flex",
    justifyContent: "space-between",
    padding: "8px 0",
    borderBottom: "1px solid var(--border-color)",
    fontSize: "13px"
  },
  metricsList: {
    display: "flex",
    flexDirection: "column",
    gap: 4,
    marginTop: "8px"
  },
  metricItemHeader: {
    display: "flex",
    justifyContent: "space-between",
    fontSize: "11px",
    fontWeight: 600,
    color: "var(--text-muted)",
    padding: "4px 0"
  },
  metricItem: {
    display: "flex",
    justifyContent: "space-between",
    fontSize: "12px",
    padding: "4px 0",
    borderBottom: "1px solid var(--border-color)"
  }
};
export default SolverBatchPage;
