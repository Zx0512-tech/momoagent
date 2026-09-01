import React, { useCallback, useEffect, useState } from "react";
import { api, resolveApiUrl } from "../../api/client";
import type { Artifact, ArtifactKind } from "../../api/types";
import { LoadingSpinner } from "../../components/feedback/LoadingSpinner";
import { ErrorPanel } from "../../components/feedback/ErrorPanel";
import { ArtifactPreview } from "../../components/artifact/ArtifactPreview";
import { FolderOpen, Filter, Search, Download, Eye, ShieldCheck, ChevronLeft, ChevronRight, Database, RefreshCw } from "lucide-react";

export const ArtifactBrowserPage: React.FC = () => {
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Filter/Search states
  const [selectedKind, setSelectedKind] = useState<string>("");
  const [selectedSource, setSelectedSource] = useState<string>("");
  const [searchQuery, setSearchQuery] = useState<string>("");
  const [submittedQuery, setSubmittedQuery] = useState<string>("");
  const [currentPage, setCurrentPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);
  const [totalItems, setTotalItems] = useState(0);
  const [syncing, setSyncing] = useState(false);
  const [syncMessage, setSyncMessage] = useState<string | null>(null);

  const [selectedArtifact, setSelectedArtifact] = useState<Artifact | null>(null);

  const pageSize = 12;

  const loadArtifacts = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.getArtifacts({
        kind: selectedKind || undefined,
        source: selectedSource === "REAL_OPTIMIZATION_HISTORY" ? selectedSource : undefined,
        page: currentPage,
        pageSize
      });
      
      // Client-side search filter as api fallback
      let data = res.data;
      if (submittedQuery !== "") {
        const query = submittedQuery.toLowerCase();
        data = data.filter(a =>
          a.name.toLowerCase().includes(query) ||
          a.path.toLowerCase().includes(query)
        );
      }

      setArtifacts(data);
      setTotalItems(res.pagination.totalItems);
      setTotalPages(res.pagination.totalPages);
    } catch (e: any) {
      setError(e.message || "无法拉取文件制品清单");
    } finally {
      setLoading(false);
    }
  }, [currentPage, selectedKind, selectedSource, submittedQuery]);

  useEffect(() => {
    void loadArtifacts();
  }, [loadArtifacts]);

  const handleSearchSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const nextQuery = searchQuery.trim();
    if (currentPage === 1 && submittedQuery === nextQuery) {
      void loadArtifacts();
      return;
    }
    setSubmittedQuery(nextQuery);
    setCurrentPage(1);
  };

  const handlePageChange = (page: number) => {
    if (page >= 1 && page <= totalPages) {
      setCurrentPage(page);
    }
  };

  const handleSyncHistory = async () => {
    setSyncing(true);
    setSyncMessage(null);
    try {
      const result = await api.syncRealOptimizationHistory();
      setSyncMessage(
        `已核对 ${result.scannedRuns} 次历史运行：新增 ${result.importedArtifacts} 个、更新 ${result.updatedArtifacts} 个制品，跳过 ${result.skippedRuns} 个非完整或干跑目录。`
      );
      await loadArtifacts();
    } catch (e: any) {
      setSyncMessage(e.message || "真实优化历史同步失败");
    } finally {
      setSyncing(false);
    }
  };

  const kindsList: ArtifactKind[] = [
    "LOAD_CASE",
    "JSON_SUMMARY",
    "CSV_TIMESERIES",
    "CSV_TABLE",
    "RAW_DATA",
    "PARITY_REPORT",
    "SURROGATE_MODEL",
    "OPTIMIZATION_REPORT",
    "DECISION_REPORT",
    "STATUS_REPORT",
    "LOG",
    "COMMAND_STREAM",
    "PLOT",
    "FEM_MODEL",
    "BINARY"
  ];

  return (
    <div className="page-container">
      {/* Page Header */}
      <div style={styles.header}>
        <div>
          <h1 style={styles.title}>平台计算制品浏览器</h1>
          <p style={styles.subtitle}>
            管理与追溯系统运行生成的所有数据资产，支持荷载时程、命令脚本、回归报告及分析摘要预览
          </p>
        </div>
      </div>

      <div className="panel" style={styles.storageInfo}>
        <Database size={18} color="var(--primary-color)" />
        <div style={{ flex: 1 }}>
          <div style={styles.storageTitle}>这里保存的是平台已登记的计算制品</div>
          <div style={styles.storageText}>
            包括输入荷载、JSON/CSV 结果、命令流、代理模型、优化与决策报告、状态日志和图件。平台保存元数据、预览、原始文件字节与 SHA256；“真实优化历史”仅接收已完成且非 dry-run 的运行目录。
          </div>
          {syncMessage && <div role="status" style={styles.syncMessage}>{syncMessage}</div>}
        </div>
        <button className="btn btn-secondary" onClick={handleSyncHistory} disabled={syncing} style={{ whiteSpace: "nowrap" }}>
          <RefreshCw size={13} className={syncing ? "spin" : undefined} />
          {syncing ? "正在核对" : "同步真实优化历史"}
        </button>
      </div>

      {/* Filter and Search Bar */}
      <div className="panel" style={styles.filterBar}>
        <form onSubmit={handleSearchSubmit} style={styles.filterForm}>
          {/* Kind Select */}
          <div style={styles.filterGroup}>
            <Filter size={14} color="var(--text-muted)" />
            <span style={{ fontSize: "12px", fontWeight: 500 }}>制品类别过滤:</span>
            <select
              className="form-control"
              aria-label="制品类别过滤"
              style={{ width: "160px", padding: "4px 8px" }}
              value={selectedKind}
              onChange={e => {
                setSelectedKind(e.target.value);
                setCurrentPage(1);
              }}
            >
              <option value="">全部类别 (All)</option>
              {kindsList.map(k => (
                <option key={k} value={k}>{k}</option>
              ))}
            </select>
          </div>

          <div style={styles.filterGroup}>
            <Database size={14} color="var(--text-muted)" />
            <span style={{ fontSize: "12px", fontWeight: 500 }}>来源:</span>
            <select
              className="form-control"
              aria-label="制品来源过滤"
              style={{ width: "180px", padding: "4px 8px" }}
              value={selectedSource}
              onChange={e => {
                setSelectedSource(e.target.value);
                setCurrentPage(1);
              }}
            >
              <option value="">全部来源</option>
              <option value="REAL_OPTIMIZATION_HISTORY">真实优化历史</option>
            </select>
          </div>

          {/* Search Query */}
          <div style={{ ...styles.filterGroup, flex: 1 }}>
            <Search size={14} color="var(--text-muted)" />
            <input
              className="form-control"
              aria-label="搜索制品名称或存储路径"
              style={{ padding: "4px 8px" }}
              placeholder="搜索制品名称或存储路径..."
              value={searchQuery}
              onChange={e => setSearchQuery(e.target.value)}
            />
            <button type="submit" className="btn btn-secondary" style={{ padding: "4px 10px", fontSize: "12px" }}>
              查询
            </button>
          </div>
        </form>
      </div>

      {/* Loading & Errors */}
      {loading ? (
        <div style={{ display: "flex", justifyContent: "center", padding: "80px 0" }}>
          <LoadingSpinner />
        </div>
      ) : error ? (
        <ErrorPanel message={error} onRetry={loadArtifacts} />
      ) : artifacts.length === 0 ? (
        <div className="panel" style={{ textAlign: "center", padding: "60px 0" }}>
          <FolderOpen size={40} color="var(--text-muted)" style={{ marginBottom: 12 }} />
          <div style={{ color: "var(--text-secondary)", fontWeight: 600 }}>未查询到匹配的制品文件</div>
          <div style={{ color: "var(--text-muted)", fontSize: "12px", marginTop: 4 }}>
            可尝试清除过滤条件或进行新一轮计算生成。
          </div>
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          {/* Artifact List Grid */}
          <div style={styles.grid}>
            {artifacts.map(art => (
              <div key={art.artifactId} style={styles.artCard}>
                <div style={styles.artHeader}>
                  <div style={styles.nameGroup}>
                    <FolderOpen size={16} color="var(--primary-color)" />
                    <span style={styles.artName} title={art.name}>{art.name}</span>
                  </div>
                  <span className="badge badge-secondary" style={{ fontSize: "10px" }}>{art.kind}</span>
                </div>

                <div style={styles.artBody}>
                  {art.runId && (
                    <div style={styles.metaRow}>
                      <span>真实运行:</span>
                      <code style={styles.path} title={art.runId}>{art.runId}</code>
                    </div>
                  )}
                  <div style={styles.metaRow}>
                    <span>存储相对路径:</span>
                    <code style={styles.path} title={art.path}>{art.path}</code>
                  </div>
                  {art.sha256 && (
                    <div style={styles.metaRow}>
                      <span>SHA256核签:</span>
                      <code style={styles.hash} title={art.sha256}>
                        <ShieldCheck size={11} color="var(--success-color)" style={{ display: "inline", marginRight: 2 }} />
                        {art.sha256.substring(0, 16)}...
                      </code>
                    </div>
                  )}
                  <div style={styles.metaRow}>
                    <span>文件大小:</span>
                    <span style={{ color: "var(--text-secondary)" }}>{formatBytes(art.sizeBytes)}</span>
                  </div>
                </div>

                <div style={styles.artFooter}>
                  {art.canPreview ? (
                    <button
                      onClick={() => setSelectedArtifact(art)}
                      className="btn btn-secondary"
                      style={{ flex: 1, padding: "5px", fontSize: "11px" }}
                    >
                      <Eye size={12} />
                      <span>在线预览</span>
                    </button>
                  ) : (
                    <button
                      disabled
                      className="btn btn-secondary"
                      style={{ flex: 1, padding: "5px", fontSize: "11px", opacity: 0.4 }}
                    >
                      <span>暂无预览</span>
                    </button>
                  )}
                  <a
                    href={resolveApiUrl(art.downloadUrl)}
                    download
                    className="btn btn-primary"
                    style={{ flex: 1, padding: "5px", fontSize: "11px", textDecoration: "none" }}
                  >
                    <Download size={12} />
                    <span>直接下载</span>
                  </a>
                </div>
              </div>
            ))}
          </div>

          {/* Pagination bar */}
          <div style={styles.paginationBar}>
            <span style={{ fontSize: "12px", color: "var(--text-secondary)" }}>
              共 {totalItems} 个计算制品 (当前页 {artifacts.length} 个)
            </span>

            <div style={styles.pageBtnGroup}>
              <button
                disabled={currentPage === 1}
                onClick={() => handlePageChange(currentPage - 1)}
                style={styles.pageBtn}
              >
                <ChevronLeft size={16} />
              </button>
              <span style={{ fontSize: "12px", color: "var(--text-primary)", fontWeight: 600 }}>
                {currentPage} / {totalPages}
              </span>
              <button
                disabled={currentPage === totalPages}
                onClick={() => handlePageChange(currentPage + 1)}
                style={styles.pageBtn}
              >
                <ChevronRight size={16} />
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Artifact Preview Modal */}
      {selectedArtifact && (
        <ArtifactPreview
          artifact={selectedArtifact}
          onClose={() => setSelectedArtifact(null)}
        />
      )}
    </div>
  );
};

const formatBytes = (bytes?: number) => {
  if (bytes === undefined) return "未知大小";
  if (bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + " " + sizes[i];
};

const styles: Record<string, React.CSSProperties> = {
  header: {
    borderBottom: "1px solid var(--border-color)",
    paddingBottom: "14px"
  },
  title: {
    fontSize: "18px",
    fontWeight: 700,
    color: "var(--text-primary)"
  },
  subtitle: {
    fontSize: "12px",
    color: "var(--text-secondary)",
    marginTop: "4px"
  },
  filterBar: {
    padding: "10px 12px"
  },
  storageInfo: {
    display: "flex",
    alignItems: "flex-start",
    gap: 12,
    padding: "12px 14px"
  },
  storageTitle: {
    color: "var(--text-primary)",
    fontSize: "13px",
    fontWeight: 650
  },
  storageText: {
    color: "var(--text-secondary)",
    fontSize: "11px",
    lineHeight: 1.6,
    marginTop: 3
  },
  syncMessage: {
    color: "var(--primary-color)",
    fontSize: "11px",
    marginTop: 5
  },
  filterForm: {
    display: "flex",
    flexWrap: "wrap",
    gap: "24px",
    alignItems: "center",
    width: "100%"
  },
  filterGroup: {
    display: "flex",
    alignItems: "center",
    gap: 8
  },
  grid: {
    display: "grid",
    gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))",
    gap: "16px"
  },
  artCard: {
    backgroundColor: "var(--bg-secondary)",
    border: "1px solid var(--border-color)",
    borderRadius: "4px",
    padding: "12px",
    display: "flex",
    flexDirection: "column",
    justifyContent: "space-between",
    minHeight: "180px"
  },
  artHeader: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    borderBottom: "1px solid var(--border-color)",
    paddingBottom: "8px"
  },
  nameGroup: {
    display: "flex",
    alignItems: "center",
    gap: 8,
    overflow: "hidden",
    whiteSpace: "nowrap"
  },
  artName: {
    fontSize: "12px",
    fontWeight: 600,
    color: "var(--text-primary)",
    textOverflow: "ellipsis",
    overflow: "hidden"
  },
  artBody: {
    padding: "10px 0",
    display: "flex",
    flexDirection: "column",
    gap: 6
  },
  metaRow: {
    display: "flex",
    justifyContent: "space-between",
    fontSize: "11px",
    color: "var(--text-muted)",
    alignItems: "center",
    overflow: "hidden"
  },
  path: {
    fontFamily: "var(--font-mono)",
    color: "var(--text-secondary)",
    textOverflow: "ellipsis",
    overflow: "hidden",
    whiteSpace: "nowrap",
    maxWidth: "160px"
  },
  hash: {
    fontFamily: "var(--font-mono)",
    color: "var(--primary-color)"
  },
  artFooter: {
    display: "flex",
    gap: 8,
    borderTop: "1px solid var(--border-color)",
    paddingTop: "10px"
  },
  paginationBar: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    padding: "8px 0"
  },
  pageBtnGroup: {
    display: "flex",
    alignItems: "center",
    gap: 12
  },
  pageBtn: {
    background: "none",
    border: "1px solid var(--border-color)",
    borderRadius: "4px",
    padding: "4px",
    cursor: "pointer",
    display: "inline-flex",
    alignItems: "center",
    color: "var(--text-secondary)"
  }
};

// pageBtn hover style
if (typeof document !== "undefined") {
  const styleEl = document.createElement("style");
  styleEl.innerHTML = `
    button[style*="pageBtn"]:hover:not(:disabled) {
      border-color: var(--text-secondary) !important;
      color: var(--text-primary) !important;
      background-color: var(--bg-tertiary) !important;
    }
    button[style*="pageBtn"]:disabled {
      opacity: 0.3;
      cursor: not-allowed;
    }
  `;
  document.head.appendChild(styleEl);
}
export default ArtifactBrowserPage;
