import { create } from "zustand";
import type { Job, JobStatus, JobType } from "../api/types";
import { api, ApiClientError } from "../api/client";

interface JobState {
  jobs: Job[];
  activeJobs: Record<string, Job>; // jobs currently polling
  totalItems: number;
  loading: boolean;
  error: string | null;
  
  // Actions
  fetchJobs: (params?: { status?: JobStatus; type?: JobType; page?: number; pageSize?: number }) => Promise<void>;
  startJobPolling: (jobId: string, intervalMs?: number) => void;
  stopJobPolling: (jobId: string) => void;
  submitJob: (type: JobType, params: Record<string, any>) => Promise<Job>;
  cancelJob: (jobId: string) => Promise<void>;
  reportError: (message: string) => void;
  clearError: () => void;
}

const activePollers: Record<string, any> = {};

export const useJobStore = create<JobState>((set, get) => ({
  jobs: [],
  activeJobs: {},
  totalItems: 0,
  loading: false,
  error: null,

  fetchJobs: async (params) => {
    set({ loading: true, error: null });
    try {
      const res = await api.getJobs(params);
      set({ jobs: res.data, totalItems: res.pagination.totalItems, loading: false });
      
      // Auto-start polling for any RUNNING or QUEUED jobs in the fetched list
      res.data.forEach(job => {
        if ((job.status === "RUNNING" || job.status === "QUEUED") && !activePollers[job.jobId]) {
          get().startJobPolling(job.jobId);
        }
      });
    } catch (err: any) {
      set({ error: err.message || "获取任务列表失败", loading: false });
    }
  },

  startJobPolling: (jobId, intervalMs = 2000) => {
    // If already polling, don't start another interval
    if (activePollers[jobId]) return;

    console.log(`[JobStore] Start polling for Job ${jobId}`);
    
    const poll = async () => {
      try {
        const job = await api.getJob(jobId);
        
        // Update active jobs state
        set(state => ({
          activeJobs: { ...state.activeJobs, [jobId]: job },
          // Also update in the main list if present
          jobs: state.jobs.map(j => j.jobId === jobId ? job : j)
        }));

        // Stop polling if in terminal state
        if (job.status === "SUCCEEDED" || job.status === "FAILED" || job.status === "CANCELLED") {
          console.log(`[JobStore] Job ${jobId} reached final state: ${job.status}. Stop polling.`);
          get().stopJobPolling(jobId);
          
          // Re-fetch list to sync full changes
          get().fetchJobs();
        }
      } catch (err: any) {
        console.error(`[JobStore] Error polling job ${jobId}`, err);
        set({ error: err instanceof Error ? err.message : "读取任务状态失败" });
        // If 404, stop polling
        if (err instanceof ApiClientError && err.status === 404) {
          get().stopJobPolling(jobId);
        }
      }
    };

    // Run once immediately, then start interval
    poll();
    activePollers[jobId] = setInterval(poll, intervalMs);
  },

  stopJobPolling: (jobId) => {
    if (activePollers[jobId]) {
      clearInterval(activePollers[jobId]);
      delete activePollers[jobId];
      console.log(`[JobStore] Stopped polling for Job ${jobId}`);
    }
    
    set(state => {
      const nextActive = { ...state.activeJobs };
      delete nextActive[jobId];
      return { activeJobs: nextActive };
    });
  },

  submitJob: async (type, params) => {
    set({ error: null });
    try {
      await api.assertLiveCapability(type, params);
      const job = await api.createJob(type, params);
      set(state => ({
        jobs: [job, ...state.jobs]
      }));
      // Start polling for this new job
      get().startJobPolling(job.jobId);
      return job;
    } catch (err: any) {
      set({ error: err.message || "启动任务失败" });
      throw err;
    }
  },

  cancelJob: async (jobId) => {
    set({ error: null });
    try {
      await api.cancelJob(jobId);
      // Immediately stop interval and fetch latest state
      get().stopJobPolling(jobId);
      const updatedJob = await api.getJob(jobId);
      set(state => ({
        jobs: state.jobs.map(j => j.jobId === jobId ? updatedJob : j)
      }));
    } catch (err: any) {
      set({ error: err.message || "取消任务失败" });
      throw err;
    }
  },

  reportError: (message) => set({ error: message }),

  clearError: () => set({ error: null })
}));
