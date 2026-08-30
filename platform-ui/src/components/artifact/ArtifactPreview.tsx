import React, { useEffect, useState } from "react";
import { api, resolveApiUrl } from "../../api/client";
import type { Artifact } from "../../api/types";
import { LoadingSpinner } from "../feedback/LoadingSpinner";
import { ErrorPanel } from "../feedback/ErrorPanel";
import { JsonViewer } from "../data/JsonViewer";
import { CsvPreview } from "../data/CsvPreview";
import { MarkdownViewer } from "../data/MarkdownViewer";
import { EarthquakeWorkflowOverviewPanel } from "./EarthquakeWorkflowOverviewPanel";
import { Download, FileText, Image, FileCode, CheckCircle, AlertTriangle } from "lucide-react";

interface ArtifactPreviewProps {
  artifact: Artifact;
  onClose?: () => void;
}

export const ArtifactPreview: React.FC<ArtifactPreviewProps> = ({ artifact, onClose }) => {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<{ message: string; code?: string } | null>(null);
  const [previewData, setPreviewData] = useState<any>(null);

  useEffect(() => {
    const loadPreview = async () => {
      setLoading(true);
      setError(null);
      try {
        if (!artifact.canPreview) {
          setLoading(false);
          return;
        }
        const data = await api.getArtifactPreview(artifact.artifactId);
        setPreviewData(data);
      } catch (err: any) {
        setError({
          message: err.message || "未能加载文件预览",
          code: err.code
        });
      } finally {
        setLoading(false);
      }
    };

    loadPreview();
  }, [artifact.artifactId, artifact.canPreview]);

  // Handle different preview render modes
  const renderPreviewContent = () => {
    if (loading) {
      return (
        <div style={styles.loadingContainer}>
          <LoadingSpinner />
          <span style={{ marginLeft: 8, color: "var(--text-secondary)" }}>正在提取并解析预览数据...</span>
        </div>
      );
    }

    if (error) {
      return (
        <ErrorPanel
          message={error.message}
          code={error.code}
          onRetry={() => {
            setLoading(true);
            setError(null);
            api.getArtifactPreview(artifact.artifactId)
              .then(data => setPreviewData(data))
              .catch(err => setError({ message: err.message, code: err.code }))
              .finally(() => setLoading(false));
          }}
        />
      );
    }

    if (!artifact.canPreview) {
      return (
        <div style={styles.noPreviewContainer}>
          <AlertTriangle size={36} color="var(--warning-color)" />
          <h3 style={{ margin: "12px 0 6px 0", color: "var(--text-primary)" }}>该类型制品不支持在线预览</h3>
          <p style={{ color: "var(--text-secondary)", marginBottom: 16, fontSize: "12px" }}>
            该文件为非文本的二进制对象 (如 .pkl 模型文件或超大分析网格数据)。
          </p>
          <a
            href={resolveApiUrl(artifact.downloadUrl)}
            download
            className="btn btn-primary"
            style={{ textDecoration: "none" }}
          >
            <Download size={14} />
            <span>下载原始文件 ({formatBytes(artifact.sizeBytes)})</span>
          </a>
        </div>
      );
    }

    if (!previewData) {
      return <div style={{ color: "var(--text-muted)", padding: 20 }}>暂无预览内容</div>;
    }

    // Distinguish preview types
    if (artifact.kind === "PARITY_REPORT") {
      const parityPassed = previewData?.status === "PASS";
      return (
        <div style={styles.reportWrapper}>
          <div style={styles.reportHeader}>
            {parityPassed ? (
              <CheckCircle size={20} color="var(--success-color)" />
            ) : (
              <AlertTriangle size={20} color="var(--warning-color)" />
            )}
            <h3 style={{ color: "var(--text-primary)" }}>
              {parityPassed ? "一致性比对通过 (Parity OK)" : "一致性比对未通过 (Parity FAIL)"}
            </h3>
          </div>
          <div style={{ display: "grid", gap: 12 }}>
            <JsonViewer data={previewData} title="对齐偏差详情" />
          </div>
        </div>
      );
    }

    if (isBaselineOptimizationOverview(artifact, previewData)) {
      return <EarthquakeWorkflowOverviewPanel overview={previewData} />;
    }

    if (artifact.kind === "JSON_SUMMARY" || artifact.mimeType === "application/json") {
      return <JsonViewer data={previewData} title={artifact.name} />;
    }

    if (artifact.kind === "COMMAND_STREAM" && typeof previewData === "object" && typeof previewData.commandText === "string") {
      return (
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <JsonViewer data={previewData.moduleSummary || {}} title="命令流模块摘要" />
          {previewData.sha256 && (
            <div style={styles.hashBox}>
              <span style={styles.hashLabel}>SHA256</span>
              <code style={styles.hashValue}>{previewData.sha256}</code>
            </div>
          )}
          <div style={styles.rawTextContainer}>
            <pre style={styles.rawTextPre}>{previewData.commandText}</pre>
          </div>
        </div>
      );
    }

    if (artifact.kind === "CSV_TIMESERIES" || artifact.kind === "CSV_TABLE" || artifact.mimeType === "text/csv") {
      return (
        <CsvPreview
          headers={previewData.headers || []}
          previewRows={previewData.previewRows || []}
          totalRows={previewData.totalRows}
          title={artifact.name}
        />
      );
    }

    if (artifact.kind === "STATUS_REPORT" || artifact.mimeType === "text/markdown") {
      return (
        <div style={styles.markdownScroll}>
          <MarkdownViewer content={typeof previewData === "string" ? previewData : String(previewData)} />
        </div>
      );
    }

    if (artifact.kind === "PLOT" || artifact.mimeType.startsWith("image/")) {
      return (
        <div style={styles.plotContainer}>
          <img src={resolveApiUrl(previewData.url || artifact.downloadUrl)} alt={artifact.name} style={styles.plotImage} />
        </div>
      );
    }

    // Default raw code/text file preview
    return (
      <div style={styles.rawTextContainer}>
        <pre style={styles.rawTextPre}>
          {typeof previewData === "string" ? previewData : JSON.stringify(previewData, null, 2)}
        </pre>
      </div>
    );
  };

  return (
    <div style={styles.overlay}>
      <div style={styles.modal}>
        {/* Modal Header */}
        <div style={styles.modalHeader}>
          <div style={styles.meta}>
            {getArtifactIcon(artifact.kind)}
            <div>
              <div style={styles.name}>{artifact.name}</div>
              <div style={styles.path}>{artifact.path}</div>
            </div>
          </div>
          <div style={styles.actions}>
            <a
              href={resolveApiUrl(artifact.downloadUrl)}
              download
              className="btn btn-secondary"
              style={{ padding: "4px 10px", fontSize: "12px" }}
            >
              <Download size={12} />
              <span>下载 ({formatBytes(artifact.sizeBytes)})</span>
            </a>
            {onClose && (
              <button onClick={onClose} style={styles.closeBtn}>
                &times;
              </button>
            )}
          </div>
        </div>

        {/* Modal Body */}
        <div style={styles.modalBody}>
          {renderPreviewContent()}
        </div>
      </div>
    </div>
  );
};

// Helpers
const getArtifactIcon = (kind: string) => {
  const color = "var(--primary-color)";
  switch (kind) {
    case "LOAD_CASE":
      return <FileText size={20} color={color} />;
    case "COMMAND_STREAM":
      return <FileCode size={20} color={color} />;
    case "PLOT":
      return <Image size={20} color={color} />;
    default:
      return <FileText size={20} color={color} />;
  }
};

const formatBytes = (bytes?: number) => {
  if (bytes === undefined) return "未知大小";
  if (bytes === 0) return "0 Bytes";
  const k = 1024;
  const sizes = ["Bytes", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + " " + sizes[i];
};

// 总览制品按荷载类型分名（real_earthquake_* / real_wind_*，见后端
// OPTIMIZATION_OVERVIEW_ARTIFACT_NAMES）。此前只识别地震那一个，风工况优化
// 的总览会落回通用 JSON 预览；面板的响应列现在由 responseTargets 动态生成，
// 两种工况共用同一个面板即可。
const BASELINE_OPTIMIZATION_OVERVIEW_NAMES = new Set([
  "real_earthquake_workflow_overview.json",
  "real_wind_workflow_overview.json",
]);

const isBaselineOptimizationOverview = (artifact: Artifact, previewData: any) =>
  BASELINE_OPTIMIZATION_OVERVIEW_NAMES.has(artifact.name) ||
  (
    previewData?.mode === "real_baseline_optimization" &&
    previewData?.doeContract &&
    previewData?.sampleResponses
  );

const styles: Record<string, React.CSSProperties> = {
  overlay: {
    position: "fixed",
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    backgroundColor: "var(--bg-overlay)",
    backdropFilter: "blur(4px)",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    zIndex: 999,
    padding: "20px"
  },
  modal: {
    backgroundColor: "var(--bg-secondary)",
    border: "1px solid var(--border-color)",
    borderRadius: "6px",
    width: "100%",
    maxWidth: "900px",
    maxHeight: "85vh",
    display: "flex",
    flexDirection: "column",
    boxShadow: "var(--shadow-lg)"
  },
  modalHeader: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    padding: "12px 16px",
    borderBottom: "1px solid var(--border-color)",
    backgroundColor: "var(--bg-tertiary)"
  },
  meta: {
    display: "flex",
    alignItems: "center",
    gap: 12
  },
  name: {
    fontSize: "13px",
    fontWeight: 600,
    color: "var(--text-primary)"
  },
  path: {
    fontSize: "11px",
    color: "var(--text-muted)",
    fontFamily: "var(--font-mono)"
  },
  actions: {
    display: "flex",
    alignItems: "center",
    gap: 12
  },
  closeBtn: {
    background: "none",
    border: "none",
    color: "var(--text-muted)",
    fontSize: "20px",
    cursor: "pointer",
    padding: "0 4px",
    transition: "color 0.15s ease"
  },
  modalBody: {
    padding: "16px",
    overflowY: "auto",
    flex: 1
  },
  loadingContainer: {
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    padding: "40px 0"
  },
  noPreviewContainer: {
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    justifyContent: "center",
    padding: "40px 20px",
    textAlign: "center"
  },
  plotContainer: {
    display: "flex",
    justifyContent: "center",
    alignItems: "center",
    backgroundColor: "var(--bg-primary)",
    border: "1px solid var(--border-color)",
    padding: "20px",
    borderRadius: "4px"
  },
  plotImage: {
    maxWidth: "100%",
    maxHeight: "450px",
    objectFit: "contain"
  },
  markdownScroll: {
    maxHeight: "500px",
    overflowY: "auto",
    backgroundColor: "var(--bg-secondary)",
    border: "1px solid var(--border-color)",
    borderRadius: "4px",
    padding: "12px"
  },
  rawTextContainer: {
    backgroundColor: "var(--bg-primary)",
    border: "1px solid var(--border-color)",
    borderRadius: "4px",
    padding: "12px",
    maxHeight: "400px",
    overflow: "auto"
  },
  rawTextPre: {
    margin: 0,
    fontSize: "12px",
    fontFamily: "var(--font-mono)",
    color: "var(--text-secondary)",
    whiteSpace: "pre-wrap"
  },
  hashBox: {
    display: "flex",
    alignItems: "center",
    gap: 8,
    backgroundColor: "var(--bg-primary)",
    border: "1px solid var(--border-color)",
    borderRadius: "4px",
    padding: "8px 10px"
  },
  hashLabel: {
    fontSize: "11px",
    color: "var(--text-muted)",
    fontWeight: 600
  },
  hashValue: {
    fontFamily: "var(--font-mono)",
    fontSize: "11px",
    color: "var(--primary-color)",
    wordBreak: "break-all"
  },
  reportWrapper: {
    display: "flex",
    flexDirection: "column",
    gap: 12
  },
  reportHeader: {
    display: "flex",
    alignItems: "center",
    gap: 8,
    borderBottom: "1px solid var(--border-color)",
    paddingBottom: "8px"
  }
};

if (typeof document !== "undefined") {
  const styleEl = document.createElement("style");
  styleEl.innerHTML = `
    button[style*="closeBtn"]:hover {
      color: var(--text-primary) !important;
    }
  `;
  document.head.appendChild(styleEl);
}
export default ArtifactPreview;
