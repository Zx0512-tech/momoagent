import type {
  Job,
  Artifact,
  DashboardSummary,
  Template,
  ParityReport,
  JobStatus,
  PreflightResponse,
  JobType
} from "./types";
import { getTargetLabel } from "./engineeringOptions";

// Helper to generate IDs
const genId = (prefix: string) => `${prefix}_${Math.random().toString(36).substr(2, 9)}`;

// Local Storage helpers to persist mock data across refreshes
const STORAGE_KEY_JOBS = "momo_mock_jobs";
const STORAGE_KEY_ARTIFACTS = "momo_mock_artifacts";

export class MockDatabase {
  private jobs: Job[] = [];
  private artifacts: Artifact[] = [];
  private templates: Template[] = [
    {
      templateId: "ansys_joint_baseline",
      name: "ANSYS Joint Baseline 核心工作流模板",
      solver: "ANSYS",
      workflow: "BASELINE",
      path: "docs/examples/templates/ansys_run_joint_baseline_workflow_template.json",
      isLegacy: false
    },
    {
      templateId: "openseespy_inproc_joint_baseline",
      name: "OpenSeesPy In-Process Joint Baseline 工作流模板",
      solver: "OPENSEESPY_INPROC",
      workflow: "BASELINE",
      path: "docs/examples/templates/openseespy_inproc_run_joint_baseline_workflow_template.json",
      isLegacy: false
    },
    {
      templateId: "solver_parity_baseline",
      name: "双求解器一致性对齐测试 (Solver Parity Baseline)",
      solver: "ANSYS",
      workflow: "SOLVER_PARITY",
      path: "docs/examples/templates/solver_parity_baseline_workflow_template.json",
      isLegacy: false
    }
  ];

  constructor() {
    this.loadFromStorage();
    if (this.jobs.length === 0) {
      this.initDefaultData();
    }
    // Start a periodic checker to simulate job execution
    setInterval(() => this.tickJobs(), 2000);
  }

  private loadFromStorage() {
    try {
      const savedJobs = localStorage.getItem(STORAGE_KEY_JOBS);
      const savedArtifacts = localStorage.getItem(STORAGE_KEY_ARTIFACTS);
      if (savedJobs) this.jobs = JSON.parse(savedJobs);
      if (savedArtifacts) this.artifacts = JSON.parse(savedArtifacts);
    } catch (e) {
      console.error("Failed to load mock DB from localStorage", e);
    }
  }

  private saveToStorage() {
    try {
      localStorage.setItem(STORAGE_KEY_JOBS, JSON.stringify(this.jobs));
      localStorage.setItem(STORAGE_KEY_ARTIFACTS, JSON.stringify(this.artifacts));
    } catch (e) {
      console.error("Failed to save mock DB to localStorage", e);
    }
  }

  private initDefaultData() {
    this.artifacts = [
      {
        artifactId: "art_gate_report",
        kind: "STATUS_REPORT",
        name: "project_optimization_gates_2026-07-01.md",
        path: "docs/status_reports/project_optimization_gates_2026-07-01.md",
        mimeType: "text/markdown",
        sizeBytes: 8192,
        canPreview: true,
        downloadUrl: "/api/v1/artifacts/art_gate_report/download"
      },
      {
        artifactId: "art_parity_report",
        kind: "PARITY_REPORT",
        name: "baseline_parity_report.json",
        path: "output/run_templates/solver_parity/baseline_parity_report.json",
        mimeType: "application/json",
        sizeBytes: 12400,
        sha256: "4a82b9a7c3f8e5d0d71a12a321927c9f80210e7cb2818de7d7281f6c6d0411ad",
        canPreview: true,
        downloadUrl: "/api/v1/artifacts/art_parity_report/download"
      },
      {
        artifactId: "art_traffic_load_demo",
        kind: "LOAD_CASE",
        name: "traffic_24h_seed_42.csv",
        path: "output/load_cases/traffic_24h_seed_42.csv",
        mimeType: "text/csv",
        sizeBytes: 2548900,
        sha256: "b20f922718cb1d5e307c8a2b53b0e1189ac3fbc19c90b0e5124000ee741639d1",
        canPreview: true,
        downloadUrl: "/api/v1/artifacts/art_traffic_load_demo/download"
      },
      {
        artifactId: "art_traffic_profile_weekday",
        kind: "RAW_DATA",
        name: "weekday_vehicle_profile.csv",
        path: "output/load_cases/weekday_vehicle_profile.csv",
        mimeType: "text/csv",
        sizeBytes: 3480000,
        sha256: "0c5bf846cb2c8b2ca2d40c91c2d9bf6a44e26e6d5aa31dd3e37ad3a21fd93e91",
        canPreview: true,
        downloadUrl: "/api/v1/artifacts/art_traffic_profile_weekday/download"
      }
    ];

    this.jobs = [
      {
        jobId: "job_init_001",
        type: "PROJECT_GATE_FAST",
        status: "SUCCEEDED",
        title: "快速回归门槛测试 (Regression Gate)",
        createdAt: "2026-07-01T16:00:00+08:00",
        startedAt: "2026-07-01T16:00:01+08:00",
        finishedAt: "2026-07-01T16:00:15+08:00",
        request: {},
        artifacts: [this.artifacts[0]]
      },
      {
        jobId: "job_init_002",
        type: "SOLVER_PARITY",
        status: "SUCCEEDED",
        title: "求解器一致性对齐测试 (Baseline Solver Parity)",
        createdAt: "2026-07-01T16:10:00+08:00",
        startedAt: "2026-07-01T16:10:02+08:00",
        finishedAt: "2026-07-01T16:12:30+08:00",
        request: { configPath: "docs/examples/templates/solver_parity_baseline_workflow_template.json" },
        artifacts: [this.artifacts[1]]
      }
    ];
    this.saveToStorage();
  }

  // Simulate background job updates
  private tickJobs() {
    let changed = false;
    this.jobs = this.jobs.map(job => {
      if (job.status === "QUEUED") {
        changed = true;
        return {
          ...job,
          status: "RUNNING",
          startedAt: new Date().toISOString(),
          progress: { phase: "初始化计算环境", message: "读取计算网格并配置求解器驱动", percent: 10 }
        };
      }
      if (job.status === "RUNNING") {
        changed = true;
        const currentPercent = job.progress?.percent || 10;
        if (currentPercent >= 90) {
          // Success! Add artifacts and finish
          const completedArtifacts = this.generateArtifactsForJob(job);
          this.artifacts = [...completedArtifacts, ...this.artifacts];
          return {
            ...job,
            status: "SUCCEEDED",
            finishedAt: new Date().toISOString(),
            progress: { phase: "已完成", message: "计算成功结束并保存产物", percent: 100 },
            artifacts: completedArtifacts
          };
        } else {
          // Progress
          const nextPercent = currentPercent + Math.floor(Math.random() * 20) + 10;
          let phase = job.progress?.phase || "计算中";
          let message = job.progress?.message || "";
          
          if (job.type === "SOLVER_BATCH") {
            const currentCase = Math.floor((nextPercent / 100) * 20);
            phase = "求解器运算中";
            message = `正在计算 Case: case_${String(currentCase).padStart(3, "0")}/case_020`;
          } else if (job.type === "LOAD_WIND_VERTICAL") {
            phase = "谐波合成计算";
            message = `频段离散点求和中 (已完成 ${nextPercent}%)`;
          } else if (job.type === "LOAD_CURVE_EXPORT") {
            phase = "荷载曲线图导出";
            message = `按项目时程图风格导出 PNG/TIFF/SVG 图件`;
          } else if (job.type === "SURROGATE_TRAINING") {
            phase = "自适应代理模型拟合";
            message = `GPR/Kriging/SVR 等小样本代理模型交叉验证中`;
          } else if (job.type === "EXPERIMENT_DESIGN") {
            phase = "DOE 样本批量计算";
            message = `正在组合 LHS、角点和中心点样本并写出阻尼器响应数据`;
          } else if (job.type === "MULTI_OBJECTIVE_OPTIMIZATION") {
            phase = "Pareto 前沿搜索";
            message = `多约束非支配排序遗传算法(NSGA-II)代数累加中`;
          } else if (job.type === "OPTIMIZATION_EXPORT") {
            phase = "优化结果导出";
            message = `正在导出 Pareto 前沿、熵权和 TOPSIS 推荐方案制品`;
          }

          return {
            ...job,
            progress: {
              phase,
              message,
              percent: Math.min(nextPercent, 90)
            }
          };
        }
      }
      return job;
    });

    if (changed) {
      this.saveToStorage();
    }
  }

  private generateArtifactsForJob(job: Job): Artifact[] {
    const timeStr = new Date().toISOString().split("T")[0];
    switch (job.type) {
      case "LOAD_TRAFFIC_RANDOM":
        return [
          {
            artifactId: genId("art"),
            kind: "LOAD_CASE",
            name: `traffic_load_${job.request.sourceMode === "LOAD_EXISTING" ? "from_existing" : "generated"}_${timeStr}.csv`,
            path: `output/load_cases/traffic_load_${timeStr}.csv`,
            mimeType: "text/csv",
            sizeBytes: 1548000,
            canPreview: true,
            downloadUrl: `/api/v1/artifacts/${genId("art")}/download`
          },
          {
            artifactId: genId("art"),
            kind: "RAW_DATA",
            name: `traffic_flow_profile_${timeStr}.json`,
            path: `output/load_cases/traffic_flow_profile_${timeStr}.json`,
            mimeType: "application/json",
            sizeBytes: 8200,
            canPreview: true,
            downloadUrl: `/api/v1/artifacts/${genId("art")}/download`
          }
        ];
      case "LOAD_WIND_VERTICAL":
        return [
          {
            artifactId: genId("art"),
            kind: "LOAD_CASE",
            name: `wind_load_vertical_applied_${timeStr}.csv`,
            path: `output/load_cases/wind_load_vertical_applied_${timeStr}.csv`,
            mimeType: "text/csv",
            sizeBytes: 980000,
            canPreview: true,
            downloadUrl: `/api/v1/artifacts/${genId("art")}/download`
          },
          {
            artifactId: genId("art"),
            kind: "PLOT",
            name: `wind_spectrum_comparison_${timeStr}.png`,
            path: `output/plots/wind_spectrum_comparison_${timeStr}.png`,
            mimeType: "image/png",
            canPreview: true,
            downloadUrl: `/api/v1/artifacts/${genId("art")}/download`
          }
        ];
      case "LOAD_EARTHQUAKE":
        return [
          {
            artifactId: genId("art"),
            kind: "LOAD_CASE",
            name: `earthquake_scaled_applied_${timeStr}.csv`,
            path: `output/load_cases/earthquake_scaled_applied_${timeStr}.csv`,
            mimeType: "text/csv",
            sizeBytes: 420000,
            canPreview: true,
            downloadUrl: `/api/v1/artifacts/${genId("art")}/download`
          },
          {
            artifactId: genId("art"),
            kind: "JSON_SUMMARY",
            name: `earthquake_code_spectrum_scaling_${timeStr}.json`,
            path: `output/load_cases/earthquake_code_spectrum_scaling_${timeStr}.json`,
            mimeType: "application/json",
            sizeBytes: 10800,
            canPreview: true,
            downloadUrl: `/api/v1/artifacts/${genId("art")}/download`
          }
        ];
      case "LOAD_CURVE_EXPORT": {
        const formats = Array.isArray(job.request.formats) ? job.request.formats : ["PNG"];
        const customFormat = String(job.request.customFormat || "dat").replace(/^\./, "").toLowerCase();
        return formats.map((format: string) => {
          const normalized = format === "CUSTOM" ? customFormat : format.toLowerCase();
          const mimeType =
            normalized === "svg"
              ? "image/svg+xml"
              : normalized === "tiff" || normalized === "tif"
                ? "image/tiff"
                : normalized === "png"
                  ? "image/png"
                  : "application/octet-stream";
          const artifactId = genId("art");
          return {
            artifactId,
            kind: "PLOT" as const,
            name: `load_curve_${job.request.loadKind || "load"}_${job.request.curveComponent || "component"}_${timeStr}.${normalized}`,
            path: `output/plots/load_curve_${job.request.loadKind || "load"}_${timeStr}.${normalized}`,
            mimeType,
            sizeBytes: normalized === "svg" ? 42000 : 580000,
            canPreview: normalized === "png" || normalized === "svg",
            downloadUrl: `/api/v1/artifacts/${artifactId}/download`
          };
        });
      }
      case "COMMAND_STREAM_ASSEMBLY":
        return [{
          artifactId: genId("art"),
          kind: "COMMAND_STREAM",
          name: `assembled_command_stream_${timeStr}.mac`,
          path: `output/command_streams/assembled_command_stream_${timeStr}.mac`,
          mimeType: "text/plain",
          sizeBytes: 45200,
          sha256: "72df89a9f243de41a87db8c0e29b0a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f",
          canPreview: true,
          downloadUrl: `/api/v1/artifacts/${genId("art")}/download`
        }];
      case "EXPERIMENT_DESIGN": {
        const baseArtifacts: Artifact[] = [
          {
            artifactId: genId("art"),
            kind: "CSV_TABLE",
            name: `doe_design_matrix_${timeStr}.csv`,
            path: `output/doe/${job.request.caseSetPrefix || "user300_doe"}/doe_design_matrix.csv`,
            mimeType: "text/csv",
            sizeBytes: 64000,
            canPreview: true,
            downloadUrl: `/api/v1/artifacts/${genId("art")}/download`
          },
          {
            artifactId: genId("art"),
            kind: "JSON_SUMMARY",
            name: `doe_case_set_summary_${timeStr}.json`,
            path: `output/doe/${job.request.caseSetPrefix || "user300_doe"}/case_set_summary.json`,
            mimeType: "application/json",
            sizeBytes: 9400,
            canPreview: true,
            downloadUrl: `/api/v1/artifacts/${genId("art")}/download`
          },
          {
            artifactId: genId("art"),
            kind: "RAW_DATA",
            name: `damper_response_training_dataset_${timeStr}.csv`,
            path: `output/doe/${job.request.caseSetPrefix || "user300_doe"}/damper_response_training_dataset.csv`,
            mimeType: "text/csv",
            sizeBytes: 248000,
            canPreview: true,
            downloadUrl: `/api/v1/artifacts/${genId("art")}/download`
          }
        ];
        if (job.request.executionGoal === "OPTIMIZATION_RECOMMENDATION") {
          return [
            ...baseArtifacts,
            {
              artifactId: genId("art"),
              kind: "SURROGATE_MODEL",
              name: `doe_trained_surrogate_${timeStr}.pkl`,
              path: `output/doe/${job.request.caseSetPrefix || "user300_doe"}/surrogate_model.pkl`,
              mimeType: "application/octet-stream",
              sizeBytes: 1520000,
              canPreview: false,
              downloadUrl: `/api/v1/artifacts/${genId("art")}/download`
            },
            {
              artifactId: genId("art"),
              kind: "OPTIMIZATION_REPORT",
              name: `doe_pareto_recommendation_${timeStr}.json`,
              path: `output/doe/${job.request.caseSetPrefix || "user300_doe"}/pareto_recommendation.json`,
              mimeType: "application/json",
              sizeBytes: 36000,
              canPreview: true,
              downloadUrl: `/api/v1/artifacts/${genId("art")}/download`
            },
            {
              artifactId: genId("art"),
              kind: "DECISION_REPORT",
              name: `doe_entropy_topsis_recommendation_${timeStr}.json`,
              path: `output/doe/${job.request.caseSetPrefix || "user300_doe"}/entropy_topsis_recommendation.json`,
              mimeType: "application/json",
              sizeBytes: 18800,
              canPreview: true,
              downloadUrl: `/api/v1/artifacts/${genId("art")}/download`
            }
          ];
        }
        return baseArtifacts;
      }
      case "SOLVER_BATCH":
        return [
          {
            artifactId: genId("art"),
            kind: "JSON_SUMMARY",
            name: `batch_solver_summary_${timeStr}.json`,
            path: `output/run_templates/solver_batch/batch_solver_summary_${timeStr}.json`,
            mimeType: "application/json",
            sizeBytes: 15300,
            canPreview: true,
            downloadUrl: `/api/v1/artifacts/${genId("art")}/download`
          },
          {
            artifactId: genId("art"),
            kind: "CSV_TABLE",
            name: `objectives_extracted_table_${timeStr}.csv`,
            path: `output/run_templates/solver_batch/objectives_extracted_table_${timeStr}.csv`,
            mimeType: "text/csv",
            sizeBytes: 124000,
            canPreview: true,
            downloadUrl: `/api/v1/artifacts/${genId("art")}/download`
          },
          {
            artifactId: genId("art"),
            kind: "CSV_TIMESERIES",
            name: `solver_raw_timeseries_${timeStr}.csv`,
            path: `output/run_templates/solver_batch/timeseries_${timeStr}.csv`,
            mimeType: "text/csv",
            sizeBytes: 1850000,
            canPreview: true,
            downloadUrl: `/api/v1/artifacts/${genId("art")}/download`
          }
        ];
      case "RESULT_EXTRACTION":
        return [
          {
            artifactId: genId("art"),
            kind: "JSON_SUMMARY",
            name: `extracted_summary_${timeStr}.json`,
            path: `output/result_extractions/summary.json`,
            mimeType: "application/json",
            sizeBytes: 4200,
            canPreview: true,
            downloadUrl: `/api/v1/artifacts/${genId("art")}/download`
          },
          {
            artifactId: genId("art"),
            kind: "CSV_TIMESERIES",
            name: `extracted_timeseries_${timeStr}.csv`,
            path: `output/result_extractions/timeseries.csv`,
            mimeType: "text/csv",
            sizeBytes: 1450000,
            canPreview: true,
            downloadUrl: `/api/v1/artifacts/${genId("art")}/download`
          },
          {
            artifactId: genId("art"),
            kind: "CSV_TABLE",
            name: `extracted_objectives_${timeStr}.csv`,
            path: `output/result_extractions/objectives.csv`,
            mimeType: "text/csv",
            sizeBytes: 32000,
            canPreview: true,
            downloadUrl: `/api/v1/artifacts/${genId("art")}/download`
          }
        ];
      case "SURROGATE_TRAINING":
        return [
          {
            artifactId: genId("art"),
            kind: "SURROGATE_MODEL",
            name: `trained_surrogate_small_sample_${timeStr}.pkl`,
            path: `output/surrogates/surrogate_model_${timeStr}.pkl`,
            mimeType: "application/octet-stream",
            sizeBytes: 1520000,
            canPreview: false,
            downloadUrl: `/api/v1/artifacts/${genId("art")}/download`
          },
          {
            artifactId: genId("art"),
            kind: "RAW_DATA",
            name: `surrogate_training_dataset_${timeStr}.csv`,
            path: `output/surrogates/training_dataset_${timeStr}.csv`,
            mimeType: "text/csv",
            sizeBytes: 248000,
            canPreview: true,
            downloadUrl: `/api/v1/artifacts/${genId("art")}/download`
          }
        ];
      case "MULTI_OBJECTIVE_OPTIMIZATION":
        return [{
          artifactId: genId("art"),
          kind: "OPTIMIZATION_REPORT",
          name: `pareto_optimization_report_${timeStr}.json`,
          path: `output/optimization/pareto_report.json`,
          mimeType: "application/json",
          sizeBytes: 32000,
          canPreview: true,
          downloadUrl: `/api/v1/artifacts/${genId("art")}/download`
        }];
      case "ENTROPY_TOPSIS_DECISION":
        return [{
          artifactId: genId("art"),
          kind: "DECISION_REPORT",
          name: `topsis_decision_report_${timeStr}.json`,
          path: `output/decisions/decision_report.json`,
          mimeType: "application/json",
          sizeBytes: 18400,
          canPreview: true,
          downloadUrl: `/api/v1/artifacts/${genId("art")}/download`
        }];
      case "OPTIMIZATION_EXPORT": {
        const artifacts: Artifact[] = [];
        const kinds = Array.isArray(job.request.exportKinds) ? job.request.exportKinds : ["PARETO_FRONT"];
        const formats = Array.isArray(job.request.formats) ? job.request.formats : ["CSV"];
        kinds.forEach((kind: string) => {
          formats.forEach((format: string) => {
            const ext = format.toLowerCase();
            const artifactId = genId("art");
            const isImage = ext === "png" || ext === "svg";
            const mimeType =
              ext === "svg"
                ? "image/svg+xml"
                : ext === "png"
                  ? "image/png"
                  : ext === "json"
                    ? "application/json"
                    : "text/csv";
            artifacts.push({
              artifactId,
              kind: isImage ? "PLOT" : kind === "TOPSIS_RECOMMENDATION" ? "DECISION_REPORT" : "CSV_TABLE",
              name: `optimization_${kind.toLowerCase()}_${timeStr}.${ext}`,
              path: `output/optimization/exports/${kind.toLowerCase()}_${timeStr}.${ext}`,
              mimeType,
              sizeBytes: isImage ? 420000 : 36000,
              canPreview: ext === "png" || ext === "svg" || ext === "json" || ext === "csv",
              downloadUrl: `/api/v1/artifacts/${artifactId}/download`
            });
          });
        });
        return artifacts;
      }
      default:
        return [];
    }
  }

  // --- API Handlers ---

  public getDashboardSummary(): DashboardSummary {
    return {
      repo: {
        branch: "main",
        isClean: true,
        lastCommit: "c69fe9d"
      },
      latestGate: {
        status: "FAIL",
        summaryPath: "docs/status_reports/project_optimization_gates_2026-07-01.md",
        finishedAt: "2026-07-01T16:30:00+08:00"
      },
      latestParity: {
        status: "FAIL",
        reportPath: "output/run_templates/solver_parity/baseline_parity_report.json",
        maxRelativeError: 0.0411
      },
      runtime: {
        ansysAvailable: true,
        openseespyInprocAvailable: true,
        user300PatchPackage: "PASS"
      }
    };
  }

  public getTemplates(solver?: string, workflow?: string, includeLegacy = false): Template[] {
    return this.templates.filter(t => {
      if (solver && t.solver !== solver) return false;
      if (workflow && t.workflow !== workflow) return false;
      if (!includeLegacy && t.isLegacy) return false;
      return true;
    });
  }

  public getTemplate(id: string): Template | undefined {
    return this.templates.find(t => t.templateId === id);
  }

  public runPreflight(configPath: string): PreflightResponse {
    const solver = configPath.includes("ansys") ? "ANSYS" : "OPENSEESPY_INPROC";
    return {
      status: "PASS",
      configPath,
      solver,
      executionMode: "run",
      pathChecks: [
        {
          name: "solver_kwargs.model_path",
          path: "bridge_models/stbridge_opensees/stbridge_opensees_modal_builder.py",
          exists: true
        },
        {
          name: "load_inputs.wind_file",
          path: "output/load_cases/vertical_wind_seed_42.csv",
          exists: true
        }
      ],
      raw: {}
    };
  }

  public getJobs(status?: JobStatus, type?: string, page = 1, pageSize = 20) {
    let filtered = this.jobs;
    if (status) filtered = filtered.filter(j => j.status === status);
    if (type) filtered = filtered.filter(j => j.type === type);

    // Sort by date descending
    filtered = [...filtered].sort((a, b) => b.createdAt.localeCompare(a.createdAt));

    const totalItems = filtered.length;
    const totalPages = Math.ceil(totalItems / pageSize);
    const startIndex = (page - 1) * pageSize;
    const paginated = filtered.slice(startIndex, startIndex + pageSize);

    return {
      data: paginated,
      pagination: { page, pageSize, totalItems, totalPages }
    };
  }

  public getJob(jobId: string): Job | undefined {
    return this.jobs.find(j => j.jobId === jobId);
  }

  public createJob(type: JobType, params: Record<string, any>): Job {
    let title = `任务: ${type}`;
    if (type === "PROJECT_GATE_FAST") title = "快速回归门槛测试 (Regression Gate)";
    else if (type === "SOLVER_PARITY") title = "双求解器一致性对齐测试 (Solver Parity)";
    else if (type === "BASELINE_DOE") title = "小样本基线 DOE 计算";
    else if (type === "EXPERIMENT_DESIGN") title = "USER300 阻尼器试验设计";
    else if (type === "LOAD_WIND_VERTICAL") title = "竖向风荷载时程配置";
    else if (type === "LOAD_TRAFFIC_RANDOM") title = "随机交通流荷载配置";
    else if (type === "LOAD_EARTHQUAKE") title = "地震动输入加速度时程配置";
    else if (type === "LOAD_CURVE_EXPORT") title = "荷载曲线图导出";
    else if (type === "COMMAND_STREAM_ASSEMBLY") title = `组装 ${params.solver || "ANSYS"} 命令流`;
    else if (type === "SOLVER_BATCH") title = `${params.solver || "ANSYS"} 并行批处理计算`;
    else if (type === "RESULT_EXTRACTION") title = `求解器结果自动提取`;
    else if (type === "SURROGATE_TRAINING") title = `代理模型自适应拟合训练`;
    else if (type === "ACTIVE_LEARNING") title = `主动学习 Infill 采样`;
    else if (type === "MULTI_OBJECTIVE_OPTIMIZATION") title = `结构多目标 Pareto 优化`;
    else if (type === "ENTROPY_TOPSIS_DECISION") title = `熵权 + TOPSIS 多准则方案决策`;
    else if (type === "OPTIMIZATION_EXPORT") title = `Pareto / 熵权 / TOPSIS 优化结果导出`;

    const newJob: Job = {
      jobId: genId("job"),
      type,
      status: "QUEUED",
      title,
      createdAt: new Date().toISOString(),
      request: params,
      artifacts: []
    };

    this.jobs.unshift(newJob);
    this.saveToStorage();
    return newJob;
  }

  public cancelJob(jobId: string): boolean {
    const job = this.jobs.find(j => j.jobId === jobId);
    if (!job) return false;
    if (job.status === "SUCCEEDED" || job.status === "FAILED") return false;
    job.status = "CANCELLED";
    job.finishedAt = new Date().toISOString();
    this.saveToStorage();
    return true;
  }

  public getArtifacts(jobId?: string, kind?: string, page = 1, pageSize = 20) {
    let filtered = this.artifacts;
    if (jobId) {
      const job = this.jobs.find(j => j.jobId === jobId);
      filtered = job ? job.artifacts : [];
    }
    if (kind) {
      filtered = filtered.filter(a => a.kind === kind);
    }

    const totalItems = filtered.length;
    const totalPages = Math.ceil(totalItems / pageSize);
    const startIndex = (page - 1) * pageSize;
    const paginated = filtered.slice(startIndex, startIndex + pageSize);

    return {
      data: paginated,
      pagination: { page, pageSize, totalItems, totalPages }
    };
  }

  public getArtifact(id: string): Artifact | undefined {
    return this.artifacts.find(a => a.artifactId === id);
  }

  public getArtifactPreview(id: string): any {
    const art = this.getArtifact(id);
    if (!art) return null;

    if (art.kind === "COMMAND_STREAM") {
      return `! MOMO assembled command stream preview
! solver=ANSYS bridge=stbridge caseSet=cases_20260701_0001
/PREP7
! MODULE MODEL
! MODULE DAMPER element=USER300 material=VISCOUS layout=tower_girder_end_pair southCount=2 northCount=2 nodes=south_tower_36_481,south_tower_107_498,north_tower_36_518,north_tower_107_521 c=1200 alpha=0.35 unitCost=180000
! MODULE LOAD_EARTHQUAKE source=PEER_RECORD scaling=SPECTRUM_MATCH targetSpectrum=JTG/T 2231-01
! MODULE TRANSIENT dt=0.02 duration=40
! MODULE POSTPROCESS targets=beamEndDisplacement,towerBaseShear,towerBaseMoment,beamEndCumulativeDisplacement,damperCost
! WRITE summary.json
! WRITE timeseries.csv
! WRITE objectives.csv
FINISH`;
    }

    if (art.kind === "PARITY_REPORT" || art.name === "extracted_summary_demo.json") {
      return {
        status: "PASS",
        reportPath: art.path,
        referenceSolver: "ANSYS/MAPDL",
        candidateSolver: "OpenSeesPy",
        relativeTolerance: 0.1,
        absoluteTolerance: 0,
        metrics: [
          { name: "beamEndDisplacement", label: getTargetLabel("beamEndDisplacement"), relativeError: 0.021, accepted: true },
          { name: "towerBaseShear", label: getTargetLabel("towerBaseShear"), relativeError: 0.030, accepted: true },
          { name: "towerBaseMoment", label: getTargetLabel("towerBaseMoment"), relativeError: 0.1835, accepted: false }
        ],
        artifacts: []
      };
    }

    if (art.mimeType === "application/json" || art.kind === "JSON_SUMMARY") {
      return {
        description: "计算分析目标函数响应值提取汇总",
        objectives: {
          beamEndDisplacement: 0.0784,
          towerBaseShear: 5.23e7,
          towerBaseMoment: 2.74e9,
          beamEndCumulativeDisplacement: 18.42,
          damperCost: 720000
        },
        constraints: {
          beamEndDisplacementLimit: 0.12,
          towerBaseShearLimit: 68000,
          towerBaseMomentLimit: 3200000,
          targetLimitSource: "无控状态值",
          targetLimitsPassed: true
        },
        runInfo: {
          solver: "ANSYS",
          modelHash: "a8f3b201...",
          wallTimeS: 312.4
        }
      };
    }

    if (art.mimeType === "text/csv" || art.kind === "CSV_TIMESERIES" || art.kind === "CSV_TABLE" || art.kind === "RAW_DATA") {
      // Simulate CSV structure
      return {
        headers: ["Time", "Beam_End_Disp", "Tower_Base_Shear", "Tower_Base_Moment", "Beam_End_Cum_Disp", "Damper_Cost"],
        previewRows: Array.from({ length: 50 }, (_, i) => [
          (i * 0.1).toFixed(1),
          (Math.sin(i * 0.2) * 0.05 * Math.exp(-i * 0.01)).toFixed(5),
          (5.0e7 + Math.sin(i * 0.15) * 2.5e6).toFixed(2),
          (2.7e9 + Math.cos(i * 0.15) * 1.2e8).toFixed(2),
          (i * 0.0037).toFixed(5),
          "720000"
        ]),
        totalRows: 6000
      };
    }

    if (art.mimeType === "text/markdown" || art.kind === "STATUS_REPORT") {
      return `# 桥梁抗风与阻尼器优化平台状态报告

本报告记录了对双求解器 (ANSYS / OpenSeesPy) 基准位移与剪力响应进行一致性对齐及帕累托多目标优化决策的详细指标。

## 一致性验证指标 (Solver Parity)

- **基准算例 ID**: \`solver_parity_baseline\`
- **模型文件**: \`stbridge_opensees_modal_builder.py\`
- **ANSYS MAPDL 版本**: 2023 R1
- **OpenSeesPy 运行模式**: 进程内 DLL (In-Process)

| 目标名称 (Objective) | ANSYS 最大值 | OpenSeesPy 最大值 | 相对误差 (Relative Error) | 门槛判定 (Gate Status) |
| :--- | :---: | :---: | :---: | :---: |
| 梁端位移 (m) | 0.0784 | 0.0799 | 1.91% | **PASS** |
| 塔底剪力 (N) | 5.23e7 | 5.39e7 | 3.00% | **PASS** |
| 塔底弯矩 (N*m) | 2.74e9 | 3.24e9 | 18.35% | **FAIL** |
| 阻尼器行程 (m) | 0.245 | 0.252 | 2.85% | **PASS** |

## 多目标阻尼器优化结果

使用自适应代理模型配合 NSGA-II 算法进行计算，得到 Pareto 前沿。使用**熵权 TOPSIS 算法**进行决策，权重分配如下：
- **梁端位移比重**: 28.0%
- **塔底内力比重**: 49.0%
- **运营梁端累计位移比重**: 23.0%

推荐最佳设计方案为：**方案 #1 (配置参数: α = 0.40, β = 0.60)**
- TOPSIS 相对贴近度: **0.847**
- FEM 校验状态: **已完成复核并验证成功** (✓ PASS)
`;
    }

    if (art.kind === "PLOT") {
      // Return a placeholder or mock SVG path
      return {
        type: "plot",
        url: "https://images.unsplash.com/photo-1541888946425-d81bb19240f5?q=80&w=600&auto=format&fit=crop"
      };
    }

    return null;
  }

  public getLatestParityReport(): ParityReport {
    return {
      status: "FAIL",
      reportPath: "output/run_templates/solver_parity/baseline_parity_report.json",
      referenceSolver: "ANSYS/MAPDL",
      candidateSolver: "OpenSeesPy",
      relativeTolerance: 0.1,
      absoluteTolerance: 0,
      metrics: [
        { name: "beamEndDisplacement", label: getTargetLabel("beamEndDisplacement"), relativeError: 0.021, accepted: true },
        { name: "towerBaseShear", label: getTargetLabel("towerBaseShear"), relativeError: 0.030, accepted: true },
        { name: "towerBaseMoment", label: getTargetLabel("towerBaseMoment"), relativeError: 0.1835, accepted: false }
      ],
      artifacts: []
    };
  }

  // --- Wind workflow mocks ---
  public runWindWorkflow(request: any): Job {
    return this.createJob("LOAD_WIND_VERTICAL", {
      source: "WIND_MODULE",
      appliedComponent: "VERTICAL",
      windRequest: request
    });
  }

  public exportWind(format: string, _req: any): Artifact {
    const art: Artifact = {
      artifactId: genId("art"),
      kind: "LOAD_CASE",
      name: `wind_export_${format.toLowerCase()}_${new Date().toISOString().split("T")[0]}.csv`,
      path: `output/load_cases/wind_export_${format.toLowerCase()}.csv`,
      mimeType: "text/csv",
      sizeBytes: 120000,
      canPreview: true,
      downloadUrl: `/api/v1/artifacts/${genId("art")}/download`
    };
    this.artifacts.unshift(art);
    this.saveToStorage();
    return art;
  }

  // --- Surrogate & optimization mocks ---
  public getParetoAndTopsis(_optRunId: string) {
    return {
      candidates: [
        { id: "c1", params: { alpha: 0.40, beta: 0.60 }, objectives: { beamEndDisplacement: 0.0784, towerBaseShear: 5.23e7, towerBaseMoment: 2.74e9, beamEndCumulativeDisplacement: 18.4, damperCost: 720000 }, constraintsPassed: true, femReviewed: true, topsisScore: 0.847, rank: 1 },
        { id: "c2", params: { alpha: 0.30, beta: 0.70 }, objectives: { beamEndDisplacement: 0.0712, towerBaseShear: 5.45e7, towerBaseMoment: 2.86e9, beamEndCumulativeDisplacement: 16.9, damperCost: 810000 }, constraintsPassed: true, femReviewed: true, topsisScore: 0.791, rank: 2 },
        { id: "c3", params: { alpha: 0.50, beta: 0.50 }, objectives: { beamEndDisplacement: 0.0982, towerBaseShear: 4.98e7, towerBaseMoment: 2.66e9, beamEndCumulativeDisplacement: 21.3, damperCost: 650000 }, constraintsPassed: true, femReviewed: false, topsisScore: 0.734, rank: 3 },
        { id: "c4", params: { alpha: 0.20, beta: 0.80 }, objectives: { beamEndDisplacement: 0.0652, towerBaseShear: 5.92e7, towerBaseMoment: 3.01e9, beamEndCumulativeDisplacement: 15.5, damperCost: 920000 }, constraintsPassed: true, femReviewed: true, topsisScore: 0.684, rank: 4 },
        { id: "c5", params: { alpha: 0.60, beta: 0.40 }, objectives: { beamEndDisplacement: 0.1245, towerBaseShear: 4.72e7, towerBaseMoment: 2.51e9, beamEndCumulativeDisplacement: 24.8, damperCost: 560000 }, constraintsPassed: false, femReviewed: false, topsisScore: 0.512, rank: 5 }
      ],
      entropyWeights: {
        beamEndDisplacement: 0.28,
        towerBaseShear: 0.23,
        towerBaseMoment: 0.26,
        beamEndCumulativeDisplacement: 0.23
      },
      recommendation: {
        candidateId: "c1",
        explanation: "该方案在地震梁端位移、塔底剪力/弯矩和运营期梁端累计位移之间取得了较均衡的折中，且已通过 ANSYS 实体有限元模型 (FEM) 复核。"
      }
    };
  }
}

export const mockDb = new MockDatabase();
