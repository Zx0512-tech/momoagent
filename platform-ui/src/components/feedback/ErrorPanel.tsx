import React from "react";
import { AlertCircle, RefreshCw } from "lucide-react";

interface ErrorPanelProps {
  code?: string;
  message: string;
  details?: any;
  onRetry?: () => void;
}

export const ErrorPanel: React.FC<ErrorPanelProps> = ({ code, message, details, onRetry }) => {
  return (
    <div style={styles.container}>
      <div style={styles.header}>
        <AlertCircle size={18} color="var(--error-color)" />
        <span style={styles.title}>操作执行异常</span>
      </div>

      <div style={styles.message}>{message}</div>

      {code && (
        <div style={styles.code}>
          <span>错误代号:</span> <code>{code}</code>
        </div>
      )}

      {details && (
        <div style={styles.details}>
          <div style={styles.detailsLabel}>故障诊断细节:</div>
          <pre style={styles.detailsPre}>
            {typeof details === "object" ? JSON.stringify(details, null, 2) : String(details)}
          </pre>
        </div>
      )}

      {onRetry && (
        <button onClick={onRetry} style={styles.retryBtn} className="btn btn-secondary">
          <RefreshCw size={14} />
          <span>重新尝试执行</span>
        </button>
      )}
    </div>
  );
};

const styles: Record<string, React.CSSProperties> = {
  container: {
    backgroundColor: "var(--error-soft)",
    border: "1px solid var(--error-color)",
    borderRadius: "4px",
    padding: "16px",
    margin: "12px 0",
    display: "flex",
    flexDirection: "column",
    gap: 10
  },
  header: {
    display: "flex",
    alignItems: "center",
    gap: 8
  },
  title: {
    fontWeight: 600,
    fontSize: "13px",
    color: "var(--text-primary)"
  },
  message: {
    fontSize: "13px",
    color: "var(--text-primary)"
  },
  code: {
    fontSize: "12px",
    color: "var(--text-secondary)"
  },
  details: {
    marginTop: "4px"
  },
  detailsLabel: {
    fontSize: "12px",
    fontWeight: 500,
    color: "var(--text-secondary)",
    marginBottom: "4px"
  },
  detailsPre: {
    backgroundColor: "var(--bg-primary)",
    border: "1px solid var(--border-color)",
    padding: "10px",
    borderRadius: "4px",
    fontSize: "11px",
    fontFamily: "var(--font-mono)",
    color: "var(--text-secondary)",
    overflowX: "auto",
    whiteSpace: "pre-wrap"
  },
  retryBtn: {
    alignSelf: "flex-start",
    marginTop: "4px",
    display: "inline-flex",
    alignItems: "center",
    gap: 6
  }
};
