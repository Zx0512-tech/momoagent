import { useEffect, useState } from "react";
import { agentApi, type AgentRun } from "../../api/agentApi";
import RunComparisonCard from "./RunComparisonCard";

const DEFAULT_OPTIMIZATION_GOAL = "执行完整阻尼优化";

type FullOptimizationPanelProps = {
  loadToolActive: boolean;
  onLoadTool: (file: File, goal: string) => Promise<void>;
};

const FullOptimizationPanel = ({ loadToolActive, onLoadTool }: FullOptimizationPanelProps) => {
  const [run, setRun] = useState<AgentRun>();
  const [goal, setGoal] = useState(DEFAULT_OPTIMIZATION_GOAL);
  const [attachment, setAttachment] = useState<File>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  useEffect(() => {
    if (!run?.runId || !["WAITING_JOB", "REVIEWING"].includes(run.status)) return;
    const timer = window.setInterval(async () => {
      try {
        setRun(await agentApi.getRun(run.runId));
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : "读取优化状态失败");
      }
    }, 2000);
    return () => window.clearInterval(timer);
  }, [run?.runId, run?.status]);

  const execute = async (operation: () => Promise<void>) => {
    setBusy(true);
    setError(undefined);
    try {
      await operation();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "操作失败");
    } finally {
      setBusy(false);
    }
  };

  const createRun = () => execute(async () => {
    if (attachment) {
      await onLoadTool(attachment, goal);
      return;
    }
    const session = await agentApi.createSession("MOMO 工程智能体");
    setRun(await agentApi.sendMessage(session.sessionId, goal, undefined, "AUTO"));
  });

  const selectAttachment = (file?: File) => {
    setAttachment(file);
  };

  const decide = (approved: boolean) => execute(async () => {
    if (!run?.pendingApproval) return;
    setRun((await agentApi.decideApproval(run.pendingApproval.approvalId, approved)).run);
  });

  const readinessComponents = Object.entries(run?.preflight?.readiness.components ?? {});
  const checks = Object.entries(run?.resultSummary?.checks ?? {});

  return (
    <div style={styles.stack}>
      {error && <div role="alert" style={styles.error}>{error}</div>}

      <section style={styles.panel}>
        <h2 style={styles.title}>向智能体下达任务</h2>
        <p style={styles.help}>描述工程目标；如附带荷载文件，智能体会先自动调用内部标准化工具。</p>
        <label>
          <span style={styles.label}>自然语言目标</span>
          <textarea className="form-control" style={styles.textarea} value={goal} onChange={event => setGoal(event.target.value)} />
        </label>
        <label>
          <span style={styles.label}>附加荷载文件（可选）</span>
          <input
            type="file"
            accept=".csv,.txt,.xlsx,.at1,.at2,.dat"
            disabled={busy || Boolean(run) || loadToolActive}
            onChange={event => selectAttachment(event.target.files?.[0])}
          />
        </label>
        {attachment && <p role="status" style={styles.help}>{attachment.name} · 提交后自动标准化</p>}
        <button className="btn btn-primary" disabled={busy || Boolean(run) || loadToolActive || !goal.trim()} onClick={createRun}>
          发送给智能体
        </button>
      </section>

      {!attachment && !loadToolActive && <section style={styles.panel}>
        <h2 style={styles.title}>可用的受控工程能力</h2>
        <div style={styles.grid}>
          <Info label="求解器" value="ANSYS / OpenSeesPy" />
          <Info label="阻尼器" value="USER300 黏滞 / 电涡流 / 摩擦" />
          <Info label="双工况对比" value="同荷载、同布置、等最大出力" />
          <Info label="优化 Profile" value="STANDARD / FULL / CUSTOM" />
          <Info label="DOE / CV" value="15 个设计 / 稳定 10 折" />
          <Info label="候选网格" value="728 个离散候选" />
          <Info label="复核预算" value="主动学习≤2轮，review修正≤1轮，误差≤5%" />
        </div>
        <p style={styles.warning}>未附文件时使用已登记模板荷载；如附带荷载文件，智能体会在同一任务内标准化、冻结 SHA256，并将其作为真实 workflow 输入。</p>
      </section>}

      {run && (
        <section style={styles.panel} aria-live="polite">
          <div style={styles.headingRow}>
            <h2 style={styles.title}>计划与预检</h2>
            <span style={styles.status}>{run.status}</span>
          </div>
          <p style={styles.help}>规划模式：{run.plannerMode === "LLM" ? "LLM 结构化意图" : "未提供规划模式"}</p>
          {run.plan && <ol style={styles.plan}>{run.plan.map(item => <li key={item}>{item}</li>)}</ol>}
          {run.taskType === "DAMPER_COMPARISON" && Array.isArray(run.workflowContract?.cases) && (
            <div style={styles.grid}>
              {(run.workflowContract.cases as Array<Record<string, unknown>>).map(item => (
                <Info
                  key={String(item.caseId)}
                  label={String(item.damperType)}
                  value={JSON.stringify(item.parameters)}
                />
              ))}
            </div>
          )}
          {run.preflight && (
            <>
              <p><strong>Preflight：</strong>{run.preflight.passed ? "通过" : "未通过"}</p>
              <div style={styles.grid}>{readinessComponents.map(([name, item]) => <Info key={name} label={name} value={`${item.status} · ${item.detail}`} />)}</div>
            </>
          )}
          {run.resultSummary?.message && <p>{run.resultSummary.message}</p>}
          {run.resultSummary?.inquiryRunComparison && <RunComparisonCard comparison={run.resultSummary.inquiryRunComparison} />}
        </section>
      )}

      {run?.pendingApproval && ["RUN_ENGINEERING_WORKFLOW", "RUN_DAMPER_COMPARISON"].includes(run.pendingApproval.action) && (
        <section style={styles.approval}>
          <h2 style={styles.title}>{run.pendingApproval.action === "RUN_DAMPER_COMPARISON" ? "双工况整单审批" : "整单计划审批"}</h2>
          <p>{run.pendingApproval.summary}</p>
          <details>
            <summary>查看冻结 Job 参数与 SHA256</summary>
            <pre style={styles.pre}>{JSON.stringify(run.pendingApproval.frozenAction, null, 2)}</pre>
            <code>{run.pendingApproval.frozenActionSha256}</code>
          </details>
          <div style={styles.actions}>
            <button className="btn btn-primary" disabled={busy} onClick={() => decide(true)}>批准并创建 Job</button>
            <button className="btn btn-secondary" disabled={busy} onClick={() => decide(false)}>拒绝</button>
          </div>
        </section>
      )}

      {run?.jobId && (
        <section style={styles.panel} aria-live="polite">
          <h2 style={styles.title}>Job、验收与证据</h2>
          <p style={styles.help}>Run：{run.runId}　Job：{run.jobId}　状态：{run.status}</p>
          {checks.length > 0 && <ul>{checks.map(([name, passed]) => <li key={name}>{passed ? "通过" : "未通过"} · {name}</li>)}</ul>}
          {run.resultSummary?.evidenceMode && <p><strong>证据模式：</strong>{run.resultSummary.evidenceMode}</p>}
          <div style={styles.artifacts}>{run.artifactIds.map(id => <a key={id} href={agentApi.artifactDownloadUrl(id)}>{id}</a>)}</div>
          {["WAITING_JOB", "REVIEWING"].includes(run.status) && (
            <button className="btn btn-secondary" disabled={busy} onClick={() => execute(async () => setRun(await agentApi.cancelRun(run.runId)))}>
              取消运行
            </button>
          )}
        </section>
      )}
    </div>
  );
};

const Info = ({ label, value }: { label: string; value: string }) => <div><span style={styles.label}>{label}</span><div>{value}</div></div>;

const styles: Record<string, React.CSSProperties> = {
  stack: { display: "flex", flexDirection: "column", gap: 14 },
  panel: { background: "var(--bg-secondary)", border: "1px solid var(--border-color)", borderRadius: 8, padding: 16 },
  approval: { background: "var(--bg-secondary)", border: "1px solid var(--primary-color)", borderRadius: 8, padding: 16 },
  title: { margin: "0 0 12px", fontSize: 16 },
  help: { color: "var(--text-secondary)" },
  warning: { color: "var(--warning-color)", marginBottom: 0 },
  error: { padding: 10, borderRadius: 6, color: "var(--error-color)", background: "var(--error-soft)" },
  textarea: { minHeight: 72, margin: "6px 0 10px" },
  label: { display: "block", marginBottom: 4, color: "var(--text-secondary)", fontSize: 12 },
  grid: { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(210px, 1fr))", gap: 12 },
  headingRow: { display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12 },
  status: { padding: "5px 9px", borderRadius: 999, color: "var(--primary-color)", background: "var(--bg-tertiary)", fontSize: 12 },
  plan: { margin: "10px 0", paddingLeft: 24, lineHeight: 1.7 },
  pre: { overflowX: "auto", padding: 10, background: "var(--bg-tertiary)", borderRadius: 6 },
  actions: { display: "flex", gap: 10, flexWrap: "wrap", marginTop: 12 },
  artifacts: { display: "flex", flexDirection: "column", gap: 5, marginBottom: 10 }
};

export default FullOptimizationPanel;
