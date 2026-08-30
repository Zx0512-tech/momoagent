import React, { useState } from "react";
import { Copy, Check } from "lucide-react";

interface JsonViewerProps {
  data: any;
  title?: string;
}

export const JsonViewer: React.FC<JsonViewerProps> = ({ data, title }) => {
  const [copied, setCopied] = useState(false);
  const jsonString = JSON.stringify(data, null, 2);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(jsonString);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (e) {
      console.error("Failed to copy JSON to clipboard", e);
    }
  };

  return (
    <div style={styles.container}>
      <div style={styles.header}>
        <span style={styles.title}>{title || "JSON 摘要预览"}</span>
        <button onClick={handleCopy} style={styles.copyBtn}>
          {copied ? <Check size={14} color="var(--success-color)" /> : <Copy size={14} />}
          <span>{copied ? "已复制" : "复制全部"}</span>
        </button>
      </div>

      <div style={styles.body}>
        <pre style={styles.pre}>
          {jsonString}
        </pre>
      </div>
    </div>
  );
};

const styles: Record<string, React.CSSProperties> = {
  container: {
    backgroundColor: "var(--bg-primary)",
    border: "1px solid var(--border-color)",
    borderRadius: "4px",
    display: "flex",
    flexDirection: "column",
    overflow: "hidden"
  },
  header: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    padding: "8px 12px",
    backgroundColor: "var(--bg-tertiary)",
    borderBottom: "1px solid var(--border-color)"
  },
  title: {
    fontSize: "12px",
    fontWeight: 600,
    color: "var(--text-secondary)"
  },
  copyBtn: {
    display: "flex",
    alignItems: "center",
    gap: 4,
    background: "none",
    border: "none",
    color: "var(--text-muted)",
    cursor: "pointer",
    fontSize: "11px",
    transition: "color 0.15s ease"
  },
  body: {
    padding: "12px",
    overflow: "auto",
    maxHeight: "400px"
  },
  pre: {
    margin: 0,
    fontSize: "12px",
    fontFamily: "var(--font-mono)",
    color: "var(--text-primary)",
    whiteSpace: "pre-wrap"
  }
};
// Fix styles copyBtn hover using CSS if needed, but it's simple enough
if (typeof document !== "undefined") {
  const styleEl = document.createElement("style");
  styleEl.innerHTML = `
    button[style*="copyBtn"]:hover {
      color: var(--text-primary) !important;
    }
  `;
  document.head.appendChild(styleEl);
}
export default JsonViewer;
