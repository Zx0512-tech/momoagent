import type React from "react";
import { Bot, User } from "lucide-react";

type MessageBubbleProps = {
  role: "USER" | "ASSISTANT";
  content: string;
  /** 助手消息下方内嵌的卡片（计划、审批、结果等）。 */
  children?: React.ReactNode;
};

/** 单条对话消息：用户消息右对齐，助手消息左对齐并可承载内嵌卡片。 */
export const MessageBubble = ({ role, content, children }: MessageBubbleProps) => {
  const isUser = role === "USER";
  return (
    <div style={isUser ? styles.rowUser : styles.rowAssistant}>
      {!isUser && (
        <div style={styles.avatar} aria-hidden="true">
          <Bot size={15} />
        </div>
      )}
      <div style={isUser ? styles.contentUser : styles.contentAssistant}>
        {content && <div style={isUser ? styles.bubbleUser : styles.bubbleAssistant}>{content}</div>}
        {children}
      </div>
      {isUser && (
        <div style={styles.avatarUser} aria-hidden="true">
          <User size={15} />
        </div>
      )}
    </div>
  );
};

const avatarBase: React.CSSProperties = {
  width: 26,
  height: 26,
  borderRadius: 6,
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  flexShrink: 0,
  marginTop: 2
};

const styles: Record<string, React.CSSProperties> = {
  rowAssistant: { display: "flex", gap: 10, alignItems: "flex-start" },
  rowUser: { display: "flex", gap: 10, alignItems: "flex-start", justifyContent: "flex-end" },
  avatar: { ...avatarBase, background: "var(--bg-tertiary)", color: "var(--primary-color)" },
  avatarUser: { ...avatarBase, background: "var(--bg-tertiary)", color: "var(--text-secondary)" },
  // 助手内容可以铺满以容纳图表与表格；用户消息保持窄气泡。
  contentAssistant: { flex: 1, minWidth: 0 },
  contentUser: { maxWidth: "72%", display: "flex", flexDirection: "column", alignItems: "flex-end" },
  bubbleAssistant: {
    lineHeight: 1.7,
    whiteSpace: "pre-wrap",
    wordBreak: "break-word",
    paddingTop: 3
  },
  bubbleUser: {
    background: "var(--bg-tertiary)",
    border: "1px solid var(--border-color)",
    borderRadius: 10,
    padding: "9px 13px",
    lineHeight: 1.7,
    whiteSpace: "pre-wrap",
    wordBreak: "break-word"
  }
};
