import { agentApi, type AgentInputProvenanceItem, type AgentRun } from "../../../api/agentApi";
import { cardStyles } from "./cardStyles";

const sourceLabels: Record<AgentInputProvenanceItem["source"], string> = {
  USER_DECISION: "用户要求",
  VERIFIED_TEMPLATE: "已验证模板",
  FILE_DERIVED: "附件标准化产物",
  OPERATIONAL_DEFAULT: "运行默认值"
};

/**
 * 工程证据卡。迁移自原 AgentWorkbenchPage 的 AgentEvidencePanel，
 * 保留版本画像、输入来源与输出清单——这些是评审可核验性的关键。
 */
export const EvidenceCard = ({ run }: { run: AgentRun }) => {
  const profile = run.solverVersionProfile ?? run.preflight?.solverVersionProfile;
  const provenance = run.inputProvenance ?? [];
  if (!profile && provenance.length === 0 && !run.outputManifestArtifactId) return null;

  return (
    <div style={cardStyles.card} aria-label="工程证据合同">
      <h3 style={cardStyles.title}>工程证据</h3>

      {profile && (
        <div style={{ marginBottom: 12 }}>
          <span style={cardStyles.label}>版本画像</span>
          <p style={cardStyles.meta}>
            {profile.solver.name} {profile.solver.version} · {profile.sdk.package} {profile.sdk.version}
            <br />
            响应合同：{profile.responseContract.id}
            {profile.userElement
              ? ` · ${profile.userElement.name} 校准哈希${profile.userElement.calibrationHashVerified ? "已验证" : "未验证"}`
              : ""}
          </p>
        </div>
      )}

      {provenance.length > 0 && (
        <div style={{ marginBottom: 12 }}>
          <span style={cardStyles.label}>输入来源</span>
          <ul style={cardStyles.list}>
            {provenance.map(item => (
              <li key={item.field} style={{ fontSize: 12 }}>
                <code style={cardStyles.code}>{item.field}</code>：{sourceLabels[item.source]}
              </li>
            ))}
          </ul>
        </div>
      )}

      {run.outputManifestArtifactId && (
        <div>
          <span style={cardStyles.label}>输出清单</span>
          <a href={agentApi.artifactDownloadUrl(run.outputManifestArtifactId)}>
            下载本次 Job 的文件路径、大小与 SHA256 清单
          </a>
        </div>
      )}
    </div>
  );
};

export default EvidenceCard;
