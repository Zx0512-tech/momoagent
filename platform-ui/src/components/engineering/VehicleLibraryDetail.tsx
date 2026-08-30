import React, { useMemo } from "react";
import type { VehicleLibraryOption } from "../../api/types";

interface VehicleLibraryDetailProps {
  library?: VehicleLibraryOption;
}

export const VehicleLibraryDetail: React.FC<VehicleLibraryDetailProps> = ({ library }) => {
  const peakHour = useMemo(() => {
    if (!library) return null;
    return library.hourlyFlow.reduce((peak, item) =>
      item.vehiclesPerHour > peak.vehiclesPerHour ? item : peak
    );
  }, [library]);

  if (!library) {
    return (
      <div style={styles.empty}>
        请选择车辆库后查看 24h 分时流量和重车占比。
      </div>
    );
  }

  const maxFlow = Math.max(...library.hourlyFlow.map(item => item.vehiclesPerHour), 1);

  return (
    <div style={styles.wrapper}>
      <div style={styles.header}>
        <div>
          <div style={styles.title}>{library.label}</div>
          <div style={styles.description}>{library.description}</div>
        </div>
      </div>

      <div style={styles.metricGrid}>
        <Metric label="24h 总车流量" value={`${library.totalVehicles24h.toLocaleString()} 辆`} />
        <Metric label="平均重车占比" value={`${(library.heavyVehicleRatio * 100).toFixed(1)}%`} />
        <Metric label="峰值小时" value={peakHour ? `${peakHour.hour}:00 / ${peakHour.vehiclesPerHour} 辆` : "-"} />
      </div>

      <div style={styles.hourGrid}>
        {library.hourlyFlow.map(item => (
          <div key={item.hour} style={styles.hourItem} title={`${item.hour}:00 ${item.vehiclesPerHour} 辆/h，重车 ${(item.heavyVehicleRatio * 100).toFixed(1)}%`}>
            <div style={styles.hourLabel}>{item.hour}</div>
            <div style={styles.barTrack}>
              <div style={{ ...styles.barFill, height: `${Math.max((item.vehiclesPerHour / maxFlow) * 100, 4)}%` }} />
            </div>
            <div style={styles.hourValue}>{item.vehiclesPerHour}</div>
            <div style={styles.heavyRatio}>{(item.heavyVehicleRatio * 100).toFixed(0)}%</div>
          </div>
        ))}
      </div>
      <div style={styles.legend}>
        <span>上方数字为小时，下方为辆/h 与重车占比。</span>
      </div>
    </div>
  );
};

const Metric: React.FC<{ label: string; value: string }> = ({ label, value }) => (
  <div style={styles.metric}>
    <span style={styles.metricLabel}>{label}</span>
    <strong style={styles.metricValue}>{value}</strong>
  </div>
);

const styles: Record<string, React.CSSProperties> = {
  wrapper: {
    backgroundColor: "var(--bg-primary)",
    border: "1px solid var(--border-color)",
    borderRadius: 4,
    padding: "10px 12px",
    marginBottom: 12
  },
  empty: {
    backgroundColor: "var(--bg-primary)",
    border: "1px solid var(--border-color)",
    borderRadius: 4,
    padding: "12px",
    fontSize: "12px",
    color: "var(--text-muted)",
    marginBottom: 12
  },
  header: {
    display: "flex",
    justifyContent: "space-between",
    gap: 12,
    marginBottom: 10
  },
  title: {
    fontSize: "13px",
    fontWeight: 700,
    color: "var(--text-primary)"
  },
  description: {
    fontSize: "11px",
    color: "var(--text-secondary)",
    marginTop: 3
  },
  metricGrid: {
    display: "grid",
    gridTemplateColumns: "repeat(3, minmax(0, 1fr))",
    gap: 8,
    marginBottom: 10
  },
  metric: {
    border: "1px solid var(--border-color)",
    borderRadius: 4,
    padding: "7px 8px",
    backgroundColor: "var(--bg-secondary)"
  },
  metricLabel: {
    display: "block",
    fontSize: "10px",
    color: "var(--text-muted)",
    marginBottom: 3
  },
  metricValue: {
    fontSize: "12px",
    color: "var(--text-primary)"
  },
  hourGrid: {
    display: "grid",
    gridTemplateColumns: "repeat(24, minmax(22px, 1fr))",
    gap: 4,
    alignItems: "end"
  },
  hourItem: {
    minWidth: 0,
    textAlign: "center"
  },
  hourLabel: {
    fontSize: "9px",
    color: "var(--text-muted)",
    marginBottom: 3
  },
  barTrack: {
    height: 58,
    display: "flex",
    alignItems: "flex-end",
    justifyContent: "center",
    backgroundColor: "var(--bg-secondary)",
    border: "1px solid var(--border-color)",
    borderRadius: 3,
    overflow: "hidden"
  },
  barFill: {
    width: "100%",
    backgroundColor: "var(--primary-color)",
    opacity: 0.72
  },
  hourValue: {
    fontSize: "9px",
    color: "var(--text-secondary)",
    marginTop: 3
  },
  heavyRatio: {
    fontSize: "9px",
    color: "var(--text-muted)"
  },
  legend: {
    fontSize: "10px",
    color: "var(--text-muted)",
    marginTop: 8
  }
};
