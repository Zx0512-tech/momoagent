import React from "react";
import { Link, useLocation } from "react-router-dom";
import {
  LayoutDashboard,
  Zap,
  PlaySquare,
  FileBarChart2,
  FlaskConical,
  Brain,
  Sliders,
  FolderOpen,
  Wrench,
  Bot,
  ChevronLeft,
  ChevronRight
} from "lucide-react";

interface SidebarProps {
  collapsed: boolean;
  onToggle: () => void;
}

export const Sidebar: React.FC<SidebarProps> = ({ collapsed, onToggle }) => {
  const location = useLocation();

  const menuItems = [
    { name: "总览主面板", path: "/", icon: LayoutDashboard },
    { name: "工程智能体", path: "/agent", icon: Bot },
    { name: "阻尼器基准配置", path: "/damper-base", icon: Wrench },
    { name: "试验设计", path: "/experiment-design", icon: FlaskConical },
    { name: "荷载配置", path: "/loads", icon: Zap },
    { name: "求解器批处理计算", path: "/solver", icon: PlaySquare },
    { name: "结果提取与报告", path: "/results", icon: FileBarChart2 },
    { name: "代理模型与主动学习", path: "/surrogate", icon: Brain },
    { name: "多目标优化与决策", path: "/optimization", icon: Sliders },
    { name: "平台制品库浏览器", path: "/artifacts", icon: FolderOpen }
  ];

  return (
    <div style={{
      ...styles.sidebar,
      width: collapsed ? "var(--sidebar-collapsed-width)" : "var(--sidebar-width)"
    }}>
      {/* Menu list */}
      <div style={styles.menuContainer}>
        {menuItems.map(item => {
          // Check if current path matches
          const isActive = item.path === "/" 
            ? location.pathname === "/" 
            : location.pathname.startsWith(item.path);

          return (
            <Link
              key={item.path}
              to={item.path}
              style={{
                ...styles.menuItem,
                backgroundColor: isActive ? "var(--primary-soft)" : "transparent",
                color: isActive ? "var(--primary-color)" : "var(--text-secondary)",
                borderLeft: isActive ? "3px solid var(--primary-color)" : "3px solid transparent",
                paddingLeft: collapsed ? "15px" : "12px"
              }}
            >
              <item.icon size={16} style={styles.icon} />
              {!collapsed && <span style={styles.menuText}>{item.name}</span>}
            </Link>
          );
        })}
      </div>

      {/* Collapse toggle button at the bottom */}
      <button onClick={onToggle} style={styles.toggleBtn}>
        {collapsed ? <ChevronRight size={16} /> : <ChevronLeft size={16} />}
        {!collapsed && <span style={{ marginLeft: 8 }}>收起侧边栏</span>}
      </button>
    </div>
  );
};

const styles: Record<string, React.CSSProperties> = {
  sidebar: {
    backgroundColor: "var(--bg-secondary)",
    borderRight: "1px solid var(--border-color)",
    display: "flex",
    flexDirection: "column",
    justifyContent: "space-between",
    height: "100%",
    transition: "width 0.2s cubic-bezier(0.4, 0, 0.2, 1)",
    userSelect: "none"
  },
  menuContainer: {
    display: "flex",
    flexDirection: "column",
    padding: "12px 0",
    gap: 4
  },
  menuItem: {
    display: "flex",
    alignItems: "center",
    height: "36px",
    textDecoration: "none",
    fontSize: "12px",
    fontWeight: 500,
    transition: "background-color 0.15s ease, color 0.15s ease"
  },
  icon: {
    flexShrink: 0
  },
  menuText: {
    marginLeft: "10px",
    whiteSpace: "nowrap",
    overflow: "hidden",
    textOverflow: "ellipsis"
  },
  toggleBtn: {
    display: "flex",
    alignItems: "center",
    justifyContent: "flex-start",
    padding: "12px 16px",
    border: "none",
    borderTop: "1px solid var(--border-color)",
    backgroundColor: "transparent",
    color: "var(--text-muted)",
    cursor: "pointer",
    fontSize: "11px",
    fontWeight: 500,
    transition: "color 0.15s ease"
  }
};
