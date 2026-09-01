import { describe, expect, it } from "vitest";
import { isTerminalStatus } from "./chatStatus";

describe("chat run status", () => {
  it("treats unsupported runs as terminal", () => {
    expect(isTerminalStatus("UNSUPPORTED")).toBe(true);
    expect(isTerminalStatus("WAITING_JOB")).toBe(false);
  });
});
