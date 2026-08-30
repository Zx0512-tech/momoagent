import { afterEach, describe, expect, it, vi } from "vitest";
import { agentApi, suggestLoadMapping, type LoadInspection } from "./agentApi";

afterEach(() => vi.unstubAllGlobals());

describe("suggestLoadMapping", () => {
  it("优先选择时间候选和另一个数值列", () => {
    const inspection: LoadInspection = {
      fileName: "wind.csv",
      format: "CSV",
      rowCount: 2,
      columnCount: 2,
      columns: [
        { name: "time", numericCount: 2, missingCount: 0, min: 0, max: 0.1, timeCandidate: true },
        { name: "load", numericCount: 2, missingCount: 0, min: 1, max: 2, timeCandidate: false }
      ],
      sampleRows: [],
      sheetName: null,
      encoding: "utf-8",
      delimiter: ","
    };

    expect(suggestLoadMapping(inspection)).toEqual({ timeColumn: "time", valueColumn: "load" });
  });
});

describe("agentApi.sendMessage", () => {
  it("无附件自然语言请求使用 AUTO 交由智能体判别", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ runId: "agr_1", artifactIds: [] })
    });
    vi.stubGlobal("fetch", fetchMock);

    await agentApi.sendMessage("ags_1", "执行完整阻尼优化", undefined, "AUTO");

    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({
      content: "执行完整阻尼优化",
      taskType: "AUTO"
    });
  });

  it("双工况对比可显式提交受控任务类型", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ runId: "agr_3", artifactIds: [] })
    });
    vi.stubGlobal("fetch", fetchMock);

    await agentApi.sendMessage("ags_1", "对比黏滞和电涡流阻尼器", undefined, "DAMPER_COMPARISON");

    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({
      content: "对比黏滞和电涡流阻尼器",
      taskType: "DAMPER_COMPARISON"
    });
  });

  it("附件随 AUTO 消息提交并由智能体选择荷载工具", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ runId: "agr_2", artifactIds: [] })
    });
    vi.stubGlobal("fetch", fetchMock);

    await agentApi.sendMessage("ags_1", "标准化附件荷载", "file_1");

    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({
      content: "标准化附件荷载",
      fileId: "file_1",
      taskType: "AUTO"
    });
  });
});

describe("agentApi.streamMessage", () => {
  it("跨分块解析 NDJSON，并按到达顺序推送进度和完成事件", async () => {
    const encoder = new TextEncoder();
    const chunks = [
      encoder.encode('{"type":"run","run":{"runId":"agr_1","status":"PLANNING","artifactIds":[]}}\n{"type":"pro'),
      encoder.encode('gress","run":{"runId":"agr_1","status":"PLANNING","artifactIds":[]}}\n'),
      encoder.encode('{"type":"complete","run":{"runId":"agr_1","status":"SUCCEEDED","artifactIds":[]}}\n')
    ];
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      body: new ReadableStream({
        start(controller) {
          chunks.forEach(chunk => controller.enqueue(chunk));
          controller.close();
        }
      })
    });
    vi.stubGlobal("fetch", fetchMock);
    const events: string[] = [];

    const run = await agentApi.streamMessage(
      "ags_1",
      "给出峰值",
      undefined,
      "AUTO",
      event => events.push(event.type)
    );

    expect(events).toEqual(["run", "progress", "complete"]);
    expect(run.status).toBe("SUCCEEDED");
    expect(fetchMock.mock.calls[0][0]).toContain("/agent/sessions/ags_1/messages/stream");
  });
});
