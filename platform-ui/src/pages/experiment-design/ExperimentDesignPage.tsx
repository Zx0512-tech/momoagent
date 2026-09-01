import React, { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Scatter, ScatterChart, CartesianGrid, XAxis, YAxis, Tooltip, ResponsiveContainer } from "recharts";
import { Beaker, CheckCircle2, Eye, FileSpreadsheet, RotateCcw, Sigma } from "lucide-react";
import { api } from "../../api/client";
import {
  DOE_METHOD_OPTIONS,
  getTargetLabel
} from "../../api/engineeringOptions";
import type { Artifact, ExperimentDesignVariable } from "../../api/types";
import { ArtifactPreview } from "../../components/artifact/ArtifactPreview";
import { ErrorPanel } from "../../components/feedback/ErrorPanel";
import { ProgressBar } from "../../components/feedback/ProgressBar";
import { StatusBadge } from "../../components/feedback/StatusBadge";
import { useEngineeringConfigStore } from "../../stores/engineeringConfigStore";
import { useCapabilityAvailability } from "../../hooks/useCapabilityAvailability";
import { useJobStore } from "../../stores/jobStore";
import { CHART_AXIS, CHART_GRID } from "../../theme/chart";
import { useManagedIntervals } from "../../hooks/useManagedIntervals";
import { errorMessage } from "../../utils/errors";

export const ExperimentDesignPage: React.FC = () => {
  const designCapability = useCapabilityAvailability("EXPERIMENT_DESIGN");
  const navigate = useNavigate();
  const {
    projectConfig,
    globalTaskConfig,
    damperBaseConfig,
    doeConfig,
    solverBatchConfig,
    surrogateLearningConfig,
    updateDoeConfig,
    validateModule
  } = useEngineeringConfigStore();
  const { submitJob, activeJobs, error: jobError, clearError, reportError } = useJobStore();
  const { setManagedInterval, clearManagedInterval } = useManagedIntervals();
  const [currentJobId, setCurrentJobId] = useState<string | null>(null);
  const [generatedArtifacts, setGeneratedArtifacts] = useState<Artifact[]>([]);
  const [selectedArtifact, setSelectedArtifact] = useState<Artifact | null>(null);

  const enabledVariables = doeConfig.variables.filter(variable => variable.enabled);
  const estimatedSamples = useMemo(() => {
    const cornerCount = doeConfig.includeCorners ? Math.pow(2, enabledVariables.length) : 0;
    const centerCount = doeConfig.includeCenter ? 1 : 0;
    return doeConfig.sampleCount + cornerCount + centerCount;
  }, [doeConfig.includeCenter, doeConfig.includeCorners, doeConfig.sampleCount, enabledVariables.length]);

  const samplePreview = useMemo(() => {
    const rows = Array.from({ length: Math.min(8, estimatedSamples) }, (_, rowIndex) => {
      const row: Record<string, number> = { caseNo: rowIndex + 1 };
      enabledVariables.forEach((variable, variableIndex) => {
        const ratio = ((rowIndex + 1) * (variableIndex + 2)) % 9 / 8;
        row[variable.id] = Number((variable.min + (variable.max - variable.min) * ratio).toFixed(4));
      });
      return row;
    });
    return rows;
  }, [enabledVariables, estimatedSamples]);

  const scatterData = samplePreview.map(row => ({
    x: enabledVariables[0] ? row[enabledVariables[0].id] : 0,
    y: enabledVariables[1] ? row[enabledVariables[1].id] : 0,
    caseNo: row.caseNo
  }));

  const activeJob = currentJobId ? activeJobs[currentJobId] : null;
  const isRunning = activeJob?.status === "RUNNING" || activeJob?.status === "QUEUED";

  const updateVariable = (id: ExperimentDesignVariable["id"], patch: Partial<ExperimentDesignVariable>) => {
    updateDoeConfig({
      variables: doeConfig.variables.map(variable => (
        variable.id === id ? { ...variable, ...patch } : variable
      )),
      validated: false
    });
  };

  const monitorJobResult = (jobId: string) => {
    const check = setManagedInterval(async () => {
      try {
        const job = await api.getJob(jobId);
        if (job.status === "SUCCEEDED") {
          clearManagedInterval(check);
          const arts = await api.getArtifacts({ jobId });
          setGeneratedArtifacts(arts.data);
        } else if (job.status === "FAILED" || job.status === "CANCELLED") {
          clearManagedInterval(check);
        }
      } catch (error) {
        clearManagedInterval(check);
        reportError(errorMessage(error, "读取试验设计任务状态失败"));
      }
    }, 1500);
  };

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    clearError();
    setGeneratedArtifacts([]);
    validateModule("DOE");
    try {
      const job = await submitJob("EXPERIMENT_DESIGN", {
        bridgeId: projectConfig.modelFile.fileName,
        designName: `${projectConfig.projectName}_doe`,
        solver: globalTaskConfig.solver,
        caseSetPrefix: "doe_user300",
        scenarioType: globalTaskConfig.scenario === "WIND_TRAFFIC" ? "OPERATION" : globalTaskConfig.scenario,
        executionGoal: globalTaskConfig.executionTarget === "OPTIMIZATION_DECISION"
          ? "OPTIMIZATION_RECOMMENDATION"
          : "BATCH_CALCULATION",
        damper: {
          enabled: damperBaseConfig.enabled,
          elementType: "USER300",
          material: {
            materialType: damperBaseConfig.materialType
          },
          placement: {
            layoutId: "CUSTOM_NODE_PAIRS",
            southTowerCount: damperBaseConfig.southTowerCount,
            northTowerCount: damperBaseConfig.northTowerCount,
            connectionNodePairIds: damperBaseConfig.connectionNodePairs.map(item => item.id),
            connectionNodePairs: damperBaseConfig.connectionNodePairs.map(item => ({
              id: item.id,
              tower: item.tower,
              nodeI: item.nodeI,
              nodeJ: item.nodeJ
            }))
          }
        },
        variables: enabledVariables,
        sampling: {
          lhsSamples: doeConfig.sampleCount,
          includeCorners: doeConfig.includeCorners,
          includeCenter: doeConfig.includeCenter,
          seed: doeConfig.seed
        },
        responseTargets: surrogateLearningConfig.outputResponseIds,
        resources: {
          processCount: solverBatchConfig.processCount,
          coresPerProcess: solverBatchConfig.coresPerProcess,
          executionTimeoutS: solverBatchConfig.caseTimeoutS
        }
      });
      setCurrentJobId(job.jobId);
      monitorJobResult(job.jobId);
    } catch (error) {
      reportError(errorMessage(error, "启动试验设计失败"));
    }
  };

  return (
    <div className="page-container">
      <div style={styles.header}>
        <div>
          <h1 style={styles.title}>试验设计</h1>
          <p style={styles.subtitle}>只配置 DOE 样本矩阵：方法、变量、上下限、样本数、随机种子和输出 case set。</p>
        </div>
        <div style={styles.headerActions}>
          <button className="btn btn-secondary" onClick={() => navigate("/")}>
            <RotateCcw size={14} />
            <span>返回主面板</span>
          </button>
          <button className="btn btn-primary" onClick={() => validateModule("DOE")}>
            <CheckCircle2 size={14} />
            <span>校验配置</span>
          </button>
        </div>
      </div>

      <div style={styles.mainGrid}>
        <div className="panel" style={{ flex: 3 }}>
          <form onSubmit={handleSubmit}>
            <div className="panel-header">
              <span style={styles.panelTitle}>
                <Beaker size={16} color="var(--primary-color)" />
                <span>DOE 样本设计</span>
              </span>
              <span className={doeConfig.validated ? "badge badge-success" : "badge badge-warning"}>
                {doeConfig.validated ? "已校验" : "未校验"}
              </span>
            </div>

            {jobError && <ErrorPanel message={jobError} />}

            <div style={styles.formGrid}>
              <Field label="DOE 方法">
                <select
                  className="form-control"
                  value={doeConfig.method}
                  onChange={event => updateDoeConfig({ method: event.target.value as typeof doeConfig.method, validated: false })}
                >
                  {DOE_METHOD_OPTIONS.map(option => (
                    <option key={option.id} value={option.id}>{option.label}</option>
                  ))}
                </select>
              </Field>
              <Field label="基础样本数量">
                <input
                  className="form-control"
                  type="number"
                  min={1}
                  value={doeConfig.sampleCount}
                  onChange={event => updateDoeConfig({ sampleCount: Number(event.target.value), validated: false })}
                />
              </Field>
              <Field label="随机种子">
                <input
                  className="form-control"
                  type="number"
                  value={doeConfig.seed}
                  onChange={event => updateDoeConfig({ seed: Number(event.target.value), validated: false })}
                />
              </Field>
            </div>

            <div style={styles.checkRow}>
              <label style={styles.checkItem}>
                <input
                  type="checkbox"
                  checked={doeConfig.includeCorners}
                  onChange={event => updateDoeConfig({ includeCorners: event.target.checked, validated: false })}
                />
                <span>包含角点样本</span>
              </label>
              <label style={styles.checkItem}>
                <input
                  type="checkbox"
                  checked={doeConfig.includeCenter}
                  onChange={event => updateDoeConfig({ includeCenter: event.target.checked, validated: false })}
                />
                <span>包含中心点样本</span>
              </label>
            </div>

            <SectionTitle title="设计变量上下界" />
            <table className="data-table">
              <thead>
                <tr>
                  <th>启用</th>
                  <th>变量</th>
                  <th>下界</th>
                  <th>上界</th>
                  <th>单位</th>
                </tr>
              </thead>
              <tbody>
                {doeConfig.variables.map(variable => (
                  <tr key={variable.id}>
                    <td>
                      <input
                        type="checkbox"
                        checked={variable.enabled}
                        onChange={event => updateVariable(variable.id, { enabled: event.target.checked })}
                      />
                    </td>
                    <td>{variable.label}</td>
                    <td>
                      <input
                        className="form-control"
                        type="number"
                        step="0.01"
                        value={variable.min}
                        onChange={event => updateVariable(variable.id, { min: Number(event.target.value) })}
                      />
                    </td>
                    <td>
                      <input
                        className="form-control"
                        type="number"
                        step="0.01"
                        value={variable.max}
                        onChange={event => updateVariable(variable.id, { max: Number(event.target.value) })}
                      />
                    </td>
                    <td><code style={styles.code}>{variable.unit}</code></td>
                  </tr>
                ))}
              </tbody>
            </table>

            <button
              type="submit"
              disabled={isRunning || enabledVariables.length === 0 || !designCapability.available}
              className="btn btn-primary"
              style={{ marginTop: 16 }}
            >
              <FileSpreadsheet size={14} />
              <span>生成 DOE 设计矩阵与 case set</span>
            </button>
          </form>
        </div>

        <div style={styles.sideColumn}>
          <div className="panel">
            <div className="panel-header">
              <span style={styles.panelTitle}>
                <Sigma size={16} color="var(--primary-color)" />
                <span>样本量估算</span>
              </span>
            </div>
            <div style={styles.estimateNumber}>{estimatedSamples}</div>
            <div style={styles.estimateGrid}>
              <span>启用变量</span><strong>{enabledVariables.length}</strong>
              <span>基础样本</span><strong>{doeConfig.sampleCount}</strong>
              <span>角点</span><strong>{doeConfig.includeCorners ? Math.pow(2, enabledVariables.length) : 0}</strong>
              <span>中心点</span><strong>{doeConfig.includeCenter ? 1 : 0}</strong>
            </div>
          </div>

          <div className="panel">
            <div className="panel-header">
              <span style={{ fontWeight: 600 }}>样本矩阵预览</span>
            </div>
            <div style={{ overflowX: "auto" }}>
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Case</th>
                    {enabledVariables.map(variable => <th key={variable.id}>{variable.label}</th>)}
                  </tr>
                </thead>
                <tbody>
                  {samplePreview.map(row => (
                    <tr key={row.caseNo}>
                      <td>{row.caseNo}</td>
                      {enabledVariables.map(variable => (
                        <td key={variable.id}>{row[variable.id]}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div className="panel">
            <div className="panel-header">
              <span style={{ fontWeight: 600 }}>参数散点图</span>
            </div>
            <div style={styles.chartBox}>
              <ResponsiveContainer width="100%" height={220}>
                <ScatterChart margin={{ top: 10, right: 15, bottom: 15, left: 5 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke={CHART_GRID} />
                  <XAxis dataKey="x" name={enabledVariables[0]?.label ?? "变量 1"} stroke={CHART_AXIS} />
                  <YAxis dataKey="y" name={enabledVariables[1]?.label ?? "变量 2"} stroke={CHART_AXIS} />
                  <Tooltip cursor={{ strokeDasharray: "3 3" }} />
                  <Scatter data={scatterData} fill="var(--primary-color)" />
                </ScatterChart>
              </ResponsiveContainer>
            </div>
          </div>

          <div className="panel">
            <div className="panel-header">
              <span style={{ fontWeight: 600 }}>输出响应对齐</span>
            </div>
            <div style={styles.targetList}>
              {surrogateLearningConfig.outputResponseIds.map(id => (
                <span key={id} className="badge badge-secondary">{getTargetLabel(id)}</span>
              ))}
            </div>
          </div>

          {currentJobId && (
            <div className="panel">
              <div className="panel-header">
                <span style={{ fontWeight: 600 }}>DOE 作业状态</span>
                <StatusBadge status={activeJob?.status || "QUEUED"} />
              </div>
              {isRunning ? (
                <ProgressBar
                  percent={activeJob?.progress?.percent}
                  phase={activeJob?.progress?.phase}
                  message={activeJob?.progress?.message}
                />
              ) : (
                <div style={styles.doneRow}>作业已结束</div>
              )}
            </div>
          )}

          <div className="panel">
            <div className="panel-header">
              <span style={{ fontWeight: 600 }}>DOE 输出</span>
            </div>
            {generatedArtifacts.length === 0 ? (
              <div style={styles.empty}>任务完成后显示 DOE 设计矩阵、case set 和原始响应数据。</div>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {generatedArtifacts.map(artifact => (
                  <div key={artifact.artifactId} style={styles.artifactRow}>
                    <div>
                      <div style={styles.artifactName}>{artifact.name}</div>
                      <div style={styles.artifactPath}>{artifact.path}</div>
                    </div>
                    {artifact.canPreview && (
                      <button className="btn btn-secondary" style={{ padding: "4px 8px" }} onClick={() => setSelectedArtifact(artifact)}>
                        <Eye size={14} />
                      </button>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>

      {selectedArtifact && <ArtifactPreview artifact={selectedArtifact} onClose={() => setSelectedArtifact(null)} />}
    </div>
  );
};

const Field: React.FC<{ label: string; children: React.ReactNode }> = ({ label, children }) => (
  <div className="form-group" style={{ flex: 1 }}>
    <label className="form-label">{label}</label>
    {children}
  </div>
);

const SectionTitle: React.FC<{ title: string }> = ({ title }) => <div style={styles.sectionTitle}>{title}</div>;

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
  mainGrid: {
    display: "flex",
    gap: 16
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
  checkRow: {
    display: "flex",
    alignItems: "center",
    gap: 14,
    margin: "12px 0"
  },
  checkItem: {
    display: "flex",
    alignItems: "center",
    gap: 6,
    color: "var(--text-secondary)",
    fontSize: 12
  },
  sectionTitle: {
    fontSize: 12,
    fontWeight: 700,
    color: "var(--text-secondary)",
    margin: "16px 0 8px"
  },
  code: {
    fontFamily: "var(--font-mono)",
    fontSize: 11
  },
  sideColumn: {
    flex: 2,
    display: "flex",
    flexDirection: "column",
    gap: 16
  },
  estimateNumber: {
    fontSize: 34,
    fontWeight: 800,
    color: "var(--primary-color)",
    lineHeight: 1,
    margin: "6px 0 14px"
  },
  estimateGrid: {
    display: "grid",
    gridTemplateColumns: "1fr auto",
    gap: "8px 12px",
    fontSize: 12,
    color: "var(--text-secondary)"
  },
  chartBox: {
    backgroundColor: "var(--bg-primary)",
    border: "1px solid var(--border-color)",
    borderRadius: 4,
    padding: "10px 8px 0 0"
  },
  targetList: {
    display: "flex",
    flexWrap: "wrap",
    gap: 8
  },
  doneRow: {
    color: "var(--text-secondary)",
    fontSize: 12
  },
  empty: {
    color: "var(--text-muted)",
    fontSize: 12,
    textAlign: "center",
    padding: 20
  },
  artifactRow: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    backgroundColor: "var(--bg-primary)",
    border: "1px solid var(--border-color)",
    borderRadius: 4,
    padding: "8px 10px"
  },
  artifactName: {
    fontSize: 12,
    fontWeight: 600,
    color: "var(--text-primary)"
  },
  artifactPath: {
    fontSize: 11,
    color: "var(--text-muted)",
    fontFamily: "var(--font-mono)",
    marginTop: 2
  }
};

export default ExperimentDesignPage;
