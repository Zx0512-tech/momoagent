from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pyansys_bridge.optimization.config_runner import preflight_config

from app.agents.analysis import (
    ANALYSIS_TARGET_SET_BY_LOAD_KIND,
    ANALYSIS_TRAFFIC_WORKFLOW_PATHS,
    ANALYSIS_WIND_WORKFLOW_PATHS,
    ANALYSIS_WORKFLOW_PATHS_BY_LOAD_KIND,
    AnalysisAgent,
)
from app.agents.core import AgentContext
from app.agents.damper_comparison import DamperComparisonAgent, EngineeringPlanner
from app.agents.engineering import PreparedApproval, ReviewOutcome, narrative_safe
from app.agents.evidence_gates import (
    artifacts_have_no_non_real_markers,
    input_provenance_passed,
    output_manifest_passed,
    result_catalog_passed,
    solver_version_profile_passed,
)
from app.services.agent_engineering import build_parameter_sweep_contract
from app.services.agent_evidence import build_agent_input_provenance


# 批量参数计算复用对应工况的 ANALYSIS 基线模板：执行侧逐案例覆盖 damper_module、
# damper_params 和 omit_dampers，模板自带的无阻尼器设定不会生效。
#
# 这里刻意不复用 ANALYSIS_WORKFLOW_PATHS_BY_LOAD_KIND：那张表是 ANALYSIS 的放行
# 口径，给它新增工况会让批量链在没有任何实现和验收的情况下被顺带放行（车流工况
# 就撞过这个坑）。批量链维护自己的登记表，新工况必须显式加进来。
PARAMETER_SWEEP_WIND_WORKFLOW_PATHS = ANALYSIS_WIND_WORKFLOW_PATHS
PARAMETER_SWEEP_WORKFLOW_PATHS_BY_LOAD_KIND = {
    'EARTHQUAKE': ANALYSIS_WORKFLOW_PATHS_BY_LOAD_KIND['EARTHQUAKE'],
    'WIND': ANALYSIS_WIND_WORKFLOW_PATHS,
    'TRAFFIC': ANALYSIS_TRAFFIC_WORKFLOW_PATHS,
}


# 计划文案会进入审批卡片和 LLM grounding：工况名说错会让用户以为冻结了别的荷载。
PARAMETER_SWEEP_LOAD_LABELS = {
    'EARTHQUAKE': '地震荷载',
    'WIND': '风荷载',
    'TRAFFIC': '车流荷载',
}


def _parameter_sweep_plan(load_kind: str) -> list[str]:
    load_label = PARAMETER_SWEEP_LOAD_LABELS.get(load_kind, '地震荷载')
    return [
        f'冻结同一已验证{load_label}、STbridge 布置和响应提取口径。',
        '登记每个阻尼器参数案例，不生成无控基线，也不引入优化约束。',
        '检查对应求解器、模型和阻尼器类型的真实执行能力。',
        '整单批准后并发执行所有独立参数案例。',
        '提取每个案例的响应、制品和哈希证据。',
    ]


PARAMETER_SWEEP_PLAN = _parameter_sweep_plan('EARTHQUAKE')


@dataclass(frozen=True)
class DamperParameterSweepPlan:
    planner_mode: str
    intent: Any
    workflow_contract: dict[str, Any]
    plan: list[str]


class DamperParameterSweepAgent(DamperComparisonAgent):
    task_type = 'DAMPER_PARAMETER_SWEEP'
    approval_action = 'RUN_DAMPER_PARAMETER_SWEEP'

    def plan(self, context: AgentContext) -> DamperParameterSweepPlan:
        result = self.planner.plan_engineering(
            context.goal,
            requested_task=context.requested_task,
            has_file=context.has_attachment,
            attachment_summary=context.attachment_summary,
        )
        intent = result.intent
        cases = [case.model_dump(by_alias=True) for case in getattr(intent, 'cases', [])]
        load_kind = str(getattr(intent, 'load_kind', None) or 'EARTHQUAKE')
        contract = (
            build_parameter_sweep_contract(
                solver=intent.solver,
                cases=cases,
                response_ids=list(intent.response_ids),
                selected_layout_id=intent.selected_layout_id or 'TWO_PER_TOWER',
                load_kind=load_kind,
                max_concurrent_cases=intent.max_concurrent_cases,
            )
            if not intent.missing_fields and cases
            else {}
        )
        return DamperParameterSweepPlan(
            planner_mode=result.planner_mode,
            intent=intent,
            workflow_contract=contract,
            plan=_parameter_sweep_plan(load_kind),
        )

    def prepare_approval(
        self,
        run: dict[str, Any],
        *,
        mapping: dict[str, Any],
        standard_artifact_id: str | None,
        standard_sha256: str | None,
    ) -> PreparedApproval:
        contract = dict(run.get('workflowContract') or {})
        solver = str(contract.get('solver') or '').upper()
        cases = list(contract.get('cases') or [])
        load_kind = str(mapping.get('loadKind') or 'EARTHQUAKE')
        registered_paths = PARAMETER_SWEEP_WORKFLOW_PATHS_BY_LOAD_KIND.get(load_kind) or {}
        plan = _parameter_sweep_plan(load_kind)
        if solver not in registered_paths or not cases:
            return PreparedApproval(
                passed=False,
                failure_status='UNSUPPORTED',
                failure_message='参数批量计算仅支持已登记的地震、风和车流工况真实模型和求解器。',
                preflight={'passed': False, 'reason': 'PRODUCTION_GATE'},
                plan=plan,
            )
        # 风与车流的通道、单位、方向和施加目标集校验与对应的 ANALYSIS 完全同口径：
        # 复用同一门禁，避免多条链路的冻结条件各自漂移。求解器白名单不在其中，
        # 由上面的扫描侧模板表单独控制。车流还要校验逐节点 mapping 制品，
        # 因此把整个 mapping 传进门，而不只是 channels。
        load_gate = AnalysisAgent._load_kind_gate(
            load_kind,
            solver=solver,
            channels=list(mapping.get('channels') or []),
            standard_artifact_id=standard_artifact_id,
            standard_sha256=standard_sha256,
            mapping=mapping,
        )
        if load_gate is not None:
            reason, message = load_gate
            return PreparedApproval(
                passed=False,
                failure_status='UNSUPPORTED',
                failure_message=message,
                preflight={'passed': False, 'reason': reason},
                plan=plan,
            )
        template_path = registered_paths[solver]
        readiness = self.readiness_builder(
            self.store,
            self.dispatcher,
            require_platform_ui=False,
            require_ansys=solver == 'ANSYS',
            require_openseespy=solver == 'OPENSEESPY_INPROC',
        )
        try:
            config_preflight = self.preflight_runner(self.repo_root / template_path)
            solver_version_profile = self.solver_profile_builder(
                self.repo_root / template_path,
                solver=solver,
            )
        except Exception as exc:
            config_preflight = {'error': type(exc).__name__, 'message': str(exc)}
            solver_version_profile = {'error': type(exc).__name__, 'message': str(exc)}
        checks = list((config_preflight.get('path_checks') or {}).values())
        passed = (
            readiness.get('status') == 'READY'
            and bool(checks)
            and all(check.get('exists') is True for check in checks)
            and solver_version_profile_passed(
                solver_version_profile,
                require_user300=solver == 'ANSYS',
            )
            and all(case.get('productionReady') is True for case in cases)
        )
        preflight = {
            'passed': passed,
            'readiness': readiness,
            'config': config_preflight,
            'solverVersionProfile': solver_version_profile,
        }
        if not passed:
            return PreparedApproval(
                passed=False,
                preflight=preflight,
                plan=plan,
                failure_status='FAILED',
                failure_message='参数批量真实求解环境预检未通过，未生成审批和 Job。',
            )

        frozen_action = {
            'jobType': 'SOLVER_BATCH',
            'solver': solver,
            'scenario': load_kind,
            'runMode': 'REAL_DAMPER_PARAMETER_SWEEP',
            'workflowConfigPath': template_path,
            'executionTimeoutS': 7200,
            'cases': cases,
            'selectedLayoutId': contract.get('selectedLayoutId'),
            'selectedLayout': contract.get('selectedLayout'),
            'responseIds': contract.get('responseIds') or [],
            'budget': contract.get('budget') or {},
            'loadKind': load_kind,
            'loadDatasetArtifactId': standard_artifact_id,
            'loadDatasetSha256': standard_sha256,
            'loadMapping': mapping,
            'solverVersionProfile': solver_version_profile,
        }
        contract_updates = {
            'loadArtifactId': standard_artifact_id,
            'loadSha256': standard_sha256,
            'loadKind': load_kind,
        }
        target_set_id = ANALYSIS_TARGET_SET_BY_LOAD_KIND.get(load_kind)
        if target_set_id:
            # 执行侧按目标节点集绑定节点力，缺少该字段会失败关闭而不是回退默认节点。
            frozen_action['loadTargetSetId'] = target_set_id
            contract_updates['loadTargetSetId'] = target_set_id
        if load_kind == 'TRAFFIC':
            # 车流的稠密矩阵制品必须与逐节点 mapping 制品配对冻结：只有矩阵时
            # 求解侧无法知道哪一列对应哪个节点。门已校验存在性，这里落进冻结动作。
            frozen_action['loadPointMappingArtifactId'] = mapping.get('pointMappingArtifactId')
            frozen_action['loadPointMappingSha256'] = mapping.get('pointMappingSha256')
        frozen_action['inputProvenance'] = build_agent_input_provenance(
            frozen_action,
            task_type=self.task_type,
        )
        return PreparedApproval(
            passed=True,
            frozen_action=frozen_action,
            approval_action=self.approval_action,
            approval_summary=f'批准后将并发执行 {len(cases)} 个阻尼器参数真实 FEM 工况，不运行无控基线或优化。',
            preflight=preflight,
            contract_updates=contract_updates,
            plan=plan,
            extra_run_fields={
                'solverVersionProfile': solver_version_profile,
                'inputProvenance': frozen_action['inputProvenance'],
            },
        )

    def review(self, job: dict[str, Any], *, workflow_contract: dict[str, Any] | None = None) -> ReviewOutcome:
        # 作业状态门与 damper_comparison / damper_optimization 同口径：失败或仍在
        # 运行的作业不得进入证据装配，否则空 result 会让下面的检查逐条误判。
        if job.get('status') != 'SUCCEEDED':
            status = str(job.get('status') or 'FAILED')
            return ReviewOutcome(
                accepted=False,
                run_status=status,
                evidence_mode=status,
                checks={'jobStatus': False},
                message='批量参数 Job 未正常完成，不构成工程批量计算结论。',
            )
        result = job.get('result') or {}
        artifacts = job.get('artifacts') or []
        case_results = list(result.get('caseResults') or [])
        expected = len((workflow_contract or {}).get('cases') or case_results)
        all_cases_present = len(case_results) == expected and expected > 0
        checks = {
            'realRunMode': result.get('mode') == 'real_damper_parameter_sweep',
            'allCasesPresent': all_cases_present,
            'allCasesCompleted': all_cases_present and all(
                case.get('status') == 'completed' for case in case_results
            ),
            'allCasesVerified': all_cases_present and all(
                case.get('isVerifiedSolverOutput') is True for case in case_results
            ),
            'artifactHashes': bool(artifacts) and all(len(str(item.get('sha256') or '')) == 64 for item in artifacts),
            'realArtifactEvidence': bool(artifacts) and artifacts_have_no_non_real_markers(artifacts, self.store),
            'solverVersionProfile': solver_version_profile_passed(
                result.get('solverVersionProfile') or {},
                require_user300=str(result.get('solver') or '').upper() == 'ANSYS',
            ),
            'inputProvenance': input_provenance_passed(result.get('inputProvenance')),
            'outputManifest': output_manifest_passed(result, artifacts, self.store),
            'resultCatalog': result_catalog_passed(result, artifacts, self.store),
        }
        accepted = all(checks.values())
        return ReviewOutcome(
            accepted=accepted,
            run_status='SUCCEEDED' if accepted else 'COMPLETED_DIAGNOSTIC',
            evidence_mode='REAL_FEM' if accepted else 'DIAGNOSTIC_ONLY',
            checks=checks,
            message='所有阻尼器参数案例均通过真实 FEM 证据审查。' if accepted else '批量参数 Job 已结束，但至少一个案例或证据门槛未通过。',
            extra={'caseResults': case_results},
        )

    def build_report(self, run: dict[str, Any], job: dict[str, Any], outcome: ReviewOutcome) -> dict[str, Any]:
        result = job.get('result') or {}
        return {
            'agentRunId': run['runId'],
            'taskType': self.task_type,
            'goal': run['goal'],
            'plannerMode': run.get('plannerMode'),
            'jobId': job['jobId'],
            'jobStatus': job['status'],
            'runMode': result.get('mode'),
            'evidenceMode': outcome.evidence_mode,
            'isFinalResult': outcome.accepted,
            'conclusion': outcome.message,
            'caseResults': result.get('caseResults') or [],
            'checks': outcome.checks,
            'workflowContract': run.get('workflowContract'),
            'solverVersionProfile': result.get('solverVersionProfile'),
            'inputProvenance': result.get('inputProvenance') or [],
            'resultCatalogArtifactId': result.get('resultCatalogArtifactId'),
            'outputManifestArtifactId': result.get('outputManifestArtifactId'),
            'artifacts': [
                {
                    'artifactId': item['artifactId'],
                    'name': item['name'],
                    'kind': item['kind'],
                    'sha256': item['sha256'],
                }
                for item in job.get('artifacts', [])
            ],
            # 与审批说明和计划文案同一口径：批量只做正向求解，既不生成无控基线，
            # 也不引入优化约束，所以案例之间不能相互当作"改善量"来解读。
            'limitations': (
                '批量参数计算只对每个给定参数做正向真实求解，不生成无控基线，也不引入优化约束；'
                '各案例共用同一登记荷载、同一布置和同一响应提取口径，'
                '只有 REAL_FEM 状态可作为最终结论。'
            ),
        }

    def narrative_facts(self, report: dict[str, Any], job: dict[str, Any]) -> dict[str, Any]:
        checks = report.get('checks') or {}
        return {
            'conclusion': report.get('conclusion'),
            'failedChecks': sorted(name for name, passed in checks.items() if not passed),
            'passedCheckCount': sum(1 for passed in checks.values() if passed),
            'totalCheckCount': len(checks),
            'jobStatus': job.get('status'),
            'cases': [
                {
                    'caseId': case.get('caseId'),
                    'damperType': case.get('damperType'),
                    'parameters': narrative_safe(case.get('parameters')),
                    'status': case.get('status'),
                    'objectives': narrative_safe(case.get('objectives')),
                }
                for case in report.get('caseResults') or []
            ],
            'limitations': report.get('limitations'),
        }
