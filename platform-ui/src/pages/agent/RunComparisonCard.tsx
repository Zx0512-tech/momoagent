import type { RunComparisonResult } from "../../api/agentApi";

const compatibilityLabels: Record<RunComparisonResult["compatibility"], string> = {
  DIRECT: "同模型同荷载，可直接比较",
  CROSS_SOLVER: "跨求解器一致性验证",
  LIMITED: "条件不足，仅展示登记值",
  NOT_COMPARABLE: "工况不一致，不可直接比较"
};

const formatValue = (value: number | null | undefined) => {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  const absolute = Math.abs(value);
  if ((absolute > 0 && absolute < 0.001) || absolute >= 1_000_000) return value.toExponential(4);
  return Number(value.toPrecision(6)).toString();
};

export const RunComparisonCard = ({ comparison }: { comparison: RunComparisonResult }) => {
  const deltas = new Map(
    comparison.comparisons.flatMap(item =>
      Object.entries(item.metrics).map(([metricId, metric]) => [
        `${item.targetKey}:${metricId}`,
        metric
      ] as const)
    )
  );
  return <div style={styles.card} aria-label="跨运行结果比较">
    <div style={styles.heading}>
      <strong>跨 Run 工程对比</strong>
      <span style={styles.badge}>{compatibilityLabels[comparison.compatibility]}</span>
    </div>
    {comparison.baselineRunId && <p style={styles.meta}>Baseline：<code>{comparison.baselineRunId}</code></p>}
    <div style={styles.scroll}>
      <table style={styles.table}>
        <thead>
          <tr>
            <th style={styles.cell}>Run</th>
            {comparison.metricIds.map(metricId => <th key={metricId} style={styles.cell}>{comparison.runs[0]?.metrics[metricId]?.label ?? metricId}</th>)}
          </tr>
        </thead>
        <tbody>
          {comparison.runs.map(item => <tr key={item.targetKey}>
            <td style={styles.cell}>
              <code>{item.runId}</code>
              {item.caseId && <div style={styles.meta}>case {item.caseId}</div>}
              {item.candidateRank && <div style={styles.meta}>TOPSIS #{item.candidateRank}</div>}
            </td>
            {comparison.metricIds.map(metricId => {
              const metric = item.metrics[metricId];
              const delta = deltas.get(`${item.targetKey}:${metricId}`);
              return <td key={metricId} style={styles.cell}>
                <div>{formatValue(metric?.value)} {metric?.unit ?? ""}</div>
                {delta?.relativeChangePercent !== null && delta?.relativeChangePercent !== undefined && (
                  <div style={styles.meta}>
                    {delta.interpretation === "SOLVER_DIFFERENCE" ? "相对差异" : "相对基线"}：
                    {delta.relativeChangePercent > 0 ? "+" : ""}{formatValue(delta.relativeChangePercent)}%
                  </div>
                )}
              </td>;
            })}
          </tr>)}
        </tbody>
      </table>
    </div>
    {comparison.warnings.length > 0 && <ul style={styles.warnings}>
      {comparison.warnings.map(item => <li key={item}>{item}</li>)}
    </ul>}
    <p style={styles.limit}>{comparison.interpretationLimit}</p>
  </div>;
};

const styles: Record<string, React.CSSProperties> = {
  card: { border: "1px solid var(--border-color)", borderRadius: 8, padding: 12, marginTop: 12 },
  heading: { display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, flexWrap: "wrap" },
  badge: { padding: "4px 8px", borderRadius: 999, background: "var(--bg-tertiary)", color: "var(--primary-color)", fontSize: 12 },
  meta: { color: "var(--text-secondary)", fontSize: 12, margin: "4px 0" },
  scroll: { overflowX: "auto" },
  table: { width: "100%", borderCollapse: "collapse", fontSize: 12, marginTop: 8 },
  cell: { padding: "8px 10px", textAlign: "left", borderBottom: "1px solid var(--border-color)", whiteSpace: "nowrap" },
  warnings: { margin: "10px 0 0", paddingLeft: 20, color: "var(--warning-color)", lineHeight: 1.6 },
  limit: { margin: "10px 0 0", color: "var(--text-secondary)", fontSize: 12 }
};

export default RunComparisonCard;
