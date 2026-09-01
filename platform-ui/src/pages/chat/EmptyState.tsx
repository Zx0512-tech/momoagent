import type React from "react";
import { Bot } from "lucide-react";

const EXAMPLES = [
  "使用 OpenSeesPy 对已登记地震工况执行黏滞阻尼器真实优化。每塔布置两个阻尼器，每塔总阻尼系数范围 2000 到 20000，单个阻尼器系数范围 1000 到 10000，速度指数范围 0.3 到 1.0。采用 baseline-first 技术路线：15 个真实 DOE、稳定交叉验证、728 个离散候选、Pareto 前沿与熵权 TOPSIS、最终真实 FEM 复核。优化梁端位移、塔底剪力和塔底弯矩。",
  "对比黏滞阻尼器和电涡流阻尼器在同一地震工况下的减震效果，采用等最大出力基准。",
  "用 OpenSeesPy 分析当前桥梁在登记地震记录下的无阻尼基线响应。"
];

const CAPABILITIES = [
  ["求解器", "OpenSeesPy（内置 USER300 运行时）/ ANSYS"],
  ["阻尼器", "USER300 黏滞 / 电涡流 / 摩擦"],
  ["优化", "真实 DOE + 代理模型 + Pareto + 熵权 TOPSIS"],
  ["证据链", "命令流、求解器版本、输入来源、SHA256、最终 FEM 复核"]
];

/** 空会话时的引导页：介绍能力并提供可一键使用的示例提示词。 */
export const EmptyState = ({ onPick }: { onPick: (text: string) => void }) => (
  <div style={styles.wrap}>
    <div style={styles.icon}><Bot size={30} /></div>
    <h1 style={styles.title}>桥梁阻尼器分析优化智能体</h1>
    <p style={styles.subtitle}>
      用自然语言描述工程目标，智能体会解析意图、冻结受控执行合同、
      在你审批后调用真实有限元求解，并给出可复核的证据。
    </p>

    <div style={styles.capabilities}>
      {CAPABILITIES.map(([label, value]) => (
        <div key={label} style={styles.capability}>
          <span style={styles.capLabel}>{label}</span>
          <span style={styles.capValue}>{value}</span>
        </div>
      ))}
    </div>

    <div style={styles.examplesHead}>试试这些任务</div>
    <div style={styles.examples}>
      {EXAMPLES.map(text => (
        <button key={text} type="button" style={styles.example} onClick={() => onPick(text)}>
          {text.length > 120 ? `${text.slice(0, 120)}…` : text}
        </button>
      ))}
    </div>

    <p style={styles.note}>
      执行任何真实求解前都需要你在对话中批准冻结参数。首次完整优化约需 60～90 分钟。
    </p>
  </div>
);

const styles: Record<string, React.CSSProperties> = {
  wrap: {
    maxWidth: 720,
    margin: "0 auto",
    padding: "48px 20px 20px",
    textAlign: "center"
  },
  icon: {
    width: 56,
    height: 56,
    borderRadius: 14,
    background: "var(--bg-tertiary)",
    color: "var(--primary-color)",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    margin: "0 auto 16px"
  },
  title: { margin: "0 0 10px", fontSize: 22, fontWeight: 600 },
  subtitle: {
    margin: "0 auto 24px",
    maxWidth: 560,
    color: "var(--text-secondary)",
    lineHeight: 1.7
  },
  capabilities: {
    display: "grid",
    gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
    gap: 10,
    marginBottom: 28,
    textAlign: "left"
  },
  capability: {
    background: "var(--bg-secondary)",
    border: "1px solid var(--border-color)",
    borderRadius: 8,
    padding: "10px 12px"
  },
  capLabel: { display: "block", color: "var(--text-secondary)", fontSize: 11, marginBottom: 3 },
  capValue: { fontSize: 12, lineHeight: 1.5 },
  examplesHead: {
    textAlign: "left",
    fontSize: 12,
    color: "var(--text-secondary)",
    marginBottom: 8
  },
  examples: { display: "flex", flexDirection: "column", gap: 8, textAlign: "left" },
  example: {
    background: "var(--bg-secondary)",
    border: "1px solid var(--border-color)",
    borderRadius: 8,
    padding: "10px 12px",
    color: "var(--text-primary)",
    cursor: "pointer",
    textAlign: "left",
    lineHeight: 1.6,
    transition: "border-color 0.15s ease, background-color 0.15s ease"
  },
  note: {
    marginTop: 24,
    color: "var(--text-muted)",
    fontSize: 11,
    lineHeight: 1.6
  }
};
