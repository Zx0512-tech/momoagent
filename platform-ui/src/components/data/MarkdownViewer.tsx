import React from "react";

interface MarkdownViewerProps {
  content: string;
}

export const MarkdownViewer: React.FC<MarkdownViewerProps> = ({ content }) => {
  // Simple markdown parser to avoid external packages
  const lines = content.split("\n");
  const renderedElements: React.ReactNode[] = [];
  
  let inCodeBlock = false;
  let codeLines: string[] = [];
  let inTable = false;
  let tableHeaders: string[] = [];
  let tableRows: string[][] = [];

  const flushTable = (key: number) => {
    if (tableHeaders.length > 0 || tableRows.length > 0) {
      renderedElements.push(
        <div key={`table-${key}`} style={styles.tableContainer}>
          <table style={styles.table}>
            {tableHeaders.length > 0 && (
              <thead>
                <tr>
                  {tableHeaders.map((th, i) => (
                    <th key={i} style={styles.th}>{th}</th>
                  ))}
                </tr>
              </thead>
            )}
            <tbody>
              {tableRows.map((row, i) => (
                <tr key={i}>
                  {row.map((cell, j) => (
                    <td key={j} style={styles.td}>{cell}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
      tableHeaders = [];
      tableRows = [];
      inTable = false;
    }
  };

  const flushCodeBlock = (key: number) => {
    if (codeLines.length > 0) {
      renderedElements.push(
        <pre key={`code-${key}`} style={styles.pre}>
          <code style={styles.code}>{codeLines.join("\n")}</code>
        </pre>
      );
      codeLines = [];
      inCodeBlock = false;
    }
  };

  lines.forEach((line, index) => {
    const trimmed = line.trim();

    // Code blocks
    if (trimmed.startsWith("```")) {
      if (inCodeBlock) {
        flushCodeBlock(index);
      } else {
        if (inTable) flushTable(index);
        inCodeBlock = true;
      }
      return;
    }

    if (inCodeBlock) {
      codeLines.push(line);
      return;
    }

    // Tables
    if (trimmed.startsWith("|")) {
      if (inTable) {
        // Check if it's separator line e.g. |---|
        if (trimmed.includes("---") || trimmed.includes("===")) {
          return;
        }
        const cells = line.split("|").slice(1, -1).map(c => c.trim());
        tableRows.push(cells);
      } else {
        if (inTable) flushTable(index); // Flush previous if any
        inTable = true;
        const cells = line.split("|").slice(1, -1).map(c => c.trim());
        tableHeaders = cells;
      }
      return;
    } else {
      if (inTable) {
        flushTable(index);
      }
    }

    // Headings
    if (trimmed.startsWith("# ")) {
      renderedElements.push(
        <h1 key={index} style={styles.h1}>{trimmed.replace("# ", "")}</h1>
      );
    } else if (trimmed.startsWith("## ")) {
      renderedElements.push(
        <h2 key={index} style={styles.h2}>{trimmed.replace("## ", "")}</h2>
      );
    } else if (trimmed.startsWith("### ")) {
      renderedElements.push(
        <h3 key={index} style={styles.h3}>{trimmed.replace("### ", "")}</h3>
      );
    }
    // Lists
    else if (trimmed.startsWith("- ") || trimmed.startsWith("* ")) {
      renderedElements.push(
        <li key={index} style={styles.li}>{trimmed.slice(2)}</li>
      );
    }
    // Blockquote
    else if (trimmed.startsWith("> ")) {
      renderedElements.push(
        <blockquote key={index} style={styles.blockquote}>
          {trimmed.slice(2)}
        </blockquote>
      );
    }
    // Empty line
    else if (trimmed === "") {
      renderedElements.push(<div key={index} style={styles.spacing} />);
    }
    // Paragraph
    else {
      renderedElements.push(
        <p key={index} style={styles.p}>{line}</p>
      );
    }
  });

  // Cleanup final blocks
  if (inCodeBlock) flushCodeBlock(lines.length);
  if (inTable) flushTable(lines.length);

  return <div style={styles.container}>{renderedElements}</div>;
};

const styles: Record<string, React.CSSProperties> = {
  container: {
    padding: "8px",
    display: "flex",
    flexDirection: "column",
    gap: 8,
    lineHeight: 1.6
  },
  h1: {
    fontSize: "18px",
    fontWeight: 700,
    borderBottom: "1px solid var(--border-color)",
    paddingBottom: "6px",
    marginTop: "16px",
    color: "var(--text-primary)"
  },
  h2: {
    fontSize: "15px",
    fontWeight: 600,
    borderBottom: "1px solid var(--border-color)",
    paddingBottom: "4px",
    marginTop: "12px",
    color: "var(--text-primary)"
  },
  h3: {
    fontSize: "13px",
    fontWeight: 600,
    marginTop: "8px",
    color: "var(--text-primary)"
  },
  p: {
    fontSize: "13px",
    color: "var(--text-secondary)"
  },
  li: {
    fontSize: "13px",
    color: "var(--text-secondary)",
    marginLeft: "18px"
  },
  blockquote: {
    borderLeft: "3px solid var(--primary-color)",
    paddingLeft: "10px",
    margin: "8px 0",
    color: "var(--text-secondary)",
    backgroundColor: "var(--primary-soft)"
  },
  spacing: {
    height: "6px"
  },
  pre: {
    backgroundColor: "var(--bg-primary)",
    border: "1px solid var(--border-color)",
    padding: "10px",
    borderRadius: "4px",
    overflow: "auto"
  },
  code: {
    fontFamily: "var(--font-mono)",
    fontSize: "12px",
    color: "var(--error-color)" /* 行内代码用错误红区分于正文，浅底上仍过 AA */
  },
  tableContainer: {
    overflowX: "auto",
    border: "1px solid var(--border-color)",
    borderRadius: "4px",
    margin: "8px 0"
  },
  table: {
    width: "100%",
    borderCollapse: "collapse",
    fontSize: "12px",
    textAlign: "left"
  },
  th: {
    backgroundColor: "var(--bg-primary)",
    padding: "8px 10px",
    fontWeight: 600,
    color: "var(--text-secondary)",
    borderBottom: "1px solid var(--border-color)"
  },
  td: {
    padding: "6px 10px",
    borderBottom: "1px solid var(--border-color)",
    color: "var(--text-secondary)"
  }
};
export default MarkdownViewer;
