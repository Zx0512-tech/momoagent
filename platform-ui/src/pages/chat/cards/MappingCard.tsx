import { UNIT_SOURCE_TEXT, type LoadImport, type LoadMappingV2Payload } from "../../../api/agentApi";
import { cardStyles } from "./cardStyles";

type MappingCardProps = {
  loadImport: LoadImport;
  mapping: LoadMappingV2Payload;
  busy: boolean;
  onChange: (mapping: LoadMappingV2Payload) => void;
  onSubmit: () => void;
};

/**
 * 荷载字段映射卡。逻辑迁移自原 AgentWorkbenchPage 的映射表单，
 * 呈现方式改为对话内嵌卡片。字段语义与后端 LoadMappingRequest 保持一致。
 */
const MappingCard = ({ loadImport, mapping, busy, onChange, onSubmit }: MappingCardProps) => {
  const channel = mapping.channels[0];
  if (!channel) return null;

  const columns = loadImport.inspection.columns;
  const suggestion = loadImport.inspection.suggestedMapping;
  const suggestedUnit = suggestion?.mapping?.channels?.[0]?.sourceUnit ?? null;
  // 界面主动提供的只有 g 与 m/s²；文件自己声明了 gal 或 mm/s² 时把当前值一并列出，
  // 否则下拉框会渲染成空选项，看起来像没识别出单位。
  const unitOptions =
    channel.quantity === "ACCELERATION"
      ? Array.from(new Set(["g", "m/s2", ...(channel.sourceUnit ? [channel.sourceUnit] : [])]))
      : ["kN", "N"];
  const setChannel = (patch: Partial<typeof channel>) =>
    onChange({ ...mapping, channels: [{ ...channel, ...patch }] });

  return (
    <div style={cardStyles.card}>
      <h3 style={cardStyles.title}>确认荷载字段映射</h3>
      <p style={cardStyles.meta}>
        {loadImport.fileName} · {loadImport.inspection.rowCount} 行 · SHA256 {loadImport.sourceSha256.slice(0, 12)}…
      </p>

      {suggestion && (
        <div style={{ marginTop: 8, fontSize: 12, lineHeight: 1.6 }}>
          <div style={cardStyles.meta}>
            单位来源：{UNIT_SOURCE_TEXT[suggestion.unitSource] ?? suggestion.unitSource}
            {" · "}
            整体置信度 {suggestion.confidence}
          </div>
          {suggestion.reasons.length > 0 && (
            <ul style={{ margin: "4px 0 0", paddingLeft: 18, color: "#5b6b7c" }}>
              {suggestion.reasons.map(reason => (
                <li key={reason}>{reason}</li>
              ))}
            </ul>
          )}
          {suggestion.warnings.length > 0 && (
            <ul style={{ margin: "4px 0 0", paddingLeft: 18, color: "#b26a00" }}>
              {suggestion.warnings.map(warning => (
                <li key={warning}>{warning}</li>
              ))}
            </ul>
          )}
        </div>
      )}

      <div style={{ ...cardStyles.grid, marginTop: 10 }}>
        <label>
          <span style={cardStyles.label}>荷载类型</span>
          <select
            className="form-control"
            value={mapping.loadKind}
            onChange={event => onChange({ ...mapping, loadKind: event.target.value as LoadMappingV2Payload["loadKind"] })}
          >
            <option value="EARTHQUAKE">地震</option>
            <option value="WIND">风荷载</option>
            <option value="TRAFFIC">交通荷载</option>
            <option value="GENERIC_NODAL">通用节点荷载</option>
          </select>
        </label>

        <label>
          <span style={cardStyles.label}>时间列</span>
          <select
            className="form-control"
            value={mapping.time.column ?? ""}
            onChange={event =>
              onChange({ ...mapping, time: { ...mapping.time, column: event.target.value || null } })
            }
          >
            <option value="">无时间列</option>
            {columns.map(column => (
              <option key={column.name}>{column.name}</option>
            ))}
          </select>
        </label>

        {!mapping.time.column && (
          <label>
            <span style={cardStyles.label}>时间步（秒）</span>
            <input
              className="form-control"
              type="number"
              min="0.000001"
              value={mapping.time.stepS ?? 0.02}
              onChange={event =>
                onChange({ ...mapping, time: { ...mapping.time, stepS: Number(event.target.value) } })
              }
            />
          </label>
        )}

        {mapping.time.column && (
          <label>
            <span style={cardStyles.label}>时间单位</span>
            <select
              className="form-control"
              value={mapping.time.unit}
              onChange={event =>
                onChange({
                  ...mapping,
                  time: { ...mapping.time, unit: event.target.value as LoadMappingV2Payload["time"]["unit"] }
                })
              }
            >
              <option value="s">秒（s）</option>
              <option value="ms">毫秒（ms）</option>
            </select>
          </label>
        )}

        <label>
          <span style={cardStyles.label}>数值列</span>
          <select className="form-control" value={channel.valueColumn} onChange={event => setChannel({ valueColumn: event.target.value })}>
            {columns.map(column => (
              <option key={column.name}>{column.name}</option>
            ))}
          </select>
        </label>

        <label>
          <span style={cardStyles.label}>物理量</span>
          <select
            className="form-control"
            value={channel.quantity}
            onChange={event => {
              const quantity = event.target.value as "FORCE" | "ACCELERATION";
              setChannel({
                quantity,
                applicationType: quantity === "ACCELERATION" ? "UNIFORM_EXCITATION" : "NODAL_FORCE",
                // 切成加速度时用后端读出的单位；读不出来就留空强制用户选，
                // 不默认 g——默认值会被当成"系统识别结果"直接确认掉。
                sourceUnit: quantity === "ACCELERATION" ? suggestedUnit ?? "" : "kN"
              });
            }}
          >
            <option value="FORCE">节点力</option>
            <option value="ACCELERATION">加速度</option>
          </select>
        </label>

        <label>
          <span style={cardStyles.label}>输入单位</span>
          <select
            className="form-control"
            value={channel.sourceUnit}
            onChange={event => setChannel({ sourceUnit: event.target.value as typeof channel.sourceUnit })}
          >
            {/* 单位判不出来时留一个必须主动选的空项，而不是默认一个看起来已确认的值。 */}
            {!channel.sourceUnit && <option value="">请选择输入单位</option>}
            {unitOptions.map(unit => (
              <option key={unit}>{unit}</option>
            ))}
          </select>
        </label>

        <label>
          <span style={cardStyles.label}>方向</span>
          <select
            className="form-control"
            value={channel.component}
            onChange={event => setChannel({ component: event.target.value as typeof channel.component })}
          >
            <option>UX</option>
            <option>UY</option>
            <option>UZ</option>
          </select>
        </label>

        {channel.applicationType === "NODAL_FORCE" && (
          <label>
            <span style={cardStyles.label}>目标节点</span>
            <input
              className="form-control"
              value={channel.targetId ?? ""}
              onChange={event => setChannel({ targetType: "NODE", targetId: event.target.value })}
            />
          </label>
        )}

        <label>
          <span style={cardStyles.label}>求解器</span>
          <select
            className="form-control"
            value={mapping.solver}
            onChange={event => onChange({ ...mapping, solver: event.target.value as LoadMappingV2Payload["solver"] })}
          >
            <option value="OPENSEESPY_INPROC">OpenSeesPy</option>
            <option value="ANSYS">ANSYS</option>
          </select>
        </label>
      </div>

      <div style={cardStyles.actions}>
        <button className="btn btn-primary" disabled={busy || !channel.sourceUnit} onClick={onSubmit}>
          校验并生成审批
        </button>
        {!channel.sourceUnit && <span style={cardStyles.meta}>请先选择输入单位</span>}
      </div>
    </div>
  );
};

export default MappingCard;
