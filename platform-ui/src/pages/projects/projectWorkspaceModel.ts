import type {
  EngineeringProjectRunSummary,
  EngineeringWorkspace,
  EngineeringWorkspacePatchPayload
} from "../../api/agentApi";

export interface WorkspaceDraft {
  solver: string;
  loadKind: string;
  damperType: string;
  selectedLayoutId: string;
  responseIdsText: string;
  optimizationProfile: string;
}

export function workspaceToDraft(workspace: EngineeringWorkspace): WorkspaceDraft {
  return {
    solver: workspace.solver ?? "",
    loadKind: workspace.loadKind ?? "",
    damperType: workspace.damperType ?? "",
    selectedLayoutId: workspace.selectedLayoutId ?? "",
    responseIdsText: workspace.responseIds.join("\n"),
    optimizationProfile: workspace.optimizationProfile || "STANDARD"
  };
}

function nullable(value: string): string | null {
  const trimmed = value.trim();
  return trimmed ? trimmed : null;
}

export function workspaceDraftToPatch(draft: WorkspaceDraft): EngineeringWorkspacePatchPayload {
  return {
    solver: nullable(draft.solver) as EngineeringWorkspacePatchPayload["solver"],
    loadKind: nullable(draft.loadKind) as EngineeringWorkspacePatchPayload["loadKind"],
    damperType: nullable(draft.damperType) as EngineeringWorkspacePatchPayload["damperType"],
    selectedLayoutId: nullable(draft.selectedLayoutId),
    responseIds: Array.from(new Set(
      draft.responseIdsText
        .split(/[\n,]/)
        .map(item => item.trim())
        .filter(Boolean)
    )),
    optimizationProfile: (draft.optimizationProfile || "STANDARD") as EngineeringWorkspacePatchPayload["optimizationProfile"]
  };
}

export function buildRunInquiryPrompt(runId: string): string {
  return `请查看工程 Run ${runId} 的已登记结果与 Evidence，概括主要工程结果、关键限制和可追溯 Artifact。精确数值只从注册结果读取。`;
}

export function buildComparisonPrompt(runIds: string[]): string {
  const unique = Array.from(new Set(runIds.map(item => item.trim()).filter(Boolean)));
  if (unique.length < 2) throw new Error("至少选择两个 Run 才能比较");
  if (unique.length > 8) throw new Error("一次最多比较 8 个 Run");
  return `比较这些工程 Run：${unique.join("、")}。请以第一个 Run 为基线，使用已登记 Evidence 做确定性比较，并遵守 DIRECT / CROSS_SOLVER / LIMITED / NOT_COMPARABLE 的兼容性限制。`;
}

export function runEvidenceMode(run: EngineeringProjectRunSummary): string {
  return String(run.resultSummary?.evidenceMode || "—");
}
