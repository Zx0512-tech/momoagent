import React, { useEffect, useMemo, useState } from "react";
import { api, IS_MOCK_MODE, resolveApiUrl } from "../../api/client";
import { useCapabilityAvailability } from "../../hooks/useCapabilityAvailability";
import { useJobStore } from "../../stores/jobStore";
import { StatusBadge } from "../../components/feedback/StatusBadge";
import { ProgressBar } from "../../components/feedback/ProgressBar";
import { ErrorPanel } from "../../components/feedback/ErrorPanel";
import { ArtifactPreview } from "../../components/artifact/ArtifactPreview";
import type { Artifact, SurrogateDatasetSourceMode, SurrogateModelFamily } from "../../api/types";
import {
  DEFAULT_SURROGATE_TARGET_METRIC_IDS,
  SURROGATE_MODEL_OPTIONS,
  getResultObjectTypeLabel,
  getResultResponseTypeLabel,
  getResultStatisticLabel,
  getSurrogateResponseMetricOptions
} from "../../api/engineeringOptions";
import { useEngineeringConfigStore } from "../../stores/engineeringConfigStore";
import { Brain, Sliders, CheckCircle, ToggleLeft, ToggleRight } from "lucide-react";
import { useManagedIntervals } from "../../hooks/useManagedIntervals";
import { errorMessage } from "../../utils/errors";
import {
  SURROGATE_SIMULATION_LABEL,
  buildMockInfillQueue,
  buildMockTrainingMetrics
} from "./surrogateDemo";

interface TrainingResult {
  model: string;
  target: string;
  r2: number;
  rmse: number;
  mae: number;
  cvScore: number;
}

const EARTHQUAKE_SURROGATE_MODELS: SurrogateModelFamily[] = [
  "GPR",
  "KRIGING",
  "RBF",
  "RESPONSE_SURFACE",
  "SVR",
  "PCE",
  "MARS"
];

export const SurrogatePage: React.FC = () => {
  const trainingCapability = useCapabilityAvailability("SURROGATE_TRAINING");
  const activeLearningCapability = useCapabilityAvailability("ACTIVE_LEARNING");
  const { resultExtractionConfig } = useEngineeringConfigStore();
  const { submitJob, activeJobs, error: jobError, clearError, reportError } = useJobStore();
  const { setManagedInterval, clearManagedInterval } = useManagedIntervals();
  const [currentJobId, setCurrentJobId] = useState<string | null>(null);
  
  // Active learning job state
  const [activeLearningJobId, setActiveLearningJobId] = useState<string | null>(null);

  // Form states - Training
  const [trainForm, setTrainForm] = useState({
    datasetSourceMode: "DOE_DATASET" as SurrogateDatasetSourceMode,
    datasetId: "",
    importedDatasetArtifactId: "",
    importedDatasetSchemaText: JSON.stringify({
      featureColumns: ["dampingCoefficient", "velocityExponent"],
      targetColumns: {
        metric_beam_end_ux_peak: "beam_end_displacement",
        metric_beam_end_ux_cumulative: "beam_end_cumulative_displacement",
        metric_tower_base_shear: "tower_base_shear",
        metric_tower_base_moment: "tower_base_moment"
      }
    }, null, 2),
    modelFamilies: EARTHQUAKE_SURROGATE_MODELS,
    targetMetricIds: [...DEFAULT_SURROGATE_TARGET_METRIC_IDS] as string[],
    validationMethod: "K_FOLD",
    folds: 5
  });

  // Form states - Active learning infill
  const [infillForm, setInfillForm] = useState({
    activeLearningEnabled: true,
    surrogateRunId: "sur_20260701_0001",
    strategy: "UNCERTAINTY_AND_PARETO",
    batchSize: 8,
    requiresRealFemReview: true
  });

  const [trainingMetrics, setTrainingMetrics] = useState<TrainingResult[]>([]);
  const [trainingDatasetArtifacts, setTrainingDatasetArtifacts] = useState<Artifact[]>([]);
  const [selectedArtifact, setSelectedArtifact] = useState<Artifact | null>(null);
  const [surrogateArtifact, setSurrogateArtifact] = useState<Artifact | null>(null);

  // Infill results queue
  const [infillQueue, setInfillQueue] = useState<Array<{
    caseId: string;
    params: string;
    status: string;
    source: string;
  }>>([]);

  const handleModelChange = (model: SurrogateModelFamily, checked: boolean) => {
    if (checked) {
      setTrainForm(prev => ({ ...prev, modelFamilies: [...prev.modelFamilies, model] }));
    } else {
      setTrainForm(prev => ({ ...prev, modelFamilies: prev.modelFamilies.filter(m => m !== model) }));
    }
  };

  const responseMetricOptions = useMemo(() => getSurrogateResponseMetricOptions([
    ...resultExtractionConfig.structuralMetrics,
    ...resultExtractionConfig.autoDamperMetrics
  ]), [resultExtractionConfig.autoDamperMetrics, resultExtractionConfig.structuralMetrics]);

  useEffect(() => {
    let cancelled = false;
    api.getArtifacts({ kind: "RAW_DATA", pageSize: 100 })
      .then(result => {
        if (cancelled) return;
        const datasets = result.data.filter(artifact => (
          artifact.name.toLowerCase().includes("dataset") ||
          artifact.path.toLowerCase().includes("/doe/")
        ));
        setTrainingDatasetArtifacts(datasets);
        setTrainForm(prev => (
          prev.datasetId || datasets.length === 0
            ? prev
            : { ...prev, datasetId: datasets[0].artifactId }
        ));
      })
      .catch(() => {
        if (!cancelled) setTrainingDatasetArtifacts([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const getMetricLabel = (metricId: string) =>
    responseMetricOptions.find(metric => metric.id === metricId)?.name ?? metricId;

  const handleTargetChange = (metricId: string, checked: boolean) => {
    if (checked) {
      setTrainForm(prev => ({ ...prev, targetMetricIds: [...prev.targetMetricIds, metricId] }));
    } else {
      setTrainForm(prev => ({ ...prev, targetMetricIds: prev.targetMetricIds.filter(id => id !== metricId) }));
    }
  };

  const parseImportedDatasetSchema = () => {
    try {
      return JSON.parse(trainForm.importedDatasetSchemaText);
    } catch {
      return {
        featureColumns: [],
        targetColumns: {}
      };
    }
  };

  const handleTrainSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    clearError();
    setTrainingMetrics([]);
    setSurrogateArtifact(null);

    try {
      const job = await submitJob("SURROGATE_TRAINING", {
        datasetSourceMode: trainForm.datasetSourceMode,
        datasetId: trainForm.datasetSourceMode === "DOE_DATASET" ? trainForm.datasetId : undefined,
        importedDatasetArtifactId:
          trainForm.datasetSourceMode === "USER_IMPORTED_ARTIFACT" ? trainForm.importedDatasetArtifactId : undefined,
        importedDatasetSchema:
          trainForm.datasetSourceMode === "USER_IMPORTED_ARTIFACT" ? parseImportedDatasetSchema() : undefined,
        modelFamilies: trainForm.modelFamilies,
        targetMetricIds: trainForm.targetMetricIds,
        targets: trainForm.targetMetricIds,
        validation: {
          method: trainForm.validationMethod,
          folds: trainForm.folds
        }
      });
      setCurrentJobId(job.jobId);

      const check = setManagedInterval(async () => {
        try {
          const updated = await api.getJob(job.jobId);
            if (updated.status === "SUCCEEDED") {
            clearManagedInterval(check);
            setTrainingMetrics(
              IS_MOCK_MODE
                ? buildMockTrainingMetrics(
                    trainForm.targetMetricIds,
                    trainForm.modelFamilies,
                    getMetricLabel
                  )
                : []
            );

            // Fetch artifacts
            const arts = await api.getArtifacts({ jobId: job.jobId });
            if (arts.data.length > 0) {
              setSurrogateArtifact(arts.data.find(artifact => artifact.kind === "SURROGATE_MODEL") ?? arts.data[0]);
            }
          } else if (updated.status === "FAILED" || updated.status === "CANCELLED") {
            clearManagedInterval(check);
          }
        } catch (error) {
          clearManagedInterval(check);
          reportError(errorMessage(error, "读取代理模型训练任务状态失败"));
        }
      }, 1500);
    } catch (error) {
      reportError(errorMessage(error, "启动代理模型训练失败"));
    }
  };

  const handleInfillSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    clearError();
    setInfillQueue([]);
    try {
      const job = await submitJob("ACTIVE_LEARNING", infillForm);
      setActiveLearningJobId(job.jobId);

      const check = setManagedInterval(async () => {
        try {
          const updated = await api.getJob(job.jobId);
          if (updated.status === "SUCCEEDED") {
            clearManagedInterval(check);
            if (updated.result?.skipped) {
              setInfillQueue([]);
              return;
            }
            setInfillQueue(IS_MOCK_MODE ? buildMockInfillQueue() : []);
          } else if (updated.status === "FAILED" || updated.status === "CANCELLED") {
            clearManagedInterval(check);
          }
        } catch (error) {
          clearManagedInterval(check);
          reportError(errorMessage(error, "读取主动学习任务状态失败"));
        }
      }, 1500);
    } catch (error) {
      reportError(errorMessage(error, "启动主动学习失败"));
    }
  };

  const activeJob = currentJobId ? activeJobs[currentJobId] : null;
  const isRunning = activeJob?.status === "RUNNING" || activeJob?.status === "QUEUED";
  const canSubmitTraining = trainForm.modelFamilies.length > 0 &&
    trainForm.targetMetricIds.length > 0 &&
    (trainForm.datasetSourceMode === "DOE_DATASET"
      ? Boolean(trainForm.datasetId)
      : Boolean(trainForm.importedDatasetArtifactId));

  const activeInfillJob = activeLearningJobId ? activeJobs[activeLearningJobId] : null;
  const isInfillRunning = activeInfillJob?.status === "RUNNING" || activeInfillJob?.status === "QUEUED";

  return (
    <div className="page-container">
      {/* Page Header */}
      <div style={styles.header}>
        <div>
          <h1 style={styles.title}>自适应代理模型与主动学习</h1>
          <p style={styles.subtitle}>
            使用试验设计模块输出的真实 FEM 阻尼器响应数据集训练 GPR/Kriging/RBF/Response Surface/SVR/PCE/MARS
          </p>
        </div>
      </div>

      <div style={styles.mainGrid}>
        {/* Left Column: Form & Training details */}
        <div style={{ flex: 3, display: "flex", flexDirection: "column", gap: 16 }}>
          {/* Training Panel */}
          <div className="panel">
            <form onSubmit={handleTrainSubmit}>
              <div className="panel-header">
                <span style={styles.panelTitle}>
                  <Brain size={16} color="var(--primary-color)" />
                  <span>代理模型训练参数配置</span>
                </span>
              </div>

              {jobError && <ErrorPanel message={jobError} />}
              {!IS_MOCK_MODE && (
                <div style={{ padding: 10, marginBottom: 12, border: "1px solid var(--warning-color)", borderRadius: 6, color: "var(--warning-color)" }}>
                  生产模式尚未开放独立代理模型训练与主动学习，请从 Momo 对话发起受控优化工作流。
                </div>
              )}

              <div style={styles.formRow}>
                <div className="form-group" style={{ flex: 2 }}>
                  <label className="form-label">训练数据来源</label>
                  <select
                    className="form-control"
                    value={trainForm.datasetSourceMode}
                    onChange={e => setTrainForm({ ...trainForm, datasetSourceMode: e.target.value as SurrogateDatasetSourceMode })}
                  >
                    <option value="DOE_DATASET">试验设计模块输出数据集</option>
                    <option value="USER_IMPORTED_ARTIFACT">用户导入数据 Artifact</option>
                  </select>
                </div>
                <div className="form-group" style={{ flex: 1 }}>
                  <label className="form-label">交叉验证折数</label>
                  <input
                    type="number"
                    className="form-control"
                    value={trainForm.folds}
                    onChange={e => setTrainForm({ ...trainForm, folds: parseInt(e.target.value) || 5 })}
                  />
                </div>
              </div>

              {trainForm.datasetSourceMode === "DOE_DATASET" ? (
                <div className="form-group" style={{ marginBottom: 12 }}>
                  <label className="form-label">DOE 训练样本数据集选择</label>
                  <select
                    className="form-control"
                    value={trainForm.datasetId}
                    onChange={e => setTrainForm({ ...trainForm, datasetId: e.target.value })}
                    required
                  >
                    <option value="">请先运行试验设计并选择已登记 RAW_DATA 制品</option>
                    {trainingDatasetArtifacts.map(artifact => (
                      <option key={artifact.artifactId} value={artifact.artifactId}>
                        {artifact.name} ({artifact.artifactId})
                      </option>
                    ))}
                  </select>
                </div>
              ) : (
                <div style={styles.importBox}>
                  <div className="form-group" style={{ flex: 1 }}>
                    <label className="form-label">用户导入数据 Artifact ID</label>
                    <input
                      className="form-control"
                      value={trainForm.importedDatasetArtifactId}
                      placeholder="输入已登记的 RAW_DATA / CSV artifactId"
                      onChange={e => setTrainForm({ ...trainForm, importedDatasetArtifactId: e.target.value })}
                      required={trainForm.datasetSourceMode === "USER_IMPORTED_ARTIFACT"}
                    />
                  </div>
                  <div className="form-group" style={{ flex: 2 }}>
                    <label className="form-label">输入参数列与响应列映射 JSON</label>
                    <textarea
                      className="form-control"
                      style={styles.schemaTextarea}
                      value={trainForm.importedDatasetSchemaText}
                      onChange={e => setTrainForm({ ...trainForm, importedDatasetSchemaText: e.target.value })}
                    />
                  </div>
                </div>
              )}

              <div style={styles.formRow}>
                {/* Model Families */}
                <div className="form-group" style={{ flex: 1 }}>
                  <label className="form-label">代理拟合模型族选择 (Model Families)</label>
                  <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                    {SURROGATE_MODEL_OPTIONS.map(option => (
                      <label key={option.id} style={{ display: "flex", alignItems: "flex-start", gap: 6 }}>
                        <input
                          type="checkbox"
                          checked={trainForm.modelFamilies.includes(option.id)}
                          onChange={e => handleModelChange(option.id, e.target.checked)}
                        />
                        <span>
                          <span style={{ display: "block" }}>{option.label}</span>
                          <span style={{ display: "block", color: "var(--text-muted)", fontSize: "11px" }}>{option.note}</span>
                        </span>
                      </label>
                    ))}
                  </div>
                </div>

                {/* Targets */}
                <div className="form-group" style={{ flex: 1 }}>
                  <label className="form-label">拟合响应目标（来自结果提取配置）</label>
                  <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                    {responseMetricOptions.map(target => (
                      <label key={target.id} style={{ display: "flex", alignItems: "center", gap: 6 }}>
                        <input
                          type="checkbox"
                          checked={trainForm.targetMetricIds.includes(target.id)}
                          onChange={e => handleTargetChange(target.id, e.target.checked)}
                        />
                        <span>{target.name}</span>
                        <code style={styles.code}>
                          {getResultObjectTypeLabel(target.objectType)} / {getResultResponseTypeLabel(target.responseType)} / {getResultStatisticLabel(target.statistic)}
                        </code>
                      </label>
                    ))}
                  </div>
                </div>
              </div>

              <button type="submit" disabled={isRunning || !canSubmitTraining || !trainingCapability.available} className="btn btn-primary" style={{ marginTop: 12 }}>
                <span>{IS_MOCK_MODE ? "开始模拟训练代理模型" : trainingCapability.available ? "开始训练代理模型" : "请使用受控智能体工作流"}</span>
              </button>
            </form>
          </div>

          {/* Active Learning Infill Panel */}
          <div className="panel">
            <form onSubmit={handleInfillSubmit}>
              <div className="panel-header">
                <span style={styles.panelTitle}>
                  <Sliders size={16} color="var(--primary-color)" />
                  <span>主动学习加点策略与 FEM 复核 (Active Learning Infill)</span>
                </span>
              </div>

              <div style={styles.formRow}>
                <div className="form-group" style={{ flex: 1 }}>
                  <label className="form-label">主动学习开关</label>
                  <button
                    type="button"
                    className="btn btn-secondary"
                    style={{ justifyContent: "center", width: "100%" }}
                    onClick={() => setInfillForm({ ...infillForm, activeLearningEnabled: !infillForm.activeLearningEnabled })}
                  >
                    {infillForm.activeLearningEnabled ? <ToggleRight size={16} /> : <ToggleLeft size={16} />}
                    <span>{infillForm.activeLearningEnabled ? "已开启" : "已关闭"}</span>
                  </button>
                </div>

                <div className="form-group" style={{ flex: 1 }}>
                  <label className="form-label">选用训练基准模型 ID</label>
                  <input
                    className="form-control"
                    value={infillForm.surrogateRunId}
                    onChange={e => setInfillForm({ ...infillForm, surrogateRunId: e.target.value })}
                    required
                    disabled={!infillForm.activeLearningEnabled}
                  />
                </div>

                <div className="form-group" style={{ flex: 1 }}>
                  <label className="form-label">不确定度加点策略 (Infill Strategy)</label>
                  <select
                    className="form-control"
                    value={infillForm.strategy}
                    onChange={e => setInfillForm({ ...infillForm, strategy: e.target.value })}
                    disabled={!infillForm.activeLearningEnabled}
                  >
                    <option value="UNCERTAINTY_AND_PARETO">不确定度 + Pareto 膝点混合搜索 (推荐)</option>
                    <option value="MAX_UNCERTAINTY_ONLY">全局最大不确定度单项探测</option>
                  </select>
                </div>

                <div className="form-group" style={{ flex: 1 }}>
                  <label className="form-label">本批加点样本数 (Batch Size)</label>
                  <input
                    type="number"
                    className="form-control"
                    value={infillForm.batchSize}
                    onChange={e => setInfillForm({ ...infillForm, batchSize: parseInt(e.target.value) || 8 })}
                    disabled={!infillForm.activeLearningEnabled}
                  />
                </div>
              </div>

              <div className="form-group">
                <label style={{ display: "flex", alignItems: "center", gap: 6, cursor: "pointer" }}>
                  <input
                    type="checkbox"
                    checked={infillForm.requiresRealFemReview}
                    onChange={e => setInfillForm({ ...infillForm, requiresRealFemReview: e.target.checked })}
                    disabled={!infillForm.activeLearningEnabled}
                  />
                  <span style={{ fontWeight: 600 }}>强制新样本点回到真实求解器 (ANSYS / OpenSeesPy) 校验</span>
                </label>
                <div style={{ fontSize: "11px", color: "var(--text-muted)", marginTop: 4, marginLeft: 20 }}>
                  主动学习关闭时，优化直接使用当前 FEM 样本与代理模型；开启后才会筛选 Infill 点并可排入真实求解器复核队列。
                </div>
              </div>

              <button type="submit" disabled={isInfillRunning || !activeLearningCapability.available} className="btn btn-primary" style={{ marginTop: 12 }}>
                <span>{infillForm.activeLearningEnabled ? "运行主动学习筛选 Infill 点" : "确认跳过主动学习"}</span>
              </button>
            </form>
          </div>
        </div>

        {/* Right Column: Training metrics & FEM queues */}
        <div style={{ flex: 2, display: "flex", flexDirection: "column", gap: 16 }}>
          {/* Training Task status */}
          {currentJobId && (
            <div className="panel">
              <div className="panel-header">
                <span style={{ fontWeight: 600 }}>代理模型拟合进度</span>
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
                    <div style={{ display: "flex", alignItems: "center", gap: 6, color: "var(--success-color)", fontWeight: 500 }}>
                      <CheckCircle size={16} />
                      <span>代理网络逼近拟合完毕。</span>
                    </div>
                  ) : (
                    <span>拟合结束</span>
                  )}
                </div>
              )}
            </div>
          )}

          {/* Model Fitting results */}
          {trainingMetrics.length > 0 && (
            <div className="panel">
              <div className="panel-header">
                <span style={{ fontWeight: 600 }}>
                  代理拟合校验矩阵{IS_MOCK_MODE ? `（${SURROGATE_SIMULATION_LABEL}）` : ""}
                </span>
              </div>

              <div style={{ overflowX: "auto" }}>
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>模型族</th>
                      <th>响应目标</th>
                      <th>决定系数 R²</th>
                      <th>均方误差 RMSE</th>
                      <th>交叉验证值</th>
                    </tr>
                  </thead>
                  <tbody>
                    {trainingMetrics.map((m, idx) => (
                      <tr key={idx}>
                        <td style={{ fontWeight: 600 }}>{m.model}</td>
                        <td>{m.target}</td>
                        <td style={{ color: "var(--primary-color)", fontWeight: 700 }}>{m.r2.toFixed(3)}</td>
                        <td style={{ fontFamily: "var(--font-mono)" }}>{m.rmse.toFixed(4)}</td>
                        <td style={{ fontFamily: "var(--font-mono)" }}>{m.cvScore.toFixed(3)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {surrogateArtifact && (
                <div style={styles.artCard}>
                  <div>
                    <div style={styles.artName}>{surrogateArtifact.name}</div>
                    <div style={{ fontSize: "11px", color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
                      {surrogateArtifact.path}
                    </div>
                  </div>
                  <a
                    href={resolveApiUrl(surrogateArtifact.downloadUrl)}
                    download
                    className="btn btn-secondary"
                    style={{ padding: "4px 8px", fontSize: "11px" }}
                  >
                    <span>下载模型 (.pkl)</span>
                  </a>
                </div>
              )}
            </div>
          )}

          {/* FEM review queue */}
          <div className="panel" style={{ flex: 1 }}>
            <div className="panel-header">
              <span style={{ fontWeight: 600 }}>
                {IS_MOCK_MODE ? `${SURROGATE_SIMULATION_LABEL} · FEM 样本复核队列` : "FEM 样本复核队列"}
              </span>
              {activeInfillJob ? (
                <StatusBadge status={activeInfillJob.status} />
              ) : IS_MOCK_MODE ? (
                <StatusBadge status="SUCCEEDED" />
              ) : null}
            </div>

            {isInfillRunning ? (
              <div style={{ padding: 20 }}>正在搜索主动学习不确定性膝点...</div>
            ) : infillQueue.length === 0 ? (
              <div style={{ color: "var(--text-muted)", fontSize: "12px", textAlign: "center", padding: 20 }}>
                暂无新样本筛选点，请运行主动学习。
              </div>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {infillQueue.map((item, idx) => (
                  <div key={idx} style={styles.queueCard}>
                    <div>
                      <div style={styles.queueId}>{item.caseId}</div>
                      <div style={styles.queueParams}>
                        <span>参数方案:</span> <code style={styles.code}>{item.params}</code>
                      </div>
                    </div>
                    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      <span className="badge badge-secondary">{item.source}</span>
                      <StatusBadge status={item.status} />
                    </div>
                  </div>
                ))}
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
  importBox: {
    display: "flex",
    gap: "16px",
    backgroundColor: "var(--bg-primary)",
    border: "1px solid var(--border-color)",
    borderRadius: "4px",
    padding: "10px",
    marginBottom: "12px"
  },
  schemaTextarea: {
    minHeight: 108,
    fontFamily: "var(--font-mono)",
    fontSize: "12px"
  },
  code: {
    fontFamily: "var(--font-mono)",
    backgroundColor: "var(--bg-primary)",
    padding: "1px 4px",
    borderRadius: "2px",
    border: "1px solid var(--border-color)",
    fontSize: "11px"
  },
  artCard: {
    backgroundColor: "var(--bg-primary)",
    border: "1px solid var(--border-color)",
    borderRadius: "4px",
    padding: "8px 10px",
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    marginTop: "12px"
  },
  artName: {
    fontWeight: 600,
    fontSize: "12px",
    color: "var(--text-primary)"
  },
  queueCard: {
    backgroundColor: "var(--bg-primary)",
    border: "1px solid var(--border-color)",
    borderRadius: "4px",
    padding: "8px 12px",
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center"
  },
  queueId: {
    fontWeight: 600,
    fontFamily: "var(--font-mono)",
    fontSize: "12px",
    color: "var(--text-primary)"
  },
  queueParams: {
    fontSize: "11px",
    color: "var(--text-muted)",
    marginTop: "2px"
  }
};
export default SurrogatePage;
