from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EngineeringTaskSpec:
    """工程任务类型的唯一事实来源。

    此前 Agent 注册键、报告文件名、规划工具、求解工具和 Job 完成后的
    目标步骤散落在 agent_service / agent_harness / agent_conversation 的
    多张手写映射表里，新增任务类型需要改 6+ 处且容易漏。集中到这里后，
    新任务类型只需登记一条 spec。
    """

    task_type: str
    # _agents() 注册表中的键。
    agent_key: str
    # 证据报告制品文件名。
    report_file_name: str
    # REQUIREMENTS 步骤的规划工具（澄清合并时用于 Guard 授权）。
    plan_tool: str
    # 审批通过后创建真实 Job 时授权的求解工具。
    solver_run_tool: str
    # Job 成功结束后 Python 自动推进游标的目标步骤。
    post_job_target_step: str


_SPECS: dict[str, EngineeringTaskSpec] = {
    spec.task_type: spec
    for spec in (
        EngineeringTaskSpec(
            task_type='ANALYSIS',
            agent_key='ANALYSIS',
            report_file_name='analysis_evidence_report.json',
            plan_tool='analysis.plan',
            solver_run_tool='analysis.run',
            post_job_target_step='EVIDENCE_REVIEW',
        ),
        EngineeringTaskSpec(
            task_type='DAMPER_COMPARISON',
            agent_key='DAMPER_COMPARISON',
            report_file_name='damper_comparison_evidence_report.json',
            plan_tool='comparison.plan',
            solver_run_tool='comparison.run',
            post_job_target_step='COMPARISON',
        ),
        EngineeringTaskSpec(
            task_type='DAMPER_OPTIMIZATION',
            agent_key='DAMPER_OPTIMIZATION',
            report_file_name='full_optimization_evidence_report.json',
            plan_tool='optimization.prepare_plan',
            solver_run_tool='optimization.run_baseline',
            post_job_target_step='REVIEW',
        ),
        EngineeringTaskSpec(
            task_type='DAMPER_PARAMETER_SWEEP',
            agent_key='DAMPER_PARAMETER_SWEEP',
            report_file_name='damper_parameter_sweep_evidence_report.json',
            plan_tool='sweep.plan',
            solver_run_tool='sweep.run',
            # EXECUTION 成功后先做结果提取，再进入证据审查（见 _parameter_sweep_workflow）。
            post_job_target_step='RESULT_EXTRACTION',
        ),
    )
}


def engineering_task_spec(task_type: str) -> EngineeringTaskSpec | None:
    return _SPECS.get(str(task_type))


def report_file_name(task_type: str) -> str | None:
    spec = engineering_task_spec(task_type)
    return spec.report_file_name if spec else None
