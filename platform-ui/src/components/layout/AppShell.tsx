import React, { useState } from "react";
import { useLocation } from "react-router-dom";
import { Sidebar } from "./Sidebar";
import { ChatSidebar } from "./ChatSidebar";
import { TopBar } from "./TopBar";

interface AppShellProps {
  children: React.ReactNode;
}

export const AppShell: React.FC<AppShellProps> = ({ children }) => {
  const location = useLocation();
  const isChat = location.pathname === "/";

  const [sidebarCollapsed, setSidebarCollapsed] = useState<boolean>(() => {
    try {
      return localStorage.getItem("sidebar_collapsed") === "true";
    } catch {
      return false;
    }
  });

  const handleToggle = () => {
    setSidebarCollapsed(prev => {
      const next = !prev;
      try {
        localStorage.setItem("sidebar_collapsed", String(next));
      } catch {}
      return next;
    });
  };

  return (
    <div className="app-container">
      {/* 对话页使用会话历史侧栏，分析页保持原有功能导航。 */}
      {isChat ? <ChatSidebar /> : <Sidebar collapsed={sidebarCollapsed} onToggle={handleToggle} />}

      <div className="main-content">
        <TopBar />

        {/* 对话需要自行控制滚动，故不套用带内边距的 page-container。 */}
        {isChat ? (
          <div className="chat-container">{children}</div>
        ) : (
          <div className="page-container">{children}</div>
        )}
      </div>
    </div>
  );
};
