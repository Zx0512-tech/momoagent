import { describe, expect, it } from "vitest";
import {
  buildComparisonPrompt,
  buildRunInquiryPrompt,
  workspaceDraftToPatch,
  workspaceToDraft
} from "./projectWorkspaceModel";

describe("project workspace model", () => {
  it("round-trips editable workspace fields without inventing artifact bindings", () => {
    const draft = workspaceToDraft({
      schemaVersion: 1,
      modelArtifactId: "art_model",
      modelFileName: "bridge.cdb",
      modelSha256: "a".repeat(64),
      solver: "OPENSEESPY_INPROC",
      loadKind: "EARTHQUAKE",
      loadArtifactId: "art_load",
      loadSha256: "b".repeat(64),
      damperType: "VISCOUS",
      selectedLayoutId: "TWO_PER_TOWER",
      responseIds: ["tower_base_shear", "girder_end_ux"],
      optimizationProfile: "FULL"
    });
    const patch = workspaceDraftToPatch(draft);
    expect(patch).toEqual({
      solver: "OPENSEESPY_INPROC",
      loadKind: "EARTHQUAKE",
      damperType: "VISCOUS",
      selectedLayoutId: "TWO_PER_TOWER",
      responseIds: ["tower_base_shear", "girder_end_ux"],
      optimizationProfile: "FULL"
    });
    expect("modelArtifactId" in patch).toBe(false);
    expect("loadArtifactId" in patch).toBe(false);
  });

  it("builds evidence-bound chat prompts", () => {
    expect(buildRunInquiryPrompt("agr_1")).toContain("agr_1");
    const prompt = buildComparisonPrompt(["agr_1", "agr_2", "agr_2"]);
    expect(prompt).toContain("agr_1、agr_2");
    expect(prompt).toContain("Evidence");
  });

  it("requires two distinct runs for comparison", () => {
    expect(() => buildComparisonPrompt(["agr_1", "agr_1"])).toThrow("至少选择两个 Run");
  });
});
