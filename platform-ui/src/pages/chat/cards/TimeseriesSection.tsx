import type React from "react";
import { useMemo, useState } from "react";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis
} from "recharts";

import {
  agentApi,
  type TimeseriesComparisonCase,
  type TimeseriesComparisonResponse,
  type TimeseriesResponse
} from "../../../api/agentApi";
import { CHART_COLORS } from "../../../theme/chart";
import { cardStyles } from "./cardStyles";
import { buildComparisonChartData, formatComparisonCaseLabel } from "./timeseriesComparison";

const DEFAULT_COLUMNS = ["displacement"];
const LINE_COLORS = CHART_COLORS;

const SERIES_GROUPS: Array<{ label: string; columns: string[] }> = [
  { label: "位移响应", columns: ["displacement", "displacement_increment", "absolute_displacement"] },
  { label: "加速度与塔底内力", columns: ["acceleration", "tower_base_shear", "tower_base_moment"] },
  { label: "阻尼器响应", columns: ["damper_force", "damper_stroke"] },
  { label: "地震输入", columns: ["ground_acceleration", "ground_displacement", "ground_velocity"] }
];

const SERIES_LABELS: Record<string, string> = {
    time: "时间",
    max_girder_end_displacement: "梁端位移",
    max_tower_base_shear: "塔底剪力",
    displacement: "梁端位移",
    displacement_increment: "位移增量",
    absolute_displacement: "绝对位移",
    tower_base_shear: "塔底剪力",
    acceleration: "加速度",
    tower_base_moment: "塔底弯矩",
    damper_force: "阻尼器力",
    damper_stroke: "阻尼器行程",
    ground_displacement: "地面位移",
    ground_velocity: "地面速度",
    ground_acceleration: "地面加速度",
    max_tower_base_moment: "塔底弯矩",
    max_damper_force: "阻尼器最大力",
    max_acceleration: "最大加速度"
};

const SERIES_DESCRIPTIONS: Record<string, string> = {
  displacement: "梁端相对位移",
  displacement_increment: "相邻时刻的位移变化量",
  absolute_displacement: "相对初始位置的绝对位移",
  acceleration: "结构响应加速度",
  tower_base_shear: "塔底剪力",
  tower_base_moment: "塔底弯矩",
  damper_force: "阻尼器输出力",
  damper_stroke: "阻尼器行程",
  ground_displacement: "地震输入位移",
  ground_velocity: "地震输入速度",
  ground_acceleration: "地震输入加速度"
};

function labelForColumn(column: string): string {
  return SERIES_LABELS[column] ?? column.replaceAll("_", " ");
}

function formatTimeTick(value: unknown): string {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric.toFixed(2) : String(value);
}

function columnsWithTime(columns: string[]): string[] {
  return ["time", ...columns.filter(column => column !== "time")];
}

function formatPeak(value: number | null): string {
  return value === null || !Number.isFinite(value) ? "—" : value.toPrecision(4);
}

function formatChartValue(value: unknown): string {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric.toPrecision(4) : "—";
}

function formatPeakTime(value: number | null): string {
  return value === null || !Number.isFinite(value) ? "—" : value.toFixed(2);
}

export const TimeseriesSection = ({ runId, taskType }: { runId: string; taskType?: string }) => {
  const [expanded, setExpanded] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [data, setData] = useState<TimeseriesResponse | null>(null);
  const [comparison, setComparison] = useState<TimeseriesComparisonResponse | null>(null);
  const [selectedColumns, setSelectedColumns] = useState<string[]>(DEFAULT_COLUMNS);
  const [selectedCaseIds, setSelectedCaseIds] = useState<string[]>([]);
  const [caseOptions, setCaseOptions] = useState<TimeseriesComparisonCase[]>([]);

  const load = async (columns: string[], caseIds: string[] = []) => {
    setLoading(true);
    setError(null);
    try {
      const response = await agentApi.getRunTimeseriesComparison(
        runId,
        columnsWithTime(columns),
        caseIds,
        1000
      );
      setComparison(response);
      setData(null);
      setSelectedColumns(response.columns.filter(column => column !== "time"));
      setSelectedCaseIds(response.cases.map(item => item.caseId));
      setCaseOptions(current => current.length > 0 ? current : response.cases);
    } catch (comparisonReason) {
      // 参数扫描必须明确给出多工况比较，不能静默退回单工况曲线。
      if (comparison || taskType === "DAMPER_PARAMETER_SWEEP") {
        setError(comparisonReason instanceof Error ? comparisonReason.message : "多工况时程数据加载失败");
        return;
      }
      try {
        const response = await agentApi.getRunTimeseries(runId, columnsWithTime(columns), 1000);
        setData(response);
        setComparison(null);
        setSelectedColumns(response.columns.filter(column => column !== "time"));
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : "时程数据加载失败");
      }
    } finally {
      setLoading(false);
    }
  };

  const chartData = useMemo(() => {
    if (!data) return [];
    const count = Math.max(...data.columns.map(column => data.series[column]?.length ?? 0), 0);
    return Array.from({ length: count }, (_, index) => {
      const row: Record<string, number | null> = {};
      data.columns.forEach(column => { row[column] = data.series[column]?.[index] ?? null; });
      return row;
    });
  }, [data]);

  const comparisonCharts = useMemo(() => (
    comparison
      ? buildComparisonChartData(
        comparison.cases,
        comparison.columns.filter(column => column !== "time")
      )
      : []
  ), [comparison]);

  const available = comparison?.availableColumns ?? data?.availableColumns ?? [];
  const displayedCases = comparison?.cases ?? [];
  const hasData = Boolean(comparison || data);
  return (
    <section style={{ marginTop: 14 }}>
      <button
        type="button"
        style={cardStyles.disclosureButton}
        onClick={() => {
          const next = !expanded;
          setExpanded(next);
          if (next && !data && !comparison && !loading) void load(DEFAULT_COLUMNS);
        }}
      >
        {expanded ? "收起时程曲线" : "查看时程曲线"}
      </button>
      {expanded && (
        <div style={{ marginTop: 10 }}>
          {loading && <p style={cardStyles.muted}>正在读取已登记的时程 CSV…</p>}
          {error && <p style={cardStyles.warning}>{error}</p>}
          {hasData && (
            <>
              <div style={styles.selector}>
                <span style={styles.timeHint}>横坐标：时间（秒）</span>
                {SERIES_GROUPS.map(group => {
                  const columns = group.columns.filter(column => available.includes(column));
                  if (columns.length === 0) return null;
                  return (
                    <fieldset key={group.label} style={styles.group}>
                      <legend style={styles.groupLabel}>{group.label}</legend>
                      {columns.map(column => (
                        <label key={column} style={styles.option}>
                          <input
                            title={SERIES_DESCRIPTIONS[column]}
                            type="checkbox"
                            checked={selectedColumns.includes(column)}
                            onChange={event => setSelectedColumns(current => (
                              event.target.checked
                                ? [...current, column]
                                : current.filter(item => item !== column)
                            ))}
                          />
                          {labelForColumn(column)}
                        </label>
                      ))}
                    </fieldset>
                  );
                })}
                {available.filter(column => column !== "time" && !SERIES_GROUPS.some(group => group.columns.includes(column))).length > 0 && (
                  <fieldset style={styles.group}>
                    <legend style={styles.groupLabel}>其他响应</legend>
                    {available
                      .filter(column => column !== "time" && !SERIES_GROUPS.some(group => group.columns.includes(column)))
                      .map(column => (
                        <label key={column} style={styles.option}>
                          <input
                            type="checkbox"
                            checked={selectedColumns.includes(column)}
                            onChange={event => setSelectedColumns(current => (
                              event.target.checked
                                ? [...current, column]
                                : current.filter(item => item !== column)
                            ))}
                          />
                          {labelForColumn(column)}
                        </label>
                      ))}
                  </fieldset>
                )}
                {comparison && (
                  <fieldset style={styles.group}>
                    <legend style={styles.groupLabel}>参数工况（可多选）</legend>
                    {caseOptions.map(item => (
                      <label key={item.caseId} style={styles.option}>
                        <input
                          type="checkbox"
                          checked={selectedCaseIds.includes(item.caseId)}
                          onChange={event => setSelectedCaseIds(current => (
                            event.target.checked
                              ? [...current, item.caseId]
                              : current.filter(caseId => caseId !== item.caseId)
                          ))}
                        />
                        {formatComparisonCaseLabel(item)}
                      </label>
                    ))}
                  </fieldset>
                )}
                <button
                  type="button"
                  className="btn btn-secondary"
                  disabled={loading || selectedColumns.length === 0 || (comparison !== null && selectedCaseIds.length === 0)}
                  onClick={() => void load(selectedColumns, comparison ? selectedCaseIds : [])}
                >
                  更新曲线
                </button>
              </div>
              {comparisonCharts.map(chart => (
                <div key={chart.column} style={styles.comparisonChart}>
                  <span style={styles.chartTitle}>{labelForColumn(chart.column)}时程对比</span>
                  {chart.rows.length > 0 && (
                    <div style={{ height: 260 }}>
                      <ResponsiveContainer width="100%" height="100%">
                        <LineChart data={chart.rows} margin={{ top: 8, right: 12, bottom: 8, left: 0 }}>
                          <CartesianGrid stroke="var(--border-color)" strokeDasharray="3 3" />
                          <XAxis
                            dataKey="time"
                            type="number"
                            domain={["dataMin", "dataMax"]}
                            allowDecimals={false}
                            tickFormatter={formatTimeTick}
                            tick={{ fill: "var(--text-secondary)", fontSize: 10 }}
                          />
                          <YAxis
                            tickFormatter={formatChartValue}
                            tick={{ fill: "var(--text-secondary)", fontSize: 10 }}
                          />
                          <Tooltip
                            labelFormatter={value => `时间：${formatTimeTick(value)} s`}
                            formatter={(value, name) => [formatChartValue(value), String(name)]}
                            contentStyle={styles.tooltip}
                          />
                          <Legend wrapperStyle={{ fontSize: 11 }} />
                          {displayedCases.map((item, index) => (
                            <Line
                              key={item.caseId}
                              type="monotone"
                              dataKey={item.caseId}
                              name={formatComparisonCaseLabel(item)}
                              dot={false}
                              stroke={LINE_COLORS[index % LINE_COLORS.length]}
                              strokeWidth={1.5}
                            />
                          ))}
                        </LineChart>
                      </ResponsiveContainer>
                    </div>
                  )}
                </div>
              ))}
              {!comparison && chartData.length > 0 && (
                <div style={{ height: 260 }}>
                  <ResponsiveContainer width="100%" height="100%">
                    <LineChart data={chartData} margin={{ top: 8, right: 12, bottom: 8, left: 0 }}>
                      <CartesianGrid stroke="var(--border-color)" strokeDasharray="3 3" />
                      <XAxis
                        dataKey="time"
                        type="number"
                        domain={["dataMin", "dataMax"]}
                        allowDecimals={false}
                        tickFormatter={formatTimeTick}
                        tick={{ fill: "var(--text-secondary)", fontSize: 10 }}
                      />
                      <YAxis
                        tickFormatter={formatChartValue}
                        tick={{ fill: "var(--text-secondary)", fontSize: 10 }}
                      />
                      <Tooltip
                        labelFormatter={value => `时间：${formatTimeTick(value)} s`}
                        formatter={(value, name) => [formatChartValue(value), labelForColumn(String(name))]}
                        contentStyle={styles.tooltip}
                      />
                      <Legend wrapperStyle={{ fontSize: 11 }} />
                      {data?.columns.filter(column => column !== "time").map((column, index) => (
                        <Line
                          key={column}
                          type="monotone"
                          dataKey={column}
                          name={labelForColumn(column)}
                          dot={false}
                          stroke={LINE_COLORS[index % LINE_COLORS.length]}
                          strokeWidth={1.5}
                        />
                      ))}
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              )}
              <div style={cardStyles.grid}>
                {data && Object.values(data.peaks).map(peak => (
                  <div key={peak.column}>
                    <span style={cardStyles.label}>{labelForColumn(peak.column)}峰值</span>
                    <strong style={cardStyles.metaValue}>{formatPeak(peak.peakSigned)}</strong>
                    <span style={cardStyles.muted}> t={formatPeakTime(peak.peakTime)} s · |峰值|={formatPeak(peak.peakAbsolute)}</span>
                  </div>
                ))}
                {comparison && comparison.columns.filter(column => column !== "time").flatMap(column => (
                  comparison.cases.map(item => {
                    const peak = item.peaks[column];
                    if (!peak) return null;
                    return (
                      <div key={`${item.caseId}:${column}`}>
                        <span style={cardStyles.label}>{formatComparisonCaseLabel(item)} · {labelForColumn(column)}峰值</span>
                        <strong style={cardStyles.metaValue}>{formatPeak(peak.peakSigned)}</strong>
                        <span style={cardStyles.muted}> t={formatPeakTime(peak.peakTime)} s · |峰值|={formatPeak(peak.peakAbsolute)}</span>
                      </div>
                    );
                  })
                ))}
              </div>
            </>
          )}
        </div>
      )}
    </section>
  );
};

const styles: Record<string, React.CSSProperties> = {
  selector: { display: "flex", flexWrap: "wrap", alignItems: "center", gap: 8, marginBottom: 10 },
  timeHint: { width: "100%", color: "var(--text-secondary)", fontSize: 11 },
  group: { display: "inline-flex", flexWrap: "wrap", alignItems: "center", gap: 8, margin: 0, padding: "3px 8px", border: "1px solid var(--border-color)", borderRadius: 5 },
  groupLabel: { padding: "0 4px", color: "var(--text-secondary)", fontSize: 11 },
  option: { display: "inline-flex", alignItems: "center", gap: 4, fontSize: 11, color: "var(--text-secondary)" },
  comparisonChart: { marginBottom: 14 },
  chartTitle: { display: "block", color: "var(--text-secondary)", fontSize: 12, marginBottom: 4 },
  tooltip: {
    background: "var(--bg-tertiary)",
    border: "1px solid var(--border-color)",
    borderRadius: 4,
    fontSize: 11
  }
};
