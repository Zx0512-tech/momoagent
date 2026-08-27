import type React from "react";
import { Link } from "react-router-dom";
import { useEffect, useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis
} from "recharts";
import { agentApi, type AgentResultArtifact, type AgentRun } from "../../../api/agentApi";
import { CHART_COLORS } from "../../../theme/chart";
import { cardStyles, InfoItem } from "./cardStyles";
import { TimeseriesSection } from "./TimeseriesSection";

/**
 * 结果卡：展示真实求解结论。
 *
 * 后端 resultSummary 只承载标量结论；时程曲线通过用户点击后再按需读取。
 */

/** 目标量的中文名与显示单位换算。 */
const OBJECTIVE_META: Record<string, { label: string; unit: string; scale: number }> = {
  max_girder_end_displacement: { label: "梁端位移", unit: "m", scale: 1 },
  max_tower_base_shear: { label: "塔底剪力", unit: "MN", scale: 1e-6 },
  max_tower_base_moment: { label: "塔底弯矩", unit: "GN·m", scale: 1e-9 },
  max_damper_force: { label: "阻尼器最大力", unit: "MN", scale: 1e-6 },
  max_damper_stroke: { label: "阻尼器行程", unit: "m", scale: 1 },
  energy_dissipation: { label: "耗能", unit: "MJ", scale: 1e-6 },
  max_acceleration: { label: "最大加速度", unit: "m/s²", scale: 1 },
  cumulative_displacement: { label: "累积位移", unit: "m", scale: 1 },
  cost: { label: "成本", unit: "", scale: 1 }
};

function formatValue(key: string, raw: number): string {
  const meta = OBJECTIVE_META[key];
  if (!meta) return String(raw);
  const scaled = raw * meta.scale;
  const text = formatSignificantNumber(scaled);
  return meta.unit ? `${text} ${meta.unit}` : text;
}

/** 结果卡中的数值统一采用四位有效数字，保留工程量级信息。 */
function formatSignificantNumber(value: number): string {
  return Number(value).toPrecision(4);
}

function formatInquiryValue(metricId: string, raw: number, sourceUnit = ""): { value: string; unit: string } {
  const meta = OBJECTIVE_META[metricId];
  const scaled = meta ? raw * meta.scale : raw;
  return {
    value: formatSignificantNumber(scaled),
    unit: meta?.unit ?? sourceUnit
  };
}

function formatInquiryTime(value: number | null): string {
  return value === null ? "未记录" : `${formatSignificantNumber(value)} s`;
}

function formatCompactNumber(value: number): string {
  return formatSignificantNumber(value);
}

function formatKeyValueMap(
  values: Record<string, number>,
  formatKey: (key: string) => string = (key) => key
): string {
  return Object.entries(values)
    .map(([key, value]) => `${formatKey(key)}=${formatCompactNumber(value)}`)
    .join("，");
}

function topsisObjectiveLabel(name: string): string {
  const key = name.split(":", 2).pop() ?? name;
  return OBJECTIVE_META[key]?.label ?? name;
}

type CaseResult = Record<string, unknown>;

function readObjectives(item: CaseResult): Record<string, number> {
  const raw = item.objectives;
  if (!raw || typeof raw !== "object") return {};
  const out: Record<string, number> = {};
  for (const [key, value] of Object.entries(raw as Record<string, unknown>)) {
    if (typeof value === "number" && Number.isFinite(value)) out[key] = value;
  }
  return out;
}

function readObjectiveMap(raw: unknown): Record<string, number> {
  return readObjectives({ objectives: raw });
}

function objectiveLabel(key: string): string {
  return OBJECTIVE_META[key]?.label ?? key;
}

function caseLabel(item: CaseResult, index: number): string {
  const parameters = item.parameters;
  if (parameters && typeof parameters === "object" && !Array.isArray(parameters)) {
    return formatDamperParameters(parameters as Record<string, unknown>);
  }
  const candidate = item.damperType ?? item.caseId ?? item.case_id;
  return candidate ? String(candidate) : `工况 ${index + 1}`;
}

const BAR_COLORS = CHART_COLORS;

const CONDITION_LABELS: Record<string, string> = {
  EARTHQUAKE: "地震",
  WIND: "风",
  COMBINED: "组合"
};

function formatDamperParameters(parameters: Record<string, unknown>): string {
  const entries = Object.entries(parameters).filter(([key, value]) => (
    key !== "vfloor"
    || typeof value !== "number"
    || Math.abs(value - 0.001) > Number.EPSILON
  ));
  if (entries.length === 0) return "未记录";
  return entries.map(([key, value]) => {
    const formatted = typeof value === "number" && Number.isFinite(value)
      ? formatSignificantNumber(value)
      : value && typeof value === "object" ? JSON.stringify(value) : String(value);
    return `${key === "alpha" ? "α" : key}=${formatted}`;
  }).join("，");
}

function artifactLabel(artifact: AgentResultArtifact): string {
  if (artifact.name.startsWith("优化结果_") && artifact.name.endsWith(".xlsx")) {
    return `优化结果：${artifact.name.slice("优化结果_".length, -".xlsx".length)}（多参数工作表）`;
  }
  switch (artifact.name) {
    case "timeseries.csv":
      return "综合响应时程（梁端位移 Node36/107 UX、加速度、塔底内力）";
    case "tower_base_shear_components.csv":
      return "塔底剪力（截面/惯性分量）";
    case "tower_girder_relative_response.csv":
      return "梁端-塔相对响应（Node36/107 ↔ Node517/518/520/521 UX）";
    case "tower_girder_relative_components.csv":
      return "梁端-塔相对响应分量（节点对 UX）";
    case "real_analysis_summary.json":
      return "分析结果摘要（梁端位移、加速度、塔底内力）";
    case "real_analysis_overview.json":
      return "分析概览（工况与求解器）";
    case "result_catalog.json":
      return "结果查询目录（指标列与单位）";
    case "real_output_manifest.json":
      return "输出清单（文件路径与 SHA256）";
    case "analysis_evidence_report.json":
    case "full_optimization_evidence_report.json":
    case "damper_comparison_evidence_report.json":
      return "结果校验报告（证据门槛）";
    case "earthquake_acceleration_record_momo_standard.csv":
      return "地震输入（UX 加速度）";
    case "bundled_earthquake_load_report.json":
      return "荷载登记报告（输入来源）";
    default:
      return `结果文件：${artifact.name}`;
  }
}

function artifactKindLabel(kind: string): string {
  return {
    CSV_TIMESERIES: "时程数据",
    CSV_TABLE: "指标明细",
    JSON_SUMMARY: "结果摘要",
    COMMAND_STREAM: "求解命令记录",
    PLOT: "结果图件",
    OPTIMIZATION_REPORT: "优化报告",
    DECISION_REPORT: "决策报告",
    RAW_DATA: "原始数据",
    SURROGATE_MODEL: "代理模型",
    BINARY: "Excel 多工况工作簿"
  }[kind] ?? "结果文件";
}

function artifactDisplayLabel(artifact: AgentResultArtifact): string {
  return artifact.kind === "UNKNOWN" ? "正在读取文件说明…" : artifactLabel(artifact);
}

export const ResultCard = ({ run }: { run: AgentRun }) => {
  const summary = run.resultSummary;
  const artifacts = useMemo(
    () => run.resultArtifacts && run.resultArtifacts.length > 0
      ? run.resultArtifacts
      : run.artifactIds.map(artifactId => ({ artifactId, name: artifactId, kind: "UNKNOWN" })),
    [run.artifactIds, run.resultArtifacts]
  );
  const [resolvedArtifacts, setResolvedArtifacts] = useState<AgentResultArtifact[]>(artifacts);
  const [artifactsExpanded, setArtifactsExpanded] = useState(false);

  useEffect(() => {
    let active = true;
    const missing = artifacts.filter(artifact => artifact.kind === "UNKNOWN");
    if (missing.length === 0) {
      setResolvedArtifacts(artifacts);
      return () => { active = false; };
    }
    Promise.all(missing.map(artifact => agentApi.getArtifact(artifact.artifactId).catch(() => artifact)))
      .then(details => {
        if (!active) return;
        const byId = new Map(details.map(artifact => [artifact.artifactId, artifact]));
        setResolvedArtifacts(artifacts.map(artifact => byId.get(artifact.artifactId) ?? artifact));
      });
    return () => { active = false; };
  }, [artifacts]);

  if (!summary) return null;

  const cases = (summary.caseResults ?? []) as CaseResult[];
  const checks = Object.entries(summary.checks ?? {});
  const accepted = summary.accepted === true;
  const evidenceMode = summary.evidenceMode;
  const objectives = readObjectiveMap(summary.objectives);
  const baselineObjectives = readObjectiveMap(summary.baselineObjectives);
  const recommendedObjectives = readObjectiveMap(summary.recommendedObjectives);
  const recommendedParameters = readObjectiveMap(summary.recommendedParameters);
  const figures = summary.figures ?? (run.figureArtifactIds ?? []).map(artifactId => ({
    claim: "结果时程图",
    artifactId,
    metrics: []
  }));
  const metadata = run.resultMetadata;
  const inquiryMetrics = summary.inquiryMetrics ?? [];
  const inquiryTopsis = summary.inquiryTopsis ?? [];
  const inquiryTopsisWeights = summary.inquiryTopsisWeights;
  const isInquiry = run.taskType === "INQUIRY";
  const isParameterSweep = run.taskType === "DAMPER_PARAMETER_SWEEP";
  const inquiryPending = isInquiry && run.status === "PLANNING";

  // 收集所有工况共同出现的目标量，逐指标构造一组柱子。
  const objectivesPerCase = cases.map(readObjectives);
  const objectiveKeys = Array.from(
    new Set(objectivesPerCase.flatMap((item) => Object.keys(item)))
  ).filter((key) => key in OBJECTIVE_META);

  const chartData = objectiveKeys.map((key) => {
    const meta = OBJECTIVE_META[key];
    const row: Record<string, string | number> = { metric: `${meta.label}（${meta.unit || "-"}）` };
    objectivesPerCase.forEach((objectives, index) => {
      const value = objectives[key];
      if (typeof value === "number") {
        row[caseLabel(cases[index], index)] = Number(formatSignificantNumber(value * meta.scale));
      }
    });
    return row;
  });

  const seriesNames = cases.map((item, index) => caseLabel(item, index));
  const singleCaseObjectives = objectivesPerCase.length === 1 ? objectivesPerCase[0] : null;
  const optimizationKeys = Array.from(new Set([
    ...Object.keys(baselineObjectives),
    ...Object.keys(recommendedObjectives)
  ])).filter(key => key in OBJECTIVE_META);
  const analysisObjectiveKeys = Object.keys(objectives).filter(key => key in OBJECTIVE_META);
  const hasOptimization = optimizationKeys.length > 0;

  return (
    <div style={isInquiry ? cardStyles.resultMessage : cardStyles.card}>
      <h3 style={cardStyles.title}>
        {isInquiry ? "结果查询" : "分析结果"}
        <span style={isInquiry || accepted ? cardStyles.badge : cardStyles.badgeWarning}>
          {isInquiry
            ? inquiryPending ? `读取中 · ${inquiryMetrics.length + inquiryTopsis.length} 项` : "只读结果"
            : evidenceMode === "REAL_FEM" ? "真实 FEM" : evidenceMode ?? "诊断"}
        </span>
      </h3>

      {metadata && (
        <div style={{ ...cardStyles.metaGrid, marginTop: 10, marginBottom: 10 }}>
          <InfoItem
            label="工况"
            value={metadata.condition ? CONDITION_LABELS[metadata.condition] ?? metadata.condition : "未记录"}
          />
          <InfoItem label="模型" value={metadata.model ?? "未记录"} />
          <InfoItem label="求解器" value={metadata.solver ?? "未记录"} />
          <InfoItem
            label="阻尼器"
            value={metadata.hasDamper ? metadata.damperTypes.join(" / ") || "有（类型未记录）" : "无"}
          />
          {metadata.hasDamper && !isParameterSweep && (
            <InfoItem label="阻尼参数" value={formatDamperParameters(metadata.damperParameters)} />
          )}
        </div>
      )}

      {!isInquiry && !isParameterSweep && summary.message && <p style={{ fontSize: 12, marginBottom: 10 }}>{summary.message}</p>}
      {!isInquiry && !isParameterSweep && summary.narrativeSummary && summary.narrativeSummary !== summary.message && (
        <p style={cardStyles.summary}>{summary.narrativeSummary}</p>
      )}

      {isInquiry && inquiryMetrics.length > 0 && (
        <ul aria-live="polite" aria-busy={inquiryPending} style={styles.inquiryList}>
          {inquiryMetrics.map(metric => {
            const peak = formatInquiryValue(metric.metricId, metric.peakAbsolute, metric.unit);
            return (
              <li key={`${metric.metricId}:${metric.sourceColumn}`} style={styles.inquiryRow}>
                <span style={styles.inquiryMetric}>{metric.label}：</span>
                <span style={styles.inquiryReading}>
                  <span style={styles.inquiryValue}>{peak.value}</span>
                  {peak.unit && <span style={styles.inquiryUnit}>{peak.unit}</span>}
                </span>
                <span style={styles.inquiryDetail}>（发生时刻 {formatInquiryTime(metric.peakTimeS)}）</span>
              </li>
            );
          })}
          {inquiryPending && (
            <li role="status" style={styles.inquiryPending}>正在继续读取其余指标…</li>
          )}
        </ul>
      )}

      {isInquiry && inquiryTopsis.length > 0 && (
        <div style={{ overflowX: "auto", marginTop: 10 }}>
          <div style={{ ...cardStyles.label, marginBottom: 6 }}>TOPSIS 候选排名</div>
          {inquiryTopsisWeights && (
            <div style={{ ...styles.inquiryDetail, marginBottom: 8 }}>
              <strong>决策权重：</strong>
              {inquiryTopsisWeights.objectiveNames.map((name, index) => (
                <span key={name} style={{ marginRight: 14 }}>
                  {topsisObjectiveLabel(name)} {formatCompactNumber(inquiryTopsisWeights.weights[index] ?? 0)}
                </span>
              ))}
            </div>
          )}
          <table style={cardStyles.table}>
            <thead>
              <tr>
                <th style={cardStyles.th}>排名</th>
                <th style={cardStyles.th}>TOPSIS 得分</th>
                <th style={cardStyles.th}>参数</th>
                <th style={cardStyles.th}>目标指标</th>
              </tr>
            </thead>
            <tbody>
              {inquiryTopsis.map(row => (
                <tr key={`${row.rank}:${row.paretoIndex}`}>
                  <td style={cardStyles.td}>{row.rank}</td>
                  <td style={cardStyles.td}>{formatCompactNumber(row.score)}</td>
                  <td style={cardStyles.td}>{formatKeyValueMap(row.parameters)}</td>
                  <td style={cardStyles.td}>{formatKeyValueMap(row.objectives, topsisObjectiveLabel)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {isInquiry && !inquiryPending && summary.message && (
        <p style={cardStyles.note}>{summary.message}</p>
      )}
      {isInquiry
        && !inquiryPending
        && inquiryMetrics.length === 0
        && summary.narrativeMode === "LLM"
        && summary.narrativeSummary
        && summary.narrativeSummary !== summary.message
        && <p style={cardStyles.summary}>{summary.narrativeSummary}</p>}

      {figures.length > 0 && (
        <div style={{ marginTop: 12 }}>
          <span style={cardStyles.label}>按需绘图</span>
          {figures.map(figure => (
            <figure key={figure.artifactId} style={{ margin: "8px 0" }}>
              <img
                src={agentApi.artifactDownloadUrl(figure.artifactId)}
                alt={figure.claim}
                style={{ display: "block", maxWidth: "100%", border: "1px solid var(--border-color)" }}
              />
              <figcaption style={{ fontSize: 11, color: "var(--text-secondary)", marginTop: 4 }}>
                {figure.claim}
                <a href={agentApi.artifactDownloadUrl(figure.artifactId)} style={{ marginLeft: 8 }}>
                  下载图件
                </a>
              </figcaption>
            </figure>
          ))}
        </div>
      )}

      {/* 单次分析：直接展示 Job result 中的 objectives。 */}
      {cases.length === 0 && analysisObjectiveKeys.length > 0 && !hasOptimization && (
        <div style={{ overflowX: "auto", marginTop: 10 }}>
          <table style={cardStyles.table}>
            <thead>
              <tr>
                <th style={cardStyles.th}>响应指标</th>
                <th style={cardStyles.th}>峰值</th>
                <th style={cardStyles.th}>单位</th>
              </tr>
            </thead>
            <tbody>
              {analysisObjectiveKeys.map(key => {
                const value = objectives[key];
                return (
                <tr key={key}>
                  <td style={cardStyles.td}>{objectiveLabel(key)}</td>
                  <td style={cardStyles.td}>{formatSignificantNumber(value * OBJECTIVE_META[key].scale)}</td>
                  <td style={cardStyles.td}>{OBJECTIVE_META[key].unit || "-"}</td>
                </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* 优化：并排展示基线、推荐值和相对变化。 */}
      {cases.length === 0 && hasOptimization && (
        <div style={{ overflowX: "auto", marginTop: 10 }}>
          <table style={cardStyles.table}>
            <thead>
              <tr>
                <th style={cardStyles.th}>指标</th>
                <th style={cardStyles.th}>基线</th>
                <th style={cardStyles.th}>推荐</th>
                <th style={cardStyles.th}>变化</th>
              </tr>
            </thead>
            <tbody>
              {optimizationKeys.map(key => {
                const baseline = baselineObjectives[key];
                const recommended = recommendedObjectives[key];
                const change = typeof baseline === "number" && typeof recommended === "number" && baseline !== 0
                  ? ((recommended - baseline) / baseline) * 100
                  : null;
                return (
                  <tr key={key}>
                    <td style={cardStyles.td}>{objectiveLabel(key)}</td>
                    <td style={cardStyles.td}>{typeof baseline === "number" ? formatValue(key, baseline) : "—"}</td>
                    <td style={cardStyles.td}>{typeof recommended === "number" ? formatValue(key, recommended) : "—"}</td>
                    <td style={cardStyles.td}>{change === null ? "—" : `${change.toPrecision(4)}%`}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {Object.keys(recommendedParameters).length > 0 && (
            <div style={{ ...cardStyles.grid, marginTop: 10 }}>
              {Object.entries(recommendedParameters).map(([key, value]) => (
                <InfoItem key={key} label={`推荐参数 · ${key}`} value={formatSignificantNumber(value)} />
              ))}
            </div>
          )}
        </div>
      )}

      {/* 单工况：直接列指标 */}
      {singleCaseObjectives && Object.keys(singleCaseObjectives).length > 0 && (
        <div style={cardStyles.grid}>
          {Object.entries(singleCaseObjectives).map(([key, value]) => (
            <InfoItem
              key={key}
              label={OBJECTIVE_META[key]?.label ?? key}
              value={<span style={cardStyles.metaValue}>{formatValue(key, value)}</span>}
            />
          ))}
        </div>
      )}

      {/* 多工况：分组柱状图对比 */}
      {!isParameterSweep && chartData.length > 0 && cases.length > 1 && (
        <div style={{ height: 260, marginTop: 12 }}>
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={chartData} margin={{ top: 8, right: 12, bottom: 8, left: 0 }}>
              <CartesianGrid stroke="var(--border-color)" strokeDasharray="3 3" />
              <XAxis dataKey="metric" tick={{ fill: "var(--text-secondary)", fontSize: 10 }} />
              <YAxis tick={{ fill: "var(--text-secondary)", fontSize: 10 }} />
              <Tooltip
                contentStyle={{
                  background: "var(--bg-tertiary)",
                  border: "1px solid var(--border-color)",
                  borderRadius: 4,
                  fontSize: 11
                }}
              />
              <Legend wrapperStyle={{ fontSize: 11 }} />
              {seriesNames.map((name, index) => (
                <Bar key={name} dataKey={name} fill={BAR_COLORS[index % BAR_COLORS.length]}>
                  <Cell fill={BAR_COLORS[index % BAR_COLORS.length]} />
                </Bar>
              ))}
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* 工况明细表 */}
      {cases.length > 1 && (
        <div style={{ overflowX: "auto", marginTop: 12 }}>
          <table style={cardStyles.table}>
            <thead>
              <tr>
                <th style={cardStyles.th}>工况</th>
                {objectiveKeys.map((key) => (
                  <th key={key} style={cardStyles.th}>
                    {OBJECTIVE_META[key].label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {cases.map((item, index) => (
                <tr key={caseLabel(item, index)}>
                  <td style={cardStyles.td}>{caseLabel(item, index)}</td>
                  {objectiveKeys.map((key) => {
                    const value = objectivesPerCase[index][key];
                    return (
                      <td key={key} style={cardStyles.td}>
                        {typeof value === "number" ? formatValue(key, value) : "—"}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* 工程门槛校验项 */}
      {checks.length > 0 && (
        <details style={cardStyles.disclosure}>
          <summary style={cardStyles.summary}>
            工程门槛校验（{checks.filter(([, passed]) => passed).length}/{checks.length} 通过）
          </summary>
          <ul style={cardStyles.list}>
            {checks.map(([name, passed]) => (
              <li key={name} style={{ fontSize: 12, color: passed ? "var(--success-color)" : "var(--error-color)" }}>
                {passed ? "通过" : "未通过"} · {name}
              </li>
            ))}
          </ul>
        </details>
      )}

      {run.taskType !== "INQUIRY" && run.taskType !== "CONVERSATION" && (
        <TimeseriesSection runId={run.runId} taskType={run.taskType} />
      )}

      {/* 制品下载：默认收起，避免长列表占满对话。 */}
      {resolvedArtifacts.length > 0 && (
        <section style={styles.artifacts}>
          <button
            type="button"
            style={cardStyles.disclosureButton}
            aria-expanded={artifactsExpanded}
            onClick={() => setArtifactsExpanded(current => !current)}
          >
            {artifactsExpanded ? "收起制品下载" : "查看制品下载"}
          </button>
          {artifactsExpanded && (
            <div style={styles.artifactList} role="list">
              {resolvedArtifacts.map(artifact => (
                <a
                  key={artifact.artifactId}
                  href={agentApi.artifactDownloadUrl(artifact.artifactId)}
                  style={styles.artifactLink}
                  title={`${artifactDisplayLabel(artifact)} · ${artifact.name} · ${artifact.kind}`}
                  role="listitem"
                >
                  <span>{artifactDisplayLabel(artifact)}</span>
                  <small style={styles.artifactKind}>{artifactKindLabel(artifact.kind)}</small>
                </a>
              ))}
            </div>
          )}
        </section>
      )}

      {/* 深挖入口：跳到分析页 */}
      {!inquiryPending && <div style={cardStyles.actions}>
        <Link to="/results" className="btn btn-secondary">
          在结果页中深入分析
        </Link>
        <Link to="/optimization" className="btn btn-secondary">
          查看多目标决策
        </Link>
      </div>}

      {!isInquiry && !accepted && (
        <p style={cardStyles.warning}>
          未通过全部真实 FEM 门槛，结果仅供诊断，不能作为正式设计结论。
        </p>
      )}
    </div>
  );
};

const styles: Record<string, React.CSSProperties> = {
  inquiryList: {
    display: "flex",
    flexDirection: "column",
    gap: 10,
    margin: "12px 0 0",
    paddingLeft: 22
  },
  inquiryRow: {
    paddingLeft: 2,
    lineHeight: 1.7
  },
  inquiryMetric: { fontSize: 13, fontWeight: 600 },
  inquiryReading: { display: "inline-flex", alignItems: "baseline", gap: 6 },
  inquiryValue: { fontFamily: "var(--font-mono)", fontSize: 14, fontWeight: 600 },
  inquiryUnit: { color: "var(--text-secondary)", fontSize: 12 },
  inquiryDetail: { marginLeft: 8, color: "var(--text-secondary)", fontSize: 12 },
  inquiryPending: { color: "var(--text-secondary)", fontSize: 12 },
  artifacts: { marginTop: 12 },
  artifactList: { display: "flex", flexWrap: "wrap", gap: 8, marginTop: 6 },
  artifactLink: {
    display: "inline-flex",
    alignItems: "center",
    minHeight: 30,
    maxWidth: "100%",
    padding: "5px 9px",
    border: "1px solid var(--border-color)",
    borderRadius: 6,
    background: "var(--bg-tertiary)",
    color: "var(--primary-color)",
    fontSize: 11,
    lineHeight: 1.4,
    textDecoration: "none"
  },
  artifactKind: {
    marginLeft: 6,
    color: "var(--text-secondary)",
    fontSize: 10
  }
};
