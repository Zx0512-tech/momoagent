import { useEffect, useMemo, useState } from "react";
import {
  agentApi,
  suggestLoadMapping,
  UNIT_SOURCE_TEXT,
  type AgentInputProvenanceItem,
  type AgentRun,
  type LoadChannelMappingPayload,
  type LoadImport,
  type LoadMappingV2Payload,
  type LoadSourceUnit
} from "../../api/agentApi";
import FullOptimizationPanel from "./FullOptimizationPanel";

const stageLabels: Record<string, string> = {
  LOAD_IMPORT: "上传文件",
  LOAD_MAPPING: "确认字段映射",
  LOAD_STANDARDIZATION: "标准化审批",
  SOLVER_APPROVAL: "求解审批",
  SOLVER_EXECUTION: "执行求解任务",
  REPORT_GENERATION: "生成证据报告",
  PLANNING: "生成工程计划",
  PREFLIGHT: "工程预检",
  WAITING_APPROVAL: "等待审批",
  WAITING_JOB: "等待求解",
  REVIEWING: "确定性复核",
  COMPLETED: "已完成"
};

// 风荷载的目标节点集由后端登记（platform_store.AGENT_LOAD_TARGET_SETS），
// 施加方向固定为竖向 UY（pyansys_bridge 把风荷载钉在 +Y，配置里的 direction
// 不会被采纳）。这里的默认值必须与 analysis._load_kind_gate 的放行条件一致：
// 此前风工况沿用通用节点力默认值（NODE/101/UZ），照默认提交必被
// WIND_LOAD_TARGET_UNSUPPORTED 挡掉。
const WIND_LOAD_TARGET_SET_ID = "STBRIDGE_WIND_DECK_NODES";

const defaultChannelTargetFor = (
  loadKind: LoadMappingV2Payload["loadKind"],
  suggestedUnit: LoadSourceUnit | null = null
): Omit<LoadChannelMappingPayload, "valueColumn" | "scale"> => {
  if (loadKind === "EARTHQUAKE") {
    return {
      applicationType: "UNIFORM_EXCITATION",
      component: "UX",
      quantity: "ACCELERATION",
      // 单位用后端从文件头或列名里读出的声明。写死 "g" 会把 m/s² 的记录预填成 g，
      // 用户看到 g 以为系统读出了 g，确认下去就是 9.8 倍荷载误差，还带着合法审批哈希。
      // 读不出来（后端给 null）就留空强制手选，默认值比空值危险。
      sourceUnit: suggestedUnit ?? ""
    };
  }
  if (loadKind === "WIND") {
    return {
      applicationType: "NODAL_FORCE",
      targetType: "NODE_GROUP",
      targetId: WIND_LOAD_TARGET_SET_ID,
      component: "UY",
      quantity: "FORCE",
      sourceUnit: "N"
    };
  }
  return {
    applicationType: "NODAL_FORCE",
    targetType: "NODE",
    targetId: "101",
    component: "UZ",
    quantity: "FORCE",
    sourceUnit: "kN"
  };
};

const AgentWorkbenchPage = () => {
  const [sessionId, setSessionId] = useState<string>();
  const [loadImport, setLoadImport] = useState<LoadImport>();
  const [run, setRun] = useState<AgentRun>();
  const [mapping, setMapping] = useState<LoadMappingV2Payload>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  const columns = loadImport?.inspection.columns ?? [];
  const suggestion = loadImport?.inspection.suggestedMapping;
  const suggestedUnit = suggestion?.mapping?.channels?.[0]?.sourceUnit ?? null;
  const channel = mapping?.channels[0];
  // 界面只主动给 g 与 m/s²；文件自己声明了 gal 或 mm/s² 时把当前值一并列出，
  // 否则下拉框渲染成空选项，看起来像没识别出单位。
  const unitOptions =
    channel?.quantity === "ACCELERATION"
      ? Array.from(new Set(["g", "m/s2", ...(channel.sourceUnit ? [channel.sourceUnit] : [])]))
      : ["kN", "N"];
  const pollingRunId = run?.runId;
  const pollingStatus = run?.status;

  useEffect(() => {
    if (!pollingRunId || pollingStatus !== "WAITING_JOB") return;
    const timer = window.setInterval(async () => {
      try {
        setRun(await agentApi.getRun(pollingRunId));
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : "读取任务状态失败");
      }
    }, 2000);
    return () => window.clearInterval(timer);
  }, [pollingRunId, pollingStatus]);

  const sampleHeaders = useMemo(
    () => Object.keys(loadImport?.inspection.sampleRows[0] ?? {}),
    [loadImport]
  );

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

  const runLoadTool = async (file: File, goal: string) => {
    setError(undefined);
    let activeSessionId = sessionId;
    if (!activeSessionId) {
      activeSessionId = (await agentApi.createSession("MOMO 工程智能体")).sessionId;
      setSessionId(activeSessionId);
    }
    const uploaded = await agentApi.uploadFile(file);
    const suggested = suggestLoadMapping(uploaded.inspection);
    // 单位取自这次上传的返回值，不用组件作用域的 suggestedUnit：setLoadImport 还没生效。
    const uploadedUnit = uploaded.inspection.suggestedMapping?.mapping?.channels?.[0]?.sourceUnit ?? null;
    const created = await agentApi.sendMessage(activeSessionId, goal, uploaded.fileId);
    const intent = created.intent ?? {};
    const loadKind = String(intent.loadKind ?? "GENERIC_NODAL") as LoadMappingV2Payload["loadKind"];
    const solver = String(intent.solver ?? "ANSYS") as LoadMappingV2Payload["solver"];
    setLoadImport(uploaded);
    setRun(created);
    setMapping({
      runId: created.runId,
      loadKind,
      time: { column: suggested.timeColumn, unit: "s", ...(!suggested.timeColumn ? { stepS: 0.02 } : {}) },
      channels: [{
        valueColumn: suggested.valueColumn,
        ...defaultChannelTargetFor(loadKind, uploadedUnit),
        scale: 1
      }],
      solver
    });
  };

  const submitMapping = () => execute(async () => {
    if (!loadImport || !mapping || !run) return;
    const response = await agentApi.setMapping(loadImport.importId, mapping);
    setRun(response.run);
  });

  const decide = (approved: boolean) => execute(async () => {
    if (!run?.pendingApproval) return;
    const response = await agentApi.decideApproval(run.pendingApproval.approvalId, approved);
    setRun(response.run);
  });

  return (
    <div style={styles.page}>
      <div style={styles.header}>
        <div>
          <h1 style={styles.title}>MOMO 工程智能体</h1>
          <p style={styles.subtitle}>描述工程目标，可按需附加荷载文件</p>
        </div>
        {run && <span style={styles.status}>{stageLabels[run.currentStage] ?? run.currentStage}</span>}
      </div>

      <FullOptimizationPanel loadToolActive={Boolean(loadImport || run)} onLoadTool={runLoadTool} />

      {error && <div style={styles.error}>{error}</div>}

      {loadImport && mapping && channel && (
        <section style={styles.panel}>
          <h2 style={styles.panelTitle}>确认荷载字段映射</h2>
          <p style={styles.meta}>{loadImport.fileName} · {loadImport.inspection.rowCount} 行 · SHA256 {loadImport.sourceSha256.slice(0, 12)}…</p>
          {/* 预填的单位可能是读出来的也可能是猜出来的，两者看起来一样，必须写明。 */}
          {suggestion && <p style={styles.meta}>单位来源：{UNIT_SOURCE_TEXT[suggestion.unitSource] ?? suggestion.unitSource}</p>}
          <div style={styles.grid}>
            <Field label="荷载类型"><select className="form-control" value={mapping.loadKind} onChange={event => setMapping({ ...mapping, loadKind: event.target.value as LoadMappingV2Payload["loadKind"] })}><option value="EARTHQUAKE">地震</option><option value="WIND">风荷载</option><option value="TRAFFIC">交通荷载</option><option value="GENERIC_NODAL">通用节点荷载</option></select></Field>
            <Field label="时间列">
              <select className="form-control" value={mapping.time.column ?? ""} onChange={event => setMapping({ ...mapping, time: { ...mapping.time, column: event.target.value || null } })}>
                <option value="">无时间列</option>
                {columns.map(column => <option key={column.name}>{column.name}</option>)}
              </select>
            </Field>
            {!mapping.time.column && <Field label="时间步（秒）"><input className="form-control" type="number" min="0.000001" value={mapping.time.stepS ?? 0.02} onChange={event => setMapping({ ...mapping, time: { ...mapping.time, stepS: Number(event.target.value) } })} /></Field>}
            {mapping.time.column && <Field label="时间单位"><select className="form-control" value={mapping.time.unit} onChange={event => setMapping({ ...mapping, time: { ...mapping.time, unit: event.target.value as LoadMappingV2Payload["time"]["unit"] } })}><option value="s">秒（s）</option><option value="ms">毫秒（ms）</option></select></Field>}
            <Field label="数值列"><select className="form-control" value={channel.valueColumn} onChange={event => setMapping({ ...mapping, channels: [{ ...channel, valueColumn: event.target.value }] })}>{columns.map(column => <option key={column.name}>{column.name}</option>)}</select></Field>
            <Field label="物理量"><select className="form-control" value={channel.quantity} onChange={event => {
              const quantity = event.target.value as "FORCE" | "ACCELERATION";
              setMapping({ ...mapping, channels: [{
                ...channel,
                quantity,
                applicationType: quantity === "ACCELERATION" ? "UNIFORM_EXCITATION" : "NODAL_FORCE",
                // 切成加速度时用后端读出的单位；读不出来就留空强制手选，不默认 g。
                sourceUnit: quantity === "ACCELERATION" ? suggestedUnit ?? "" : "kN"
              }] });
            }}><option value="FORCE">节点力</option><option value="ACCELERATION">加速度</option></select></Field>
            <Field label="输入单位"><select className="form-control" value={channel.sourceUnit} onChange={event => setMapping({ ...mapping, channels: [{ ...channel, sourceUnit: event.target.value as typeof channel.sourceUnit }] })}>{!channel.sourceUnit && <option value="">请选择输入单位</option>}{unitOptions.map(unit => <option key={unit}>{unit}</option>)}</select></Field>
            <Field label="方向"><select className="form-control" value={channel.component} onChange={event => setMapping({ ...mapping, channels: [{ ...channel, component: event.target.value as typeof channel.component }] })}><option>UX</option><option>UY</option><option>UZ</option></select></Field>
            {channel.applicationType === "NODAL_FORCE" && <Field label="目标节点"><input className="form-control" value={channel.targetId ?? ""} onChange={event => setMapping({ ...mapping, channels: [{ ...channel, targetType: "NODE", targetId: event.target.value }] })} /></Field>}
            <Field label="求解器"><select className="form-control" value={mapping.solver} onChange={event => setMapping({ ...mapping, solver: event.target.value as LoadMappingV2Payload["solver"] })}><option value="OPENSEESPY_INPROC">OpenSeesPy</option><option value="ANSYS">ANSYS</option></select></Field>
          </div>
          <button className="btn btn-primary" style={{ marginTop: 12 }} disabled={!run || busy || !channel.sourceUnit} onClick={submitMapping}>校验并生成审批</button>
          {!channel.sourceUnit && <p style={styles.meta}>请先选择输入单位</p>}
        </section>
      )}

      {loadImport && sampleHeaders.length > 0 && (
        <section style={styles.panel}>
          <h2 style={styles.panelTitle}>文件样本</h2>
          <div style={{ overflowX: "auto" }}><table style={styles.table}><thead><tr>{sampleHeaders.map(header => <th key={header}>{header}</th>)}</tr></thead><tbody>{loadImport.inspection.sampleRows.map((row, index) => <tr key={index}>{sampleHeaders.map(header => <td key={header}>{row[header]}</td>)}</tr>)}</tbody></table></div>
        </section>
      )}

      {run?.workflowContract && loadImport && (
        <section style={styles.panel} aria-live="polite">
          <h2 style={styles.panelTitle}>受控工程计划</h2>
          <p style={styles.meta}>
            规划模式：{run.plannerMode === "LLM" ? "LLM 结构化意图" : "未提供规划模式"} ·
            求解器：{String(run.intent?.solver ?? "ANSYS")} ·
            阻尼器：{String(run.intent?.damperType ?? "未指定")}
          </p>
          {run.plan && <ol style={styles.plan}>{run.plan.map(item => <li key={item}>{item}</li>)}</ol>}
          {run.preflight && <p><strong>Preflight：</strong>{run.preflight.passed ? "通过" : "未通过"}</p>}
          {run.resultSummary?.message && <p>{run.resultSummary.message}</p>}
        </section>
      )}

      {run?.pendingApproval && (
        <section style={{ ...styles.panel, borderColor: "var(--primary-color)" }}>
          <h2 style={styles.panelTitle}>{run.pendingApproval.action === "STANDARDIZE_LOAD" ? "荷载映射审批" : "工程执行审批"}</h2>
          <p>{run.pendingApproval.summary}</p>
          <p style={styles.warning}>批准后只执行审批卡中冻结的参数。普通平台 Job 不会被标记为真实 FEM，除非求解结果携带已验证证据。</p>
          {run.pendingApproval.frozenAction && <details>
            <summary>查看冻结参数与 SHA256</summary>
            <pre style={styles.pre}>{JSON.stringify(run.pendingApproval.frozenAction, null, 2)}</pre>
            <code>{run.pendingApproval.frozenActionSha256}</code>
          </details>}
          <div style={styles.row}>
            <button className="btn btn-primary" disabled={busy} onClick={() => decide(true)}>批准</button>
            <button className="btn btn-secondary" disabled={busy} onClick={() => decide(false)}>拒绝</button>
          </div>
        </section>
      )}

      {run && (
        <section style={styles.panel}>
          <h2 style={styles.panelTitle}>运行与证据</h2>
          <div style={styles.meta}>Run：{run.runId}　状态：{run.status}{run.jobId ? `　Job：${run.jobId}` : ""}</div>
          <AgentEvidencePanel run={run} />
          <div style={styles.artifacts}>{run.artifactIds.map(artifactId => <a key={artifactId} href={agentApi.artifactDownloadUrl(artifactId)}>{artifactId}</a>)}</div>
          {run.status === "WAITING_JOB" && <button className="btn btn-secondary" disabled={busy} onClick={() => execute(async () => setRun(await agentApi.cancelRun(run.runId)))}>取消运行</button>}
        </section>
      )}
    </div>
  );
};

const sourceLabels: Record<AgentInputProvenanceItem["source"], string> = {
  USER_DECISION: "用户要求",
  VERIFIED_TEMPLATE: "已验证模板",
  FILE_DERIVED: "附件标准化产物",
  OPERATIONAL_DEFAULT: "运行默认值"
};

export const AgentEvidencePanel = ({ run }: { run: AgentRun }) => {
  const profile = run.solverVersionProfile ?? run.preflight?.solverVersionProfile;
  const provenance = run.inputProvenance ?? [];
  if (!profile && provenance.length === 0 && !run.outputManifestArtifactId) return null;
  return <div style={styles.evidence} aria-label="工程证据合同">
    {profile && <div>
      <h3 style={styles.evidenceTitle}>版本画像</h3>
      <p style={styles.meta}>
        {profile.solver.name} {profile.solver.version} · {profile.sdk.package} {profile.sdk.version}<br />
        响应合同：{profile.responseContract.id}
        {profile.userElement ? ` · ${profile.userElement.name} 校准哈希${profile.userElement.calibrationHashVerified ? "已验证" : "未验证"}` : ""}
      </p>
    </div>}
    {provenance.length > 0 && <div>
      <h3 style={styles.evidenceTitle}>输入来源</h3>
      <ul style={styles.provenance}>
        {provenance.map(item => <li key={item.field}>
          <code>{item.field}</code>：{sourceLabels[item.source]}
        </li>)}
      </ul>
    </div>}
    {run.outputManifestArtifactId && <div>
      <h3 style={styles.evidenceTitle}>输出清单</h3>
      <a href={agentApi.artifactDownloadUrl(run.outputManifestArtifactId)}>下载本次 Job 的文件路径、大小与 SHA256 清单</a>
    </div>}
  </div>;
};

const Field = ({ label, children }: { label: string; children: React.ReactNode }) => <label><span style={styles.label}>{label}</span>{children}</label>;

const styles: Record<string, React.CSSProperties> = {
  page: { display: "flex", flexDirection: "column", gap: 14, maxWidth: 1180, margin: "0 auto" },
  header: { display: "flex", justifyContent: "space-between", alignItems: "center" },
  title: { margin: 0, fontSize: 24 },
  subtitle: { margin: "6px 0 0", color: "var(--text-secondary)" },
  status: { padding: "6px 10px", borderRadius: 999, color: "var(--primary-color)", background: "var(--bg-tertiary)" },
  panel: { background: "var(--bg-secondary)", border: "1px solid var(--border-color)", borderRadius: 8, padding: 16 },
  panelTitle: { margin: "0 0 12px", fontSize: 16 },
  row: { display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" },
  grid: { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: 12 },
  label: { display: "block", marginBottom: 5, color: "var(--text-secondary)", fontSize: 12 },
  error: { padding: 10, borderRadius: 6, color: "var(--error-color)", background: "var(--error-soft)" },
  warning: { color: "var(--warning-color)", fontSize: 12 },
  meta: { color: "var(--text-secondary)", marginBottom: 10 },
  artifacts: { display: "flex", flexDirection: "column", gap: 5, marginBottom: 10 },
  evidence: { display: "grid", gap: 12, marginBottom: 14 },
  evidenceTitle: { margin: "0 0 6px", fontSize: 14 },
  provenance: { margin: 0, paddingLeft: 20, lineHeight: 1.7 },
  table: { width: "100%", borderCollapse: "collapse", fontSize: 12 },
  plan: { margin: "10px 0", paddingLeft: 24, lineHeight: 1.7 },
  pre: { overflowX: "auto", padding: 10, background: "var(--bg-tertiary)", borderRadius: 6 }
};

export default AgentWorkbenchPage;
