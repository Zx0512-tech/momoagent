import React, { useMemo, useState } from "react";
import { api } from "../../api/client";
import { useJobStore } from "../../stores/jobStore";
import { StatusBadge } from "../../components/feedback/StatusBadge";
import { ProgressBar } from "../../components/feedback/ProgressBar";
import { ErrorPanel } from "../../components/feedback/ErrorPanel";
import { ArtifactPreview } from "../../components/artifact/ArtifactPreview";
import type { Artifact, DamperMaterialParameters, DamperMaterialType, ResponseTargetId } from "../../api/types";
import {
  DAMPER_MATERIAL_OPTIONS,
  DEFAULT_DAMPER_CONFIG,
  DEFAULT_DAMPER_CONNECTION_NODE_PAIRS,
  DEFAULT_DAMPER_MATERIALS,
  DEFAULT_OPERATION_TARGETS,
  DEFAULT_SEISMIC_TARGETS,
  RESPONSE_TARGETS,
  getTargetLabel
} from "../../api/engineeringOptions";
import { Check, Code, Eye, FileCode2, Settings2, Shield } from "lucide-react";
import { useManagedIntervals } from "../../hooks/useManagedIntervals";
import { errorMessage } from "../../utils/errors";

const availableModules = [
  { id: "MODEL", label: "MODEL", description: "全桥空间杆系模型、材料、单元和边界约束" },
  { id: "DAMPER", label: "DAMPER", description: "阻尼器单元、布置、参数和造价配置" },
  { id: "LOAD_TRAFFIC", label: "LOAD_TRAFFIC", description: "车辆荷载时程与移动荷载映射" },
  { id: "LOAD_VERTICAL_WIND", label: "LOAD_VERTICAL_WIND", description: "竖向风荷载时程施加" },
  { id: "LOAD_EARTHQUAKE", label: "LOAD_EARTHQUAKE", description: "地震加速度输入与调幅记录" },
  { id: "TRANSIENT", label: "TRANSIENT", description: "瞬态动力积分、阻尼和求解设置" },
  { id: "POSTPROCESS", label: "POSTPROCESS", description: "按目标响应写出原始时程和汇总结果" }
];

const createDefaultDamper = () => ({
  ...DEFAULT_DAMPER_CONFIG,
  material: { ...DEFAULT_DAMPER_CONFIG.material },
  placement: {
    ...DEFAULT_DAMPER_CONFIG.placement,
    connectionNodePairIds: [...DEFAULT_DAMPER_CONFIG.placement.connectionNodePairIds]
  }
});

const formatDamperMaterialParameters = (material: DamperMaterialParameters) => {
  if (material.materialType === "VISCOUS") {
    return `c=${material.dampingCoefficient} alpha=${material.velocityExponent} unitCost=${material.unitCost}`;
  }
  if (material.materialType === "EDDY_CURRENT") {
    return `maxForce=${material.maxOutputForce} criticalVelocity=${material.criticalVelocity} unitCost=${material.unitCost}`;
  }
  return `maxForce=${material.maxOutputForce} unitCost=${material.unitCost}`;
};

export const CommandStreamPage: React.FC = () => {
  const { submitJob, activeJobs, error: jobError, clearError, reportError } = useJobStore();
  const { setManagedInterval, clearManagedInterval } = useManagedIntervals();
  const [currentJobId, setCurrentJobId] = useState<string | null>(null);
  const [generatedArtifact, setGeneratedArtifact] = useState<Artifact | null>(null);
  const [selectedArtifact, setSelectedArtifact] = useState<Artifact | null>(null);

  const [form, setForm] = useState({
    solver: "ANSYS",
    bridgeId: "stbridge",
    caseSetId: "cases_20260701_0001",
    modules: ["MODEL", "DAMPER", "LOAD_EARTHQUAKE", "TRANSIENT", "POSTPROCESS"],
    damper: createDefaultDamper(),
    responseTargets: [...DEFAULT_SEISMIC_TARGETS, ...DEFAULT_OPERATION_TARGETS] as ResponseTargetId[]
  });

  const activeJob = currentJobId ? activeJobs[currentJobId] : null;
  const isRunning = activeJob?.status === "RUNNING" || activeJob?.status === "QUEUED";
  const canSubmit = form.modules.length > 0 && form.responseTargets.length > 0;

  const previewLines = useMemo(() => {
    const targets = form.responseTargets.map(getTargetLabel).join(", ");
    return [
      `# solver=${form.solver} bridge=${form.bridgeId} caseSet=${form.caseSetId}`,
      `MODULES ${form.modules.join(" -> ")}`,
      form.modules.includes("DAMPER")
        ? `DAMPER element=${form.damper.elementType} material=${form.damper.material.materialType} layout=${form.damper.placement.layoutId} southCount=${form.damper.placement.southTowerCount} northCount=${form.damper.placement.northTowerCount} nodes=${form.damper.placement.connectionNodePairIds.join(",")} ${formatDamperMaterialParameters(form.damper.material)}`
        : "DAMPER disabled",
      `POSTPROCESS targets=${targets}`,
      "WRITE summary.json timeseries.csv objectives.csv command_stream.sha256"
    ];
  }, [form]);

  const handleModuleChange = (module: string, checked: boolean) => {
    setForm(prev => ({
      ...prev,
      modules: checked ? [...prev.modules, module] : prev.modules.filter(item => item !== module)
    }));
  };

  const handleTargetChange = (target: ResponseTargetId, checked: boolean) => {
    setForm(prev => ({
      ...prev,
      responseTargets: checked ? [...prev.responseTargets, target] : prev.responseTargets.filter(item => item !== target)
    }));
  };

  const handleDamperMaterialChange = (materialType: DamperMaterialType) => {
    setForm(prev => ({
      ...prev,
      damper: {
        ...prev.damper,
        material: { ...DEFAULT_DAMPER_MATERIALS[materialType] }
      }
    }));
  };

  const updateDamperMaterial = (patch: Partial<DamperMaterialParameters>) => {
    setForm(prev => ({
      ...prev,
      damper: {
        ...prev.damper,
        material: { ...prev.damper.material, ...patch } as DamperMaterialParameters
      }
    }));
  };

  const handleConnectionNodeChange = (pairId: string, checked: boolean) => {
    setForm(prev => ({
      ...prev,
      damper: {
        ...prev.damper,
        placement: {
          ...prev.damper.placement,
          connectionNodePairIds: checked
            ? [...prev.damper.placement.connectionNodePairIds, pairId]
            : prev.damper.placement.connectionNodePairIds.filter(id => id !== pairId)
        }
      }
    }));
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    clearError();
    setGeneratedArtifact(null);
    try {
      const job = await submitJob("COMMAND_STREAM_ASSEMBLY", {
        solver: form.solver,
        bridgeId: form.bridgeId,
        caseSetId: form.caseSetId,
        moduleConfig: {
          modules: form.modules,
          damper: form.damper,
          responseTargets: form.responseTargets
        }
      });
      setCurrentJobId(job.jobId);

      const check = setManagedInterval(async () => {
        try {
          const updated = await api.getJob(job.jobId);
          if (updated.status === "SUCCEEDED") {
            clearManagedInterval(check);
            const arts = await api.getArtifacts({ jobId: job.jobId });
            const commandStream = arts.data.find(artifact => artifact.kind === "COMMAND_STREAM") || arts.data[0];
            if (commandStream) setGeneratedArtifact(commandStream);
          } else if (updated.status === "FAILED" || updated.status === "CANCELLED") {
            clearManagedInterval(check);
          }
        } catch (error) {
          clearManagedInterval(check);
          reportError(errorMessage(error, "读取命令流任务状态失败"));
        }
      }, 1500);
    } catch (error) {
      reportError(errorMessage(error, "启动命令流组装失败"));
    }
  };

  return (
    <div className="page-container">
      <div style={styles.header}>
        <div>
          <h1 style={styles.title}>有限元命令流模块化组装</h1>
          <p style={styles.subtitle}>配置阻尼器、荷载模块和响应提取清单，并预览可审计命令流</p>
        </div>
      </div>

      <div style={styles.mainGrid}>
        <div className="panel" style={{ flex: 3 }}>
          <form onSubmit={handleSubmit}>
            <div className="panel-header">
              <span style={styles.panelTitle}>
                <Code size={16} color="var(--primary-color)" />
                <span>组装配置</span>
              </span>
            </div>

            {jobError && <ErrorPanel message={jobError} />}

            <div style={styles.formRow}>
              <Field label="目标有限元求解器">
                <select className="form-control" value={form.solver} onChange={e => setForm({ ...form, solver: e.target.value })}>
                  <option value="ANSYS">ANSYS (MAPDL / APDL)</option>
                  <option value="OPENSEES">OpenSees (Tcl)</option>
                  <option value="OPENSEESPY_INPROC">OpenSeesPy (Python)</option>
                </select>
              </Field>
              <Field label="桥梁 ID">
                <select className="form-control" value={form.bridgeId} onChange={e => setForm({ ...form, bridgeId: e.target.value })}>
                  <option value="stbridge">STbridge</option>
                </select>
              </Field>
              <Field label="关联工况集 ID">
                <input className="form-control" value={form.caseSetId} onChange={e => setForm({ ...form, caseSetId: e.target.value })} />
              </Field>
            </div>

            <div style={styles.safetyCard}>
              <Shield size={16} color="var(--primary-color)" />
              <span>前端只提交白名单模块和结构化配置，不允许输入任意 APDL/Tcl/Python 命令文本。</span>
            </div>

            <SectionTitle title="模块清单" />
            <div style={styles.moduleGrid}>
              {availableModules.map(module => (
                <label key={module.id} style={styles.moduleCard}>
                  <input type="checkbox" checked={form.modules.includes(module.id)} onChange={e => handleModuleChange(module.id, e.target.checked)} />
                  <div>
                    <div style={styles.moduleName}>{module.label}</div>
                    <div style={styles.moduleDesc}>{module.description}</div>
                  </div>
                </label>
              ))}
            </div>

            <SectionTitle title="阻尼器选型与参数" />
            <div style={styles.formRow}>
              <Field label="阻尼器类型">
                <select className="form-control" value={form.damper.material.materialType} onChange={e => handleDamperMaterialChange(e.target.value as DamperMaterialType)}>
                  {DAMPER_MATERIAL_OPTIONS.map(option => (
                    <option key={option.id} value={option.id}>{option.label}</option>
                  ))}
                </select>
              </Field>
              <Field label="布置方案">
                <select className="form-control" value={form.damper.placement.layoutId} onChange={e => setForm({ ...form, damper: { ...form.damper, placement: { ...form.damper.placement, layoutId: e.target.value } } })}>
                  <option value="tower_girder_end_pair">塔梁端连接布置</option>
                  <option value="tower_girder_multi_pair">塔梁多点连接布置</option>
                </select>
              </Field>
              <Field label="南塔布设数量">
                <input type="number" min="0" className="form-control" value={form.damper.placement.southTowerCount} onChange={e => setForm({ ...form, damper: { ...form.damper, placement: { ...form.damper.placement, southTowerCount: parseInt(e.target.value) || 0 } } })} />
              </Field>
              <Field label="北塔布设数量">
                <input type="number" min="0" className="form-control" value={form.damper.placement.northTowerCount} onChange={e => setForm({ ...form, damper: { ...form.damper, placement: { ...form.damper.placement, northTowerCount: parseInt(e.target.value) || 0 } } })} />
              </Field>
            </div>
            <div style={styles.formRow}>
              {form.damper.material.materialType === "VISCOUS" && (
                <>
                  <Field label="阻尼系数 c">
                    <input type="number" className="form-control" value={form.damper.material.dampingCoefficient} onChange={e => updateDamperMaterial({ dampingCoefficient: parseFloat(e.target.value) || 0 })} />
                  </Field>
                  <Field label="速度指数 alpha">
                    <input type="number" step="0.01" className="form-control" value={form.damper.material.velocityExponent} onChange={e => updateDamperMaterial({ velocityExponent: parseFloat(e.target.value) || 0 })} />
                  </Field>
                </>
              )}
              {form.damper.material.materialType === "EDDY_CURRENT" && (
                <>
                  <Field label="最大出力">
                    <input type="number" className="form-control" value={form.damper.material.maxOutputForce} onChange={e => updateDamperMaterial({ maxOutputForce: parseFloat(e.target.value) || 0 })} />
                  </Field>
                  <Field label="临界速度">
                    <input type="number" step="0.01" className="form-control" value={form.damper.material.criticalVelocity} onChange={e => updateDamperMaterial({ criticalVelocity: parseFloat(e.target.value) || 0 })} />
                  </Field>
                </>
              )}
              {form.damper.material.materialType === "FRICTION" && (
                <Field label="最大出力">
                  <input type="number" className="form-control" value={form.damper.material.maxOutputForce} onChange={e => updateDamperMaterial({ maxOutputForce: parseFloat(e.target.value) || 0 })} />
                </Field>
              )}
              <Field label="单价">
                <input type="number" className="form-control" value={form.damper.material.unitCost} onChange={e => updateDamperMaterial({ unitCost: parseFloat(e.target.value) || 0 })} />
              </Field>
            </div>

            <SectionTitle title="阻尼器连接节点" />
            <div style={styles.nodeGrid}>
              {DEFAULT_DAMPER_CONNECTION_NODE_PAIRS.map(pair => (
                <label key={pair.id} style={styles.nodeItem}>
                  <input type="checkbox" checked={form.damper.placement.connectionNodePairIds.includes(pair.id)} onChange={e => handleConnectionNodeChange(pair.id, e.target.checked)} />
                  <span>{pair.label}</span>
                  <code style={styles.unit}>{pair.nodeI}-{pair.nodeJ}</code>
                </label>
              ))}
            </div>

            <SectionTitle title="响应提取清单" />
            <div style={styles.targetGrid}>
              {RESPONSE_TARGETS.map(target => (
                <label key={target.id} style={styles.targetItem}>
                  <input type="checkbox" checked={form.responseTargets.includes(target.id)} onChange={e => handleTargetChange(target.id, e.target.checked)} />
                  <span>{target.label}</span>
                  <code style={styles.unit}>{target.unit}</code>
                </label>
              ))}
            </div>

            <button type="submit" disabled={isRunning || !canSubmit} className="btn btn-primary" style={{ marginTop: 16 }}>
              <Settings2 size={14} />
              <span>启动命令流组装任务</span>
            </button>
          </form>
        </div>

        <div style={{ flex: 2, display: "flex", flexDirection: "column", gap: 16 }}>
          {currentJobId && (
            <div className="panel">
              <div className="panel-header">
                <span style={{ fontWeight: 600 }}>组装作业进度</span>
                <StatusBadge status={activeJob?.status || "QUEUED"} />
              </div>
              {isRunning ? (
                <ProgressBar percent={activeJob?.progress?.percent} phase={activeJob?.progress?.phase} message={activeJob?.progress?.message} />
              ) : (
                <div style={styles.doneRow}>
                  <Check size={16} color="var(--success-color)" />
                  <span>{activeJob?.status === "SUCCEEDED" ? "组装任务已成功完成" : "组装任务已结束"}</span>
                </div>
              )}
            </div>
          )}

          <div className="panel">
            <div className="panel-header">
              <span style={{ fontWeight: 600 }}>命令流摘要预览</span>
            </div>
            <pre style={styles.previewBlock}>{previewLines.map((line, index) => `${String(index + 1).padStart(2, "0")}  ${line}`).join("\n")}</pre>
          </div>

          <div className="panel" style={{ flex: 1 }}>
            <div className="panel-header">
              <span style={{ fontWeight: 600 }}>COMMAND_STREAM 制品</span>
            </div>
            {generatedArtifact ? (
              <div style={styles.resultCard}>
                <div style={styles.resultHeader}>
                  <FileCode2 size={24} color="var(--primary-color)" />
                  <div>
                    <div style={styles.fileName}>{generatedArtifact.name}</div>
                    <div style={styles.fileSize}>文件大小: {formatBytes(generatedArtifact.sizeBytes)}</div>
                  </div>
                </div>
                <div style={styles.auditBlock}>
                  <div style={styles.auditLabel}>SHA256</div>
                  <div style={styles.auditHash}>{generatedArtifact.sha256 || "未核签"}</div>
                </div>
                {generatedArtifact.canPreview && (
                  <button onClick={() => setSelectedArtifact(generatedArtifact)} className="btn btn-primary">
                    <Eye size={14} />
                    <span>预览组装好的命令流</span>
                  </button>
                )}
              </div>
            ) : (
              <div style={styles.empty}>组装成功后将在此展示可预览、可下载、带哈希的命令流文件。</div>
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

const formatBytes = (bytes?: number) => {
  if (bytes === undefined) return "未知大小";
  if (bytes === 0) return "0 B";
  return (bytes / 1024).toFixed(1) + " KB";
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
  safetyCard: {
    backgroundColor: "var(--info-soft)",
    border: "1px solid var(--info-color)",
    borderRadius: "4px",
    padding: "10px 12px",
    margin: "12px 0",
    display: "flex",
    alignItems: "center",
    gap: 10,
    fontSize: "12px",
    color: "var(--text-secondary)"
  },
  sectionTitle: {
    fontSize: "12px",
    fontWeight: 700,
    color: "var(--text-secondary)",
    margin: "16px 0 8px"
  },
  moduleGrid: {
    display: "grid",
    gridTemplateColumns: "repeat(2, minmax(0, 1fr))",
    gap: 8
  },
  moduleCard: {
    display: "flex",
    alignItems: "flex-start",
    gap: 8,
    backgroundColor: "var(--bg-primary)",
    border: "1px solid var(--border-color)",
    borderRadius: "4px",
    padding: "9px 10px",
    cursor: "pointer"
  },
  moduleName: {
    fontFamily: "var(--font-mono)",
    fontWeight: 700,
    color: "var(--text-primary)",
    fontSize: "12px"
  },
  moduleDesc: {
    fontSize: "11px",
    color: "var(--text-muted)",
    marginTop: 2
  },
  targetGrid: {
    display: "grid",
    gridTemplateColumns: "repeat(2, minmax(0, 1fr))",
    gap: 8
  },
  nodeGrid: {
    display: "grid",
    gridTemplateColumns: "repeat(2, minmax(0, 1fr))",
    gap: 8
  },
  nodeItem: {
    display: "flex",
    alignItems: "center",
    gap: 8,
    backgroundColor: "var(--bg-primary)",
    border: "1px solid var(--border-color)",
    borderRadius: "4px",
    padding: "8px 10px",
    fontSize: "12px"
  },
  targetItem: {
    display: "flex",
    alignItems: "center",
    gap: 8,
    backgroundColor: "var(--bg-primary)",
    border: "1px solid var(--border-color)",
    borderRadius: "4px",
    padding: "8px 10px",
    fontSize: "12px"
  },
  unit: {
    marginLeft: "auto",
    color: "var(--text-muted)"
  },
  doneRow: {
    display: "flex",
    alignItems: "center",
    gap: 8,
    padding: "8px 0",
    color: "var(--success-color)",
    fontWeight: 600,
    fontSize: "12px"
  },
  previewBlock: {
    margin: 0,
    padding: "12px",
    backgroundColor: "var(--bg-primary)",
    border: "1px solid var(--border-color)",
    borderRadius: "4px",
    color: "var(--text-primary)",
    fontFamily: "var(--font-mono)",
    fontSize: "11px",
    lineHeight: 1.7,
    whiteSpace: "pre-wrap"
  },
  resultCard: {
    display: "flex",
    flexDirection: "column",
    gap: 16
  },
  resultHeader: {
    display: "flex",
    alignItems: "center",
    gap: 12
  },
  fileName: {
    fontWeight: 600,
    fontSize: "13px",
    color: "var(--text-primary)"
  },
  fileSize: {
    fontSize: "11px",
    color: "var(--text-secondary)",
    marginTop: "2px"
  },
  auditBlock: {
    backgroundColor: "var(--bg-primary)",
    border: "1px solid var(--border-color)",
    padding: "10px",
    borderRadius: "4px"
  },
  auditLabel: {
    fontSize: "11px",
    color: "var(--text-muted)",
    fontWeight: 500,
    marginBottom: "4px"
  },
  auditHash: {
    fontFamily: "var(--font-mono)",
    fontSize: "11px",
    color: "var(--primary-color)",
    wordBreak: "break-all"
  },
  empty: {
    color: "var(--text-muted)",
    fontSize: "12px",
    textAlign: "center",
    padding: 30
  }
};

export default CommandStreamPage;
