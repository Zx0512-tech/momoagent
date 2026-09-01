import type React from "react";

/** 对话内嵌卡片的共用样式，沿用平台 design token，保持与分析页一致的工程风。 */
export const cardStyles: Record<string, React.CSSProperties> = {
  card: {
    background: "var(--bg-secondary)",
    border: "1px solid var(--border-color)",
    borderRadius: 8,
    padding: 14,
    marginTop: 10,
    boxShadow: "var(--shadow-sm)"
  },
  /** 结果追问作为助手消息的一部分展示，保留对话流的连续性。 */
  resultMessage: {
    marginTop: 4,
    padding: "2px 0 0",
    background: "transparent",
    border: "none",
    boxShadow: "none"
  },
  /** 需要人工决策的卡片：主色描边强调。 */
  approvalCard: {
    background: "var(--bg-secondary)",
    border: "1px solid var(--primary-color)",
    borderRadius: 8,
    padding: 14,
    marginTop: 10,
    boxShadow: "var(--shadow-sm)"
  },
  /** 审批作为消息内容内联，保留历史记录但不占用整张大卡片。 */
  approvalInline: {
    marginTop: 8,
    padding: "8px 0 2px 12px",
    borderLeft: "2px solid var(--primary-color)",
    background: "transparent"
  },
  approvalLine: {
    display: "flex",
    alignItems: "center",
    gap: 7,
    color: "var(--text-primary)",
    fontSize: 13
  },
  header: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    gap: 10,
    marginBottom: 10
  },
  title: {
    margin: 0,
    fontSize: 14,
    fontWeight: 600,
    display: "flex",
    alignItems: "center",
    gap: 8
  },
  summary: { margin: "0 0 8px", lineHeight: 1.6 },
  note: { color: "var(--text-secondary)", fontSize: 12, margin: "6px 0 0" },
  warning: { color: "var(--warning-color)", fontSize: 12, margin: "8px 0 0" },

  badge: {
    display: "inline-flex",
    alignItems: "center",
    padding: "2px 8px",
    borderRadius: 999,
    fontSize: 11,
    fontWeight: 600,
    background: "var(--bg-tertiary)",
    color: "var(--primary-color)",
    whiteSpace: "nowrap"
  },
  badgeWarning: {
    display: "inline-flex",
    alignItems: "center",
    padding: "2px 8px",
    borderRadius: 999,
    fontSize: 11,
    fontWeight: 600,
    background: "var(--warning-soft)",
    color: "var(--warning-color)",
    whiteSpace: "nowrap"
  },

  metaGrid: {
    display: "grid",
    gridTemplateColumns: "repeat(auto-fit, minmax(170px, 1fr))",
    gap: 10
  },
  metaLabel: { display: "block", marginBottom: 3, color: "var(--text-secondary)", fontSize: 11 },
  metaValue: { fontSize: 12, wordBreak: "break-word" },

  list: { margin: "8px 0 0", paddingLeft: 20, lineHeight: 1.7 },

  /** 阶段进度列表 */
  stageList: { display: "flex", flexDirection: "column", gap: 6, marginTop: 8 },
  stageItem: { display: "flex", alignItems: "center", gap: 8, fontSize: 12 },
  dot: {
    width: 7,
    height: 7,
    borderRadius: "50%",
    background: "var(--primary-color)",
    flexShrink: 0
  },

  /** 冻结参数披露区 */
  disclosure: { marginTop: 10, fontSize: 12 },
  frozenBox: {
    marginTop: 8,
    padding: 10,
    background: "var(--bg-tertiary)",
    borderRadius: 6
  },
  pre: {
    overflowX: "auto",
    margin: 0,
    fontFamily: "var(--font-mono)",
    fontSize: 11,
    maxHeight: 240,
    whiteSpace: "pre-wrap",
    wordBreak: "break-all"
  },
  sha: {
    display: "block",
    marginTop: 8,
    fontFamily: "var(--font-mono)",
    fontSize: 11,
    color: "var(--text-muted)",
    wordBreak: "break-all"
  },

  actions: { display: "flex", gap: 10, flexWrap: "wrap", marginTop: 12 },
  linkColumn: { display: "flex", flexDirection: "column", gap: 4, marginTop: 8 },
  meta: { color: "var(--text-secondary)", fontSize: 12 },

  /** 表单栅格与字段标签（映射卡） */
  grid: {
    display: "grid",
    gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
    gap: 10
  },
  label: { display: "block", marginBottom: 4, color: "var(--text-secondary)", fontSize: 12 },
  /** 行内等宽代码（证据卡的字段名） */
  code: { fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--text-primary)" },

  /** 指标 / 校验项表格 */
  disclosureButton: {
    border: "1px solid var(--border-color)",
    borderRadius: 5,
    background: "var(--bg-tertiary)",
    color: "var(--text-secondary)",
    padding: "5px 9px",
    cursor: "pointer",
    fontSize: 11
  },
  muted: { color: "var(--text-muted)", fontSize: 11 },
  table: {
    width: "100%",
    borderCollapse: "collapse",
    fontSize: 12,
    marginTop: 8
  },
  th: {
    textAlign: "left",
    padding: "7px 10px",
    color: "var(--text-secondary)",
    fontWeight: 600,
    borderBottom: "1px solid var(--border-color)",
    background: "var(--bg-tertiary)"
  },
  td: {
    padding: "7px 10px",
    borderBottom: "1px solid var(--border-color)",
    wordBreak: "break-word"
  },
  chartBox: { marginTop: 10, width: "100%", height: 220 }
};

/** 小标签：键值对展示。 */
export const InfoItem = ({ label, value }: { label: string; value: React.ReactNode }) => (
  <div>
    <span style={cardStyles.metaLabel}>{label}</span>
    <div style={cardStyles.metaValue}>{value}</div>
  </div>
);
