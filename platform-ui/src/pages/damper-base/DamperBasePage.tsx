import React, { useState } from "react";
import { useNavigate } from "react-router-dom";
import { CheckCircle2, Plus, RotateCcw, Save, Trash2, Wrench } from "lucide-react";
import { USER300_MATERIAL_OPTIONS } from "../../api/engineeringOptions";
import { api } from "../../api/client";
import type { BridgeTowerId, DamperBaseConfig, DamperConnectionNodePair } from "../../api/types";
import { ErrorPanel } from "../../components/feedback/ErrorPanel";
import { useEngineeringConfigStore } from "../../stores/engineeringConfigStore";

const DIRECTION_OPTIONS: Array<{ id: DamperBaseConfig["direction"]; label: string }> = [
  { id: "UX", label: "UX 全局纵向" },
  { id: "UY", label: "UY 全局横向" },
  { id: "UZ", label: "UZ 全局竖向" },
  { id: "LOCAL_AXIAL", label: "局部轴向" }
];

const connectionLabel = (tower: BridgeTowerId, nodeI: number, nodeJ: number) =>
  `${tower === "SOUTH" ? "南塔" : "北塔"}自定义节点 ${nodeI}-${nodeJ}`;

export const DamperBasePage: React.FC = () => {
  const navigate = useNavigate();
  const {
    projectConfig,
    damperBaseConfig,
    updateDamperBaseConfig,
    validateModule,
    getEngineeringConfig
  } = useEngineeringConfigStore();
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const registry = damperBaseConfig.damperInstanceRegistry;
  const totalCount = damperBaseConfig.southTowerCount + damperBaseConfig.northTowerCount;

  const updateConnectionNodePair = (id: string, patch: Partial<DamperConnectionNodePair>) => {
    const connectionNodePairs = damperBaseConfig.connectionNodePairs.map(pair => {
      if (pair.id !== id) return pair;
      const next = { ...pair, ...patch };
      return { ...next, label: connectionLabel(next.tower, next.nodeI, next.nodeJ) };
    });
    updateDamperBaseConfig({ connectionNodePairs });
  };

  const addConnectionNodePair = () => {
    let sequence = damperBaseConfig.connectionNodePairs.length + 1;
    let id = `custom_connection_${sequence}`;
    while (damperBaseConfig.connectionNodePairs.some(pair => pair.id === id)) {
      sequence += 1;
      id = `custom_connection_${sequence}`;
    }
    const pair: DamperConnectionNodePair = {
      id,
      tower: "SOUTH",
      nodeI: 1,
      nodeJ: 2,
      label: connectionLabel("SOUTH", 1, 2)
    };
    updateDamperBaseConfig({ connectionNodePairs: [...damperBaseConfig.connectionNodePairs, pair] });
  };

  const removeConnectionNodePair = (id: string) => {
    updateDamperBaseConfig({
      connectionNodePairs: damperBaseConfig.connectionNodePairs.filter(pair => pair.id !== id)
    });
  };

  const saveAndValidate = async () => {
    setSaving(true);
    setSaveError(null);
    validateModule("DAMPER_BASE");
    try {
      const config = getEngineeringConfig();
      await api.saveEngineeringConfig(config);
      await api.validateEngineeringConfig(config);
    } catch (error) {
      setSaveError(error instanceof Error ? error.message : "阻尼器配置保存失败");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="page-container">
      <div style={styles.header}>
        <div>
          <h1 style={styles.title}>阻尼器基准配置</h1>
          <p style={styles.subtitle}>
            配置阻尼器类型、作用方向和连接节点；USER300 单元及结果提取项由平台派生。
          </p>
        </div>
        <div style={styles.headerActions}>
          <button className="btn btn-secondary" onClick={() => navigate("/")}>
            <RotateCcw size={14} />
            <span>返回主面板</span>
          </button>
          <button className="btn btn-primary" disabled={saving} onClick={saveAndValidate}>
            <CheckCircle2 size={14} />
            <span>{saving ? "保存中..." : "校验配置"}</span>
          </button>
        </div>
      </div>

      <div style={styles.grid}>
        <div className="panel" style={styles.configPanel}>
          {saveError && <ErrorPanel message={saveError} />}
          <div className="panel-header">
            <span style={styles.panelTitle}>
              <Wrench size={16} color="var(--primary-color)" />
              <span>工程布置意图</span>
            </span>
            <span className="badge badge-secondary">自定义连接节点，不输入单元号</span>
          </div>

          <div style={styles.formGrid}>
            <div className="form-group">
              <label className="form-label">模型文件</label>
              <input className="form-control" value={projectConfig.modelFile.fileName} readOnly />
            </div>

            <div className="form-group">
              <label className="form-label">模型解析状态</label>
              <input
                className="form-control"
                value={projectConfig.modelFile.parseStatus === "PARSED" ? "已解析" : "待解析/未校验"}
                readOnly
              />
            </div>

            <div className="form-group">
              <label className="form-label">阻尼器类型</label>
              <select
                className="form-control"
                value={damperBaseConfig.materialType}
                onChange={event => updateDamperBaseConfig({
                  materialType: event.target.value as DamperBaseConfig["materialType"]
                })}
              >
                {USER300_MATERIAL_OPTIONS.map(item => (
                  <option key={item.id} value={item.id}>{item.label}</option>
                ))}
              </select>
            </div>

            <div className="form-group">
              <label className="form-label">南塔布设数量</label>
              <input className="form-control" value={damperBaseConfig.southTowerCount} readOnly />
            </div>

            <div className="form-group">
              <label className="form-label">北塔布设数量</label>
              <input className="form-control" value={damperBaseConfig.northTowerCount} readOnly />
            </div>

            <div className="form-group">
              <label className="form-label">作用方向</label>
              <select
                className="form-control"
                value={damperBaseConfig.direction}
                onChange={event => updateDamperBaseConfig({
                  direction: event.target.value as DamperBaseConfig["direction"]
                })}
              >
                {DIRECTION_OPTIONS.map(item => (
                  <option key={item.id} value={item.id}>{item.label}</option>
                ))}
              </select>
            </div>

          </div>

          <div style={styles.connectionHeader}>
            <div>
              <div style={styles.sectionTitle}>自定义阻尼器连接节点</div>
              <div style={styles.sectionHint}>每行定义一个 USER300 阻尼器的两个连接节点，节点必须为不同的正整数。</div>
            </div>
            <button type="button" className="btn btn-secondary" onClick={addConnectionNodePair}>
              <Plus size={14} />
              <span>添加节点对</span>
            </button>
          </div>

          <div style={{ overflowX: "auto" }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>所属塔</th>
                  <th>节点 i</th>
                  <th>节点 j</th>
                  <th>连接说明</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {damperBaseConfig.connectionNodePairs.map(pair => (
                  <tr key={pair.id}>
                    <td>
                      <select
                        className="form-control"
                        value={pair.tower}
                        onChange={event => updateConnectionNodePair(pair.id, { tower: event.target.value as BridgeTowerId })}
                      >
                        <option value="SOUTH">南塔</option>
                        <option value="NORTH">北塔</option>
                      </select>
                    </td>
                    <td>
                      <input
                        className="form-control"
                        type="number"
                        min={1}
                        step={1}
                        value={pair.nodeI}
                        onChange={event => updateConnectionNodePair(pair.id, { nodeI: Number(event.target.value) })}
                      />
                    </td>
                    <td>
                      <input
                        className="form-control"
                        type="number"
                        min={1}
                        step={1}
                        value={pair.nodeJ}
                        onChange={event => updateConnectionNodePair(pair.id, { nodeJ: Number(event.target.value) })}
                      />
                    </td>
                    <td>{pair.label}</td>
                    <td>
                      <button type="button" className="btn btn-secondary" onClick={() => removeConnectionNodePair(pair.id)}>
                        <Trash2 size={14} />
                        <span>删除</span>
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div style={styles.notice}>
            当前配置 {totalCount} 个连接节点对。平台按这些节点生成 USER300 注册表；后端保存同一节点契约。
          </div>

          <div style={styles.footerActions}>
            <button className="btn btn-primary" disabled={saving} onClick={saveAndValidate}>
              <Save size={14} />
              <span>{saving ? "保存中..." : "保存并校验"}</span>
            </button>
          </div>
        </div>

        <div className="panel" style={styles.summaryPanel}>
          <div className="panel-header">
            <span style={styles.panelTitle}>阻尼器派生注册表</span>
            <span className={damperBaseConfig.placementValidated ? "badge badge-success" : "badge badge-warning"}>
              {damperBaseConfig.placementValidated ? "已校验" : "未校验"}
            </span>
          </div>

          <div style={{ overflowX: "auto" }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>阻尼器 ID</th>
                  <th>类型</th>
                  <th>布置位置</th>
                  <th>单元</th>
                  <th>节点 i</th>
                  <th>节点 j</th>
                  <th>方向</th>
                  <th>来源</th>
                </tr>
              </thead>
              <tbody>
                {registry.map(item => (
                  <tr key={item.damperId}>
                    <td style={styles.mono}>{item.damperId}</td>
                    <td>{USER300_MATERIAL_OPTIONS.find(option => option.id === item.materialType)?.label}</td>
                    <td>{item.placementLabel}</td>
                    <td style={styles.mono}>USER300:{item.elementId}</td>
                    <td style={styles.mono}>{item.nodeI}</td>
                    <td style={styles.mono}>{item.nodeJ}</td>
                    <td>{item.direction}</td>
                    <td>{item.source === "USER_DEFINED" ? "用户配置" : item.source === "AUTO_GENERATED" ? "平台自动生成" : "模型识别"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
};

const styles: Record<string, React.CSSProperties> = {
  header: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
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
    marginTop: 4
  },
  headerActions: {
    display: "flex",
    gap: 8
  },
  grid: {
    display: "grid",
    gridTemplateColumns: "minmax(360px, 0.9fr) minmax(520px, 1.1fr)",
    gap: 16
  },
  configPanel: {
    minHeight: 420
  },
  summaryPanel: {
    minHeight: 420
  },
  panelTitle: {
    display: "flex",
    alignItems: "center",
    gap: 8,
    fontWeight: 600
  },
  formGrid: {
    display: "grid",
    gridTemplateColumns: "repeat(2, minmax(0, 1fr))",
    gap: 12
  },
  notice: {
    marginTop: 16,
    padding: "10px 12px",
    border: "1px solid var(--border-color)",
    borderRadius: 4,
    backgroundColor: "var(--bg-primary)",
    color: "var(--text-secondary)",
    fontSize: 12
  },
  connectionHeader: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 12,
    marginTop: 18,
    marginBottom: 10
  },
  sectionTitle: {
    fontSize: 13,
    fontWeight: 600,
    color: "var(--text-primary)"
  },
  sectionHint: {
    marginTop: 3,
    fontSize: 12,
    color: "var(--text-secondary)"
  },
  footerActions: {
    display: "flex",
    justifyContent: "flex-end",
    marginTop: 14
  },
  mono: {
    fontFamily: "var(--font-mono)",
    fontSize: 12
  }
};

export default DamperBasePage;
