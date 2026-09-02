import { afterEach, describe, expect, it, vi } from "vitest";

const project = {
  projectId: "agp_1",
  name: "Bridge",
  description: "",
  status: "ACTIVE",
  workspace: {
    schemaVersion: 1,
    modelArtifactId: null,
    modelFileName: null,
    modelSha256: null,
    solver: "OPENSEESPY_INPROC",
    loadKind: "EARTHQUAKE",
    loadArtifactId: null,
    loadSha256: null,
    damperType: "VISCOUS",
    selectedLayoutId: null,
    responseIds: [],
    optimizationProfile: "STANDARD"
  },
  workspaceRevision: 7,
  sessionIds: [],
  sessionCount: 0,
  runCount: 0,
  sessions: [],
  runs: [],
  createdAt: "2026-09-02T00:00:00Z",
  updatedAt: "2026-09-02T00:00:00Z"
};

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe("agentApi workspace optimistic concurrency", () => {
  it("refuses a workspace write before the client has read a revision", async () => {
    const { agentApi } = await import("./agentApi");

    await expect(agentApi.updateProjectWorkspace("agp_unread", { solver: "ANSYS" }))
      .rejects.toThrow("revision 不可用");
  });

  it("sends the revision from getProject as expectedRevision", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(project), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ ...project, workspaceRevision: 8 }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const { agentApi } = await import("./agentApi");

    await agentApi.getProject("agp_1");
    await agentApi.updateProjectWorkspace("agp_1", { solver: "ANSYS" });

    expect(fetchMock).toHaveBeenCalledTimes(2);
    const [, secondInit] = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(JSON.parse(String(secondInit.body))).toEqual({
      expectedRevision: 7,
      patch: { solver: "ANSYS" }
    });
  });
});
