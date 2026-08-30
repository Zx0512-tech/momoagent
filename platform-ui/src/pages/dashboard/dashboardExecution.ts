import type { JobType, TaskExecutionTarget } from "../../api/types";

const BASELINE_OPTIMIZATION_WORKFLOW_BY_SOLVER: Partial<Record<string, string>> = {
  ANSYS: "docs/examples/templates/ansys_run_joint_baseline_workflow_template.json",
  OPENSEESPY_INPROC: "docs/examples/templates/openseespy_inproc_run_joint_baseline_workflow_template.json"
};

interface DashboardExecutionInput {
  projectName: string;
  modelFileName: string;
  solver: string;
  scenario: string;
  executionTarget: TaskExecutionTarget;
  requiredModules: string[];
  executionTimeoutS: number;
}

interface DashboardExecutionRequest {
  type: JobType;
  params: Record<string, unknown>;
}

export function buildDashboardExecutionRequest(input: DashboardExecutionInput): DashboardExecutionRequest | null {
  const workflowConfigPath = BASELINE_OPTIMIZATION_WORKFLOW_BY_SOLVER[input.solver];
  if (
    input.scenario !== "EARTHQUAKE"
    || input.executionTarget !== "OPTIMIZATION_DECISION"
    || !workflowConfigPath
  ) return null;

  return {
    type: "MULTI_OBJECTIVE_OPTIMIZATION",
    params: {
      projectName: input.projectName,
      modelFileName: input.modelFileName,
      solver: input.solver,
      scenario: input.scenario,
      executionTarget: input.executionTarget,
      requiredModules: input.requiredModules,
      runMode: "REAL_BASELINE_OPTIMIZATION",
      workflowConfigPath,
      executionTimeoutS: input.executionTimeoutS
    }
  };
}
