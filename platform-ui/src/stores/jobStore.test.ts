import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Job } from "../api/types";

const apiMock = vi.hoisted(() => ({
  getJobs: vi.fn(),
  getJob: vi.fn(),
  createJob: vi.fn(),
  cancelJob: vi.fn(),
  assertLiveCapability: vi.fn().mockResolvedValue(undefined)
}));

vi.mock("../api/client", () => ({
  api: apiMock,
  ApiClientError: class ApiClientError extends Error {
    status: number;

    constructor(status: number, error: { code: string; message: string }) {
      super(error.message);
      this.status = status;
    }
  }
}));

import { useJobStore } from "./jobStore";
import { ApiClientError } from "../api/client";

const makeJob = (jobId: string, status: Job["status"]): Job => ({
  jobId,
  type: "SOLVER_BATCH",
  status,
  title: `Job ${jobId}`,
  createdAt: "2026-07-20T00:00:00Z",
  request: {},
  artifacts: []
});

describe("job store", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
    useJobStore.setState({
      jobs: [],
      activeJobs: {},
      totalItems: 0,
      loading: false,
      error: null
    });
  });

  afterEach(() => {
    for (const jobId of Object.keys(useJobStore.getState().activeJobs)) {
      useJobStore.getState().stopJobPolling(jobId);
    }
    vi.clearAllTimers();
    vi.useRealTimers();
  });

  it("获取任务后自动轮询排队中的任务", async () => {
    const queuedJob = makeJob("queued-1", "QUEUED");
    apiMock.getJobs.mockResolvedValue({
      data: [queuedJob],
      pagination: { page: 1, pageSize: 20, totalItems: 1, totalPages: 1 }
    });
    apiMock.getJob.mockResolvedValue(queuedJob);

    await useJobStore.getState().fetchJobs();
    await vi.waitFor(() => expect(apiMock.getJob).toHaveBeenCalledWith("queued-1"));

    expect(useJobStore.getState().jobs).toEqual([queuedJob]);
    expect(useJobStore.getState().activeJobs["queued-1"]).toEqual(queuedJob);
  });

  it("任务进入终态后停止轮询并刷新列表", async () => {
    const runningJob = makeJob("running-1", "RUNNING");
    const finishedJob = makeJob("running-1", "SUCCEEDED");
    apiMock.getJob.mockResolvedValue(finishedJob);
    apiMock.getJobs.mockResolvedValue({
      data: [finishedJob],
      pagination: { page: 1, pageSize: 20, totalItems: 1, totalPages: 1 }
    });
    useJobStore.setState({ jobs: [runningJob] });

    useJobStore.getState().startJobPolling("running-1", 1000);
    await vi.waitFor(() => expect(apiMock.getJobs).toHaveBeenCalled());

    expect(useJobStore.getState().jobs).toEqual([finishedJob]);
    expect(useJobStore.getState().activeJobs).toEqual({});
  });

  it("轮询任务返回 404 时停止轮询并显示错误", async () => {
    const missingJob = makeJob("missing-1", "RUNNING");
    apiMock.getJob.mockRejectedValue(
      new ApiClientError(404, { code: "NOT_FOUND", message: "任务不存在" })
    );
    useJobStore.setState({ jobs: [missingJob] });

    useJobStore.getState().startJobPolling("missing-1", 1000);
    await vi.waitFor(() => expect(useJobStore.getState().error).toBe("任务不存在"));

    expect(useJobStore.getState().activeJobs).toEqual({});
  });

  it("取消任务后以服务端最终状态更新任务列表", async () => {
    const runningJob = makeJob("cancel-1", "RUNNING");
    const cancelledJob = makeJob("cancel-1", "CANCELLED");
    apiMock.cancelJob.mockResolvedValue({ success: true });
    apiMock.getJob.mockResolvedValue(cancelledJob);
    useJobStore.setState({
      jobs: [runningJob],
      activeJobs: { "cancel-1": runningJob }
    });

    await useJobStore.getState().cancelJob("cancel-1");

    expect(apiMock.cancelJob).toHaveBeenCalledWith("cancel-1");
    expect(useJobStore.getState().jobs).toEqual([cancelledJob]);
    expect(useJobStore.getState().activeJobs).toEqual({});
  });

  it("创建任务前先通过能力门禁", async () => {
    apiMock.assertLiveCapability.mockRejectedValueOnce(new Error("能力未实现"));

    await expect(useJobStore.getState().submitJob("SOLVER_BATCH", {})).rejects.toThrow("能力未实现");
    expect(apiMock.createJob).not.toHaveBeenCalled();
  });
});
