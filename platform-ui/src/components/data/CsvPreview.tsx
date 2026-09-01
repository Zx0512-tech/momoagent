import React from "react";
import { FileSpreadsheet } from "lucide-react";

interface CsvPreviewProps {
  headers: string[];
  previewRows: string[][];
  totalRows?: number;
  title?: string;
}

export const CsvPreview: React.FC<CsvPreviewProps> = ({ headers, previewRows, totalRows, title }) => {
  return (
    <div style={styles.container}>
      <div style={styles.header}>
        <div style={styles.titleGroup}>
          <FileSpreadsheet size={16} color="var(--primary-color)" />
          <span style={styles.title}>{title || "CSV 数据时程预览 (仅展示前 200 行)"}</span>
        </div>
        {totalRows !== undefined && (
          <span style={styles.totalRows}>共 {totalRows} 行数据</span>
        )}
      </div>

      <div style={styles.tableWrapper}>
        <table style={styles.table}>
          <thead>
            <tr>
              <th style={styles.indexTh}>#</th>
              {headers.map((h, i) => (
                <th key={i}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {previewRows.map((row, rowIndex) => (
              <tr key={rowIndex}>
                <td style={styles.indexTd}>{rowIndex + 1}</td>
                {row.map((cell, colIndex) => (
                  <td key={colIndex}>{cell}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
};

const styles: Record<string, React.CSSProperties> = {
  container: {
    backgroundColor: "var(--bg-secondary)",
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
    padding: "10px 12px",
    borderBottom: "1px solid var(--border-color)",
    backgroundColor: "var(--bg-tertiary)"
  },
  titleGroup: {
    display: "flex",
    alignItems: "center",
    gap: 8
  },
  title: {
    fontSize: "12px",
    fontWeight: 600
  },
  totalRows: {
    fontSize: "11px",
    color: "var(--text-muted)",
    fontWeight: 500
  },
  tableWrapper: {
    overflow: "auto",
    maxHeight: "300px"
  },
  table: {
    width: "100%",
    borderCollapse: "collapse",
    fontSize: "12px",
    fontFamily: "var(--font-mono)",
    textAlign: "left"
  },
  indexTh: {
    width: "50px",
    color: "var(--text-muted)",
    textAlign: "center"
  },
  indexTd: {
    color: "var(--text-muted)",
    textAlign: "center",
    borderRight: "1px solid var(--border-color)",
    backgroundColor: "var(--bg-tertiary)"
  }
};
// Add table styles since inline styles don't support pseudo selectors
if (typeof document !== "undefined") {
  const styleEl = document.createElement("style");
  styleEl.innerHTML = `
    table[style*="table"] th {
      padding: 8px 10px;
      border-bottom: 1px solid var(--border-color);
      background-color: var(--bg-primary);
      color: var(--text-secondary);
      font-weight: 600;
      position: sticky;
      top: 0;
    }
    table[style*="table"] td {
      padding: 6px 10px;
      border-bottom: 1px solid var(--border-color);
    }
    table[style*="table"] tbody tr:hover {
      background-color: var(--bg-tertiary);
    }
  `;
  document.head.appendChild(styleEl);
}
export default CsvPreview;
