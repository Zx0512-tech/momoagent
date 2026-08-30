import { useEffect, useRef, useState } from "react";
import type React from "react";
import { Paperclip, Send, X } from "lucide-react";

type ComposerProps = {
  busy: boolean;
  /** 附件仅在会话首条工程消息前可选，标准化流程进行中禁用。 */
  attachmentDisabled: boolean;
  /** 空态示例提示词填入输入框；每次点击都会覆盖当前草稿。 */
  presetValue?: string;
  onSend: (content: string, file?: File) => void;
};

/** 底部输入区：Enter 发送、Shift+Enter 换行、可选荷载附件。 */
export const Composer = ({ busy, attachmentDisabled, presetValue, onSend }: ComposerProps) => {
  const [value, setValue] = useState("");
  const [file, setFile] = useState<File>();
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (presetValue) setValue(presetValue);
  }, [presetValue]);

  const canSend = value.trim().length > 0 && !busy;

  const submit = () => {
    if (!canSend) return;
    onSend(value.trim(), file);
    setValue("");
    setFile(undefined);
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  const handleKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      submit();
    }
  };

  return (
    <div style={styles.wrapper}>
      {file && (
        <div style={styles.attachment}>
          <Paperclip size={13} />
          <span style={styles.attachmentName}>{file.name}</span>
          <span style={styles.attachmentHint}>提交后自动标准化并冻结 SHA256</span>
          <button
            type="button"
            aria-label="移除附件"
            style={styles.removeBtn}
            onClick={() => {
              setFile(undefined);
              if (fileInputRef.current) fileInputRef.current.value = "";
            }}
          >
            <X size={13} />
          </button>
        </div>
      )}
      <div style={styles.inputRow}>
        <label style={attachmentDisabled ? styles.attachBtnDisabled : styles.attachBtn}>
          <Paperclip size={16} />
          <input
            ref={fileInputRef}
            type="file"
            accept=".csv,.txt,.xlsx,.at1,.at2,.dat"
            style={{ display: "none" }}
            disabled={busy || attachmentDisabled}
            onChange={event => setFile(event.target.files?.[0])}
          />
        </label>
        <textarea
          value={value}
          onChange={event => setValue(event.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="描述工程目标，例如：使用 OpenSeesPy 对已登记地震工况执行黏滞阻尼器真实优化…"
          rows={1}
          style={styles.textarea}
          aria-label="向智能体下达任务"
        />
        <button
          type="button"
          onClick={submit}
          disabled={!canSend}
          style={canSend ? styles.sendBtn : styles.sendBtnDisabled}
          aria-label="发送"
        >
          <Send size={15} />
        </button>
      </div>
      <p style={styles.hint}>Enter 发送 · Shift+Enter 换行 · 支持 CSV / TXT / XLSX 荷载附件</p>
    </div>
  );
};

const styles: Record<string, React.CSSProperties> = {
  wrapper: {
    borderTop: "1px solid var(--border-color)",
    background: "var(--bg-primary)",
    padding: "12px 20px 14px",
    flexShrink: 0
  },
  inputRow: {
    display: "flex",
    alignItems: "flex-end",
    gap: 8,
    background: "var(--bg-secondary)",
    border: "1px solid var(--border-color)",
    borderRadius: 10,
    padding: 8,
    maxWidth: 860,
    margin: "0 auto"
  },
  textarea: {
    flex: 1,
    minHeight: 34,
    maxHeight: 180,
    padding: "7px 4px",
    background: "transparent",
    border: "none",
    outline: "none",
    resize: "none",
    lineHeight: 1.6,
    fontFamily: "inherit"
  },
  attachBtn: {
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    width: 32,
    height: 32,
    borderRadius: 6,
    color: "var(--text-secondary)",
    cursor: "pointer",
    flexShrink: 0
  },
  attachBtnDisabled: {
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    width: 32,
    height: 32,
    borderRadius: 6,
    color: "var(--text-muted)",
    opacity: 0.4,
    cursor: "not-allowed",
    flexShrink: 0
  },
  sendBtn: {
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    width: 32,
    height: 32,
    borderRadius: 6,
    border: "none",
    background: "var(--primary-color)",
    color: "var(--btn-primary-ink)",
    cursor: "pointer",
    flexShrink: 0
  },
  sendBtnDisabled: {
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    width: 32,
    height: 32,
    borderRadius: 6,
    border: "none",
    background: "var(--bg-tertiary)",
    color: "var(--text-muted)",
    cursor: "not-allowed",
    flexShrink: 0
  },
  attachment: {
    display: "flex",
    alignItems: "center",
    gap: 8,
    maxWidth: 860,
    margin: "0 auto 8px",
    padding: "6px 10px",
    background: "var(--bg-tertiary)",
    border: "1px solid var(--border-color)",
    borderRadius: 6,
    fontSize: 12
  },
  attachmentName: { fontFamily: "var(--font-mono)", fontSize: 11 },
  attachmentHint: { color: "var(--text-secondary)", fontSize: 11, marginLeft: "auto" },
  removeBtn: {
    display: "flex",
    alignItems: "center",
    border: "none",
    background: "transparent",
    color: "var(--text-secondary)",
    cursor: "pointer",
    padding: 2
  },
  hint: {
    maxWidth: 860,
    margin: "8px auto 0",
    color: "var(--text-muted)",
    fontSize: 11,
    textAlign: "center"
  }
};
