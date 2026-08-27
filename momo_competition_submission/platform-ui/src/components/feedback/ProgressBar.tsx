import React from "react";

interface ProgressBarProps {
  percent?: number;
  phase?: string;
  message?: string;
}

export const ProgressBar: React.FC<ProgressBarProps> = ({ percent, phase, message }) => {
  const showPercent = percent !== undefined;
  const clampedPercent = showPercent ? Math.min(Math.max(percent, 0), 100) : 0;

  return (
    <div style={styles.container}>
      <div style={styles.meta}>
        <span style={styles.phase}>{phase || "正在读取进度数据..."}</span>
        {showPercent && <span style={styles.percent}>{clampedPercent}%</span>}
      </div>

      <div style={styles.track}>
        <div style={{
          ...styles.fill,
          width: `${showPercent ? clampedPercent : 100}%`,
          animation: showPercent ? "none" : "progress-loading 1.5s infinite linear"
        }} />
      </div>

      {message && <div style={styles.message}>{message}</div>}
    </div>
  );
};

const styles: Record<string, React.CSSProperties> = {
  container: {
    margin: "12px 0",
    width: "100%"
  },
  meta: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    marginBottom: "6px",
    fontSize: "12px"
  },
  phase: {
    fontWeight: 600,
    color: "var(--text-primary)"
  },
  percent: {
    fontWeight: 600,
    color: "var(--primary-color)"
  },
  track: {
    height: "6px",
    backgroundColor: "var(--bg-active)",
    border: "1px solid var(--border-color)",
    borderRadius: "3px",
    overflow: "hidden",
    position: "relative"
  },
  fill: {
    height: "100%",
    backgroundColor: "var(--primary-color)",
    borderRadius: "2px",
    transition: "width 0.3s ease-out"
  },
  message: {
    marginTop: "6px",
    fontSize: "11px",
    color: "var(--text-secondary)",
    fontFamily: "var(--font-mono)"
  }
};

// Add standard keyframe animation in code using stylesheet injection if necessary
if (typeof document !== "undefined") {
  const styleEl = document.createElement("style");
  styleEl.innerHTML = `
    @keyframes progress-loading {
      0% { background-position: 0% 0%; width: 0%; left: 0%; }
      50% { width: 40%; left: 30%; }
      100% { background-position: 100% 0%; width: 0%; left: 100%; }
    }
  `;
  document.head.appendChild(styleEl);
}
