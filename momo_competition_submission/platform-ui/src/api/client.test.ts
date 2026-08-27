import { describe, expect, it } from "vitest";
import { api } from "./client";

describe("api.getCapabilities", () => {
  it("mock 模式明确返回模拟能力而不是生产 Live 能力", async () => {
    const catalog = await api.getCapabilities();

    expect(catalog.version).toBe("1.0.0");
    expect(catalog.data.length).toBeGreaterThan(0);
    expect(catalog.data.every(item => item.status === "MOCK_ONLY")).toBe(true);
    expect(catalog.data.find(item => item.jobType === "SOLVER_BATCH")?.mode).toBe("MOCK");
  });
});
