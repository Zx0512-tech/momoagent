import React from "react";

export const LoadingSpinner: React.FC = () => {
  return (
    <svg
      style={styles.spinner}
      viewBox="0 0 50 50"
    >
      <circle
        style={styles.path}
        cx="25"
        cy="25"
        r="20"
        fill="none"
        strokeWidth="5"
      />
    </svg>
  );
};

const styles: Record<string, React.CSSProperties> = {
  spinner: {
    animation: "rotate 2s linear infinite",
    width: "24px",
    height: "24px"
  },
  path: {
    stroke: "var(--primary-color)",
    strokeLinecap: "round",
    animation: "dash 1.5s ease-in-out infinite"
  }
};

if (typeof document !== "undefined") {
  const styleEl = document.createElement("style");
  styleEl.innerHTML = `
    @keyframes rotate {
      100% { transform: rotate(360deg); }
    }
    @keyframes dash {
      0% { stroke-dasharray: 1, 150; stroke-dashoffset: 0; }
      50% { stroke-dasharray: 90, 150; stroke-dashoffset: -35; }
      100% { stroke-dasharray: 90, 150; stroke-dashoffset: -124; }
    }
  `;
  document.head.appendChild(styleEl);
}
export default LoadingSpinner;
