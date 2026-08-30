import React, { useEffect, useState } from "react";
import { api } from "../../api/client";
import { Activity, ShieldAlert, Wifi } from "lucide-react";

export const TopBar: React.FC = () => {
  const [health, setHealth] = useState<{ status: string; service: string; version?: string } | null>(null);
  const [apiMode, setApiMode] = useState<string>("mock");

  useEffect(() => {
    // Check API mode from environment
    const mode = import.meta.env.VITE_API_MODE || "mock";
    setApiMode(mode);

    // Call health API
    const checkHealth = async () => {
      try {
        const res = await api.getHealth();
        setHealth({ status: res.status, service: res.service, version: res.version });
      } catch {
        setHealth({ status: "ERROR", service: "backend-disconnected" });
      }
    };

    checkHealth();
    const interval = setInterval(checkHealth, 10000);
    return () => clearInterval(interval);
  }, []);

  const isHealthy = health?.status === "OK";
  const isMock = apiMode === "mock";

  return (
    <div style={styles.container}>
      <div style={styles.logoGroup}>
        <Activity size={18} color="var(--primary-color)" />
        <span style={styles.title}>MOMO 桥梁分析与优化平台</span>
        <span style={styles.version}>v{health?.version || import.meta.env.VITE_APP_VERSION}</span>
      </div>

      <div style={styles.statusGroup}>
        {/* API Mode Tag */}
        <span style={{
          ...styles.tag,
          backgroundColor: isMock ? "var(--info-soft)" : "var(--success-soft)",
          color: isMock ? "var(--info-color)" : "var(--success-color)",
          borderColor: isMock ? "var(--info-color)" : "var(--success-color)"
        }}>
          {isMock ? "仿真模拟模式 (Mock API)" : "实时运行模式 (Live API)"}
        </span>

        {/* Health status */}
        <div style={styles.healthIndicator}>
          {isHealthy ? (
            <>
              <Wifi size={14} color="var(--success-color)" />
              <span style={{ color: "var(--success-color)", fontWeight: 500 }}>后端服务在线</span>
            </>
          ) : (
            <>
              <ShieldAlert size={14} color="var(--error-color)" className="pulse" />
              <span style={{ color: "var(--error-color)", fontWeight: 500 }}>
                {health?.status === "ERROR" ? "后端未连接" : "检测后端..."}
              </span>
            </>
          )}
        </div>
      </div>
    </div>
  );
};

const styles: Record<string, React.CSSProperties> = {
  container: {
    height: "var(--topbar-height)",
    backgroundColor: "var(--bg-secondary)",
    borderBottom: "1px solid var(--border-color)",
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    padding: "0 16px",
    zIndex: 100
  },
  logoGroup: {
    display: "flex",
    alignItems: "center",
    gap: 8
  },
  title: {
    fontSize: "14px",
    fontWeight: 600,
    letterSpacing: "0.5px"
  },
  version: {
    color: "var(--text-secondary)",
    fontSize: "11px"
  },
  statusGroup: {
    display: "flex",
    alignItems: "center",
    gap: 16
  },
  tag: {
    fontSize: "11px",
    padding: "2px 8px",
    borderRadius: "12px",
    border: "1px solid",
    fontWeight: 500
  },
  healthIndicator: {
    display: "flex",
    alignItems: "center",
    gap: 6,
    fontSize: "12px"
  }
};
