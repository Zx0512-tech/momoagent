import React from "react";
import { verificationLabel } from "./earthquakeWorkflowStatus";

interface EarthquakeWorkflowOverviewPanelProps {
  overview: any;
}

export const EarthquakeWorkflowOverviewPanel: React.FC<EarthquakeWorkflowOverviewPanelProps> = ({ overview }) => {
  const doeDesigns = asArray(overview?.doeContract?.designs);
  const samples = asArray(overview?.sampleResponses);
  const surrogateRows = surrogateMetricRows(overview?.surrogateMetrics, overview?.surrogateCandidateMetrics);
  const topsis = overview?.topsis ?? {};
  const topsisRanking = asArray(topsis.ranking);
  const objectiveLimits = asArray(overview?.objectiveLimits);
  const candidateFilter = overview?.surrogateCandidateFilter ?? {};
  // 样本响应列按后端 responseTargets 动态生成：风工况只有累计位移一个目标，
  // 硬编码地震三列会让风工况显示三个空列、真正的目标反而不出现。
  // 兜底列表兼容 responseTargets 字段之前产出的旧制品。
  const responseTargets = asArray(overview?.responseTargets).length > 0
    ? asArray(overview?.responseTargets)
    : LEGACY_EARTHQUAKE_RESPONSE_TARGETS;

  return (
    <div style={styles.wrapper}>
      <div style={styles.summaryStrip}>
        <span>solver: {overview?.solver ?? "-"}</span>
        <span>DOE: {overview?.doeContract?.totalSamples ?? "-"} 点</span>
        <span>validation: {verificationLabel(overview?.validationStatus)}</span>
        <span>review: {verificationLabel(overview?.reviewStatus)}</span>
        <span>final: {overview?.finalRecommendationStatus ?? "-"}</span>
        <span>candidate filter: {candidateFilter.accepted_candidate_count ?? "-"} / {candidateFilter.total_candidate_count ?? "-"}</span>
      </div>

      <div style={styles.tableBlock}>
        <div style={styles.blockTitle}>DOE 参数</div>
        <div style={styles.tableFrame}>
          <table className="data-table" style={styles.compactTable}>
            <thead>
              <tr>
                <th>#</th>
                <th>C</th>
                <th>alpha</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>无控</td>
                <td>-</td>
                <td>-</td>
              </tr>
              {doeDesigns.map((design, index) => (
                <tr key={`doe-${index}`}>
                  <td>{index + 1}</td>
                  <td>{formatNumber(design.c, 2)}</td>
                  <td>{formatNumber(design.alpha, 3)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div style={styles.tableBlock}>
        <div style={styles.blockTitle}>样本响应</div>
        <div style={styles.tableFrame}>
          <table className="data-table" style={styles.responseTable}>
            <thead>
              <tr>
                <th>样本</th>
                <th>case</th>
                <th>C</th>
                <th>alpha</th>
                {responseTargets.map(target => (
                  <th key={`target-${target.targetId}`}>{target.label ?? target.targetId}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {samples.length === 0 ? (
                <tr>
                  <td colSpan={4 + responseTargets.length} style={styles.emptyCell}>暂无样本响应</td>
                </tr>
              ) : samples.map((sample, index) => (
                <tr key={`sample-${index}`}>
                  <td>{sample.sampleType === "UNCONTROLLED_BASELINE" ? "无控" : sample.sampleIndex ?? index + 1}</td>
                  <td style={styles.mono}>{sample.caseId ?? "-"}</td>
                  <td>{formatNumber(sample.design?.c, 2)}</td>
                  <td>{formatNumber(sample.design?.alpha, 3)}</td>
                  {responseTargets.map(target => (
                    <td key={`value-${target.targetId}`}>{responseValue(sample, target.targetId)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div style={styles.tableBlock}>
        <div style={styles.blockTitle}>无控基准限值</div>
        <div style={styles.tableFrame}>
          <table className="data-table" style={styles.compactTable}>
            <thead>
              <tr>
                <th>响应</th>
                <th>限值</th>
              </tr>
            </thead>
            <tbody>
              {objectiveLimits.length === 0 ? (
                <tr>
                  <td colSpan={2} style={styles.emptyCell}>暂无无控基准限值</td>
                </tr>
              ) : objectiveLimits.map((item, index) => (
                <tr key={`limit-${index}`}>
                  <td>{item.label ?? item.targetId}</td>
                  <td>{formatEngineeringValue(item)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div style={styles.tableBlock}>
        <div style={styles.blockTitle}>代理模型精度</div>
        <div style={styles.tableFrame}>
          <table className="data-table" style={styles.compactTable}>
            <thead>
              <tr>
                <th>目标</th>
                <th>模型</th>
                <th>状态</th>
                <th>R²</th>
                <th>CV峰值误差</th>
                <th>MAE</th>
                <th>RMSE</th>
              </tr>
            </thead>
            <tbody>
              {surrogateRows.length === 0 ? (
                <tr>
                  <td colSpan={7} style={styles.emptyCell}>暂无代理模型精度</td>
                </tr>
              ) : surrogateRows.map(row => (
                <tr key={`${row.target}-${row.modelName}`}>
                  <td>{row.target}</td>
                  <td>{row.modelName ?? "-"}</td>
                  <td>{row.selected ? "已选" : "候选"}</td>
                  <td>{formatNumber(metricValue(row.metrics, ["r2", "r2_score", "fit_r2", "train_r2"]))}</td>
                  <td>{formatNumber(metricValue(row.cvMetrics, ["peak_relative_error", "mean_relative_error", "relative_error"]))}</td>
                  <td>{formatNumber(metricValue(row.metrics, ["mae", "mean_absolute_error"]))}</td>
                  <td>{formatNumber(metricValue(row.metrics, ["rmse", "root_mean_squared_error"]))}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div style={styles.tableBlock}>
        <div style={styles.blockTitle}>熵权 TOPSIS</div>
        <div style={styles.summaryStrip}>
          <span>method: {topsis.method ?? "-"}</span>
          <span>best index: {topsis.best_index ?? "-"}</span>
          <span>weights: {asArray(topsis.weights).map(value => formatNumber(value, 4)).join(", ") || "-"}</span>
        </div>
        <div style={styles.tableFrame}>
          <table className="data-table" style={styles.compactTable}>
            <thead>
              <tr>
                <th>排名</th>
                <th>Pareto index</th>
                <th>贴近度</th>
              </tr>
            </thead>
            <tbody>
              {topsisRanking.length === 0 ? (
                <tr>
                  <td colSpan={3} style={styles.emptyCell}>暂无 TOPSIS 排序</td>
                </tr>
              ) : topsisRanking.map((paretoIndex, rank) => (
                <tr key={`topsis-${rank}`}>
                  <td>{rank + 1}</td>
                  <td>{paretoIndex}</td>
                  <td>{formatNumber(asArray(topsis.closeness)[Number(paretoIndex)], 4)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
};

const asArray = (value: any): any[] => Array.isArray(value) ? value : [];

// 旧制品（responseTargets 字段之前产出的）没有这个键，退回地震三响应，
// 与该字段引入前的渲染结果一致。风工况总览一定带 responseTargets。
const LEGACY_EARTHQUAKE_RESPONSE_TARGETS = [
  { targetId: "beamEndDisplacement", label: "梁端位移" },
  { targetId: "towerBaseShear", label: "塔底剪力" },
  { targetId: "towerBaseMoment", label: "塔底弯矩" },
];

const surrogateMetricRows = (value: any, candidateMetrics: any) => {
  const selectedByTarget = selectedSurrogateByTarget(value);
  const candidateRows = Object.entries(candidateMetrics ?? {}).flatMap(([target, models]: [string, any]) =>
    Object.entries(models ?? {}).map(([modelName, metrics]: [string, any]) => ({
      target,
      modelName,
      selected: Boolean(metrics?.selected) || selectedByTarget[target] === modelName,
      metrics: metrics?.fit ?? {},
      cvMetrics: metrics?.cross_validation ?? {}
    }))
  );
  if (candidateRows.length > 0) return candidateRows;
  if (Array.isArray(value)) {
    return value.map((selection, index) => ({
      target: selection?.target ?? selection?.objective ?? selection?.response ?? String(index + 1),
      modelName: selection?.model_name ?? selection?.name,
      selected: true,
      metrics: selection?.metrics ?? {},
      cvMetrics: selection?.cross_validation_metrics ?? {}
    }));
  }
  return Object.entries(value ?? {}).map(([target, selection]: [string, any]) => ({
    target,
    modelName: selection?.model_name,
    selected: true,
    metrics: selection?.metrics ?? {},
    cvMetrics: selection?.cross_validation_metrics ?? {}
  }));
};

const selectedSurrogateByTarget = (value: any) => {
  if (Array.isArray(value)) {
    return Object.fromEntries(value.map((selection, index) => [
      selection?.target ?? selection?.objective ?? selection?.response ?? String(index + 1),
      selection?.model_name ?? selection?.name
    ]));
  }
  return Object.fromEntries(
    Object.entries(value ?? {}).map(([target, selection]: [string, any]) => [target, selection?.model_name])
  );
};

const formatNumber = (value: any, maximumFractionDigits = 3) => {
  const numericValue = Number(value);
  if (!Number.isFinite(numericValue)) return value === null || value === undefined ? "-" : String(value);
  return numericValue.toLocaleString("zh-CN", { maximumFractionDigits });
};

const formatEngineeringValue = (value?: { displayValue?: number | null; displayUnit?: string }) => {
  if (!value || value.displayValue === null || value.displayValue === undefined) return "-";
  return `${formatNumber(value.displayValue)} ${value.displayUnit ?? ""}`.trim();
};

const responseValue = (sample: any, targetId: string) =>
  formatEngineeringValue(sample?.responses?.[targetId]);

const metricValue = (metrics: Record<string, any> | undefined, keys: string[]) => {
  if (!metrics) return undefined;
  return keys.map(key => metrics[key]).find(value => value !== undefined && value !== null);
};

const styles: Record<string, React.CSSProperties> = {
  wrapper: {
    display: "grid",
    gridTemplateColumns: "repeat(2, minmax(0, 1fr))",
    gap: 12
  },
  summaryStrip: {
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
  tableFrame: {
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
  },
  emptyCell: {
    color: "var(--text-muted)",
    textAlign: "center",
    padding: "16px 8px"
  },
  mono: {
    fontFamily: "var(--font-mono)"
  }
};
