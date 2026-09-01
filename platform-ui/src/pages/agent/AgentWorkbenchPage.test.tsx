import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import AgentWorkbenchPage, { AgentEvidencePanel } from "./AgentWorkbenchPage";

describe("AgentWorkbenchPage", () => {
  it("keeps load standardization hidden behind the Agent attachment input", () => {
    const markup = renderToStaticMarkup(<AgentWorkbenchPage />);

    expect(markup).toContain("向智能体下达任务");
    expect(markup).toContain("附加荷载文件（可选）");
    expect(markup).toContain("发送给智能体");
    expect(markup).toContain('type="file"');
    expect(markup).not.toContain("荷载导入能力");
  });

  it("shows the frozen version profile, input provenance, and output manifest", () => {
    const markup = renderToStaticMarkup(<AgentEvidencePanel run={{
      runId: "agr_1",
      sessionId: "ags_1",
      goal: "优化附件地震荷载",
      status: "SUCCEEDED",
      currentStage: "SUCCEEDED",
      artifactIds: ["art_manifest"],
      solverVersionProfile: {
        schemaVersion: "1.0",
        solver: { name: "ANSYS_MAPDL", version: "2024 R2", versionSource: "CONFIGURED_EXECUTABLE_PATH" },
        sdk: { package: "ansys-mapdl-core", version: "0.71.0" },
        responseContract: { id: "ANSYS_BEAM4_SMISC_MMOM_R4", version: "R4", description: "响应合同" },
        userElement: { name: "USER300", calibrationHashVerified: true }
      },
      inputProvenance: [
        { field: "solver", source: "USER_DECISION", value: "ANSYS" },
        { field: "loadDataset", source: "FILE_DERIVED", value: { artifactId: "art_load" } }
      ],
      outputManifestArtifactId: "art_manifest"
    }} />);

    expect(markup).toContain("版本画像");
    expect(markup).toContain("ANSYS_MAPDL 2024 R2");
    expect(markup).toContain("输入来源");
    expect(markup).toContain("附件标准化产物");
    expect(markup).toContain("输出清单");
    expect(markup).toContain("art_manifest/download");
  });
});
