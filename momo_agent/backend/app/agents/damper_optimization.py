from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pyansys_bridge.optimization.config_runner import preflight_config

from app.agents.analysis import ANALYSIS_TARGET_SET_BY_LOAD_KIND
from app.agents.core import AgentContext
from app.agents.engineering import EngineeringAgent, PreparedApproval, ReviewOutcome, narrative_safe
from app.agents.evidence_gates import (
    artifacts_have_no_non_real_markers,
    engineering_preflight_passed,
    input_provenance_passed,
    output_manifest_passed,
    result_catalog_passed,
    solver_version_profile_passed,
)
from app.services.agent_engineering import build_engineering_contract
from app.services.agent_evidence import build_agent_input_provenance, build_solver_version_profile
from app.services.platform_readiness import build_readiness_report
from app.core.engineering_limits import (
    DOE_ACTIVE_LEARNING_MAX_ITERATIONS,
    DOE_INITIAL_DEFAULT,
    DOE_INITIAL_MAX,
    DOE_INITIAL_MIN,
)


REPO_ROOT = Path(__file__).resolve().parents[4]
ENGINEERING_WORKFLOW_PATHS = {
    'ANSYS': 'docs/examples/templates/ansys_run_joint_baseline_workflow_template.json',
    'OPENSEESPY_INPROC': 'docs/examples/templates/openseespy_inproc_run_joint_baseline_workflow_template.json',
}
# 风工况两个求解器都有已登记的 baseline-first 优化模板，与 platform_store 的
# WIND_OPTIMIZATION_WORKFLOW_CONFIGS 保持同一口径。
WIND_OPTIMIZATION_WORKFLOW_PATHS = {
    'ANSYS': 'docs/examples/templates/ansys_run_wind_baseline_workflow_template.json',
    'OPENSEESPY_INPROC': (
        'docs/examples/templates/openseespy_inproc_run_wind_baseline_workflow_template.json'
    ),
}
# 车流工况同样两个求解器都有已登记的 baseline-first 优化模板。
TRAFFIC_OPTIMIZATION_WORKFLOW_PATHS = {
    'ANSYS': 'docs/examples/templates/ansys_run_traffic_baseline_workflow_template.json',
    'OPENSEESPY_INPROC': (
        'docs/examples/templates/openseespy_inproc_run_traffic_baseline_workflow_template.json'
    ),
}
OPTIMIZATION_WORKFLOW_PATHS_BY_LOAD_KIND = {
    'EARTHQUAKE': ENGINEERING_WORKFLOW_PATHS,
    'WIND': WIND_OPTIMIZATION_WORKFLOW_PATHS,
    'TRAFFIC': TRAFFIC_OPTIMIZATION_WORKFLOW_PATHS,
}
# 风工况优化的唯一目标是梁端累计位移；放开其他响应会让审批冻结的目标与
# 模板里的 objective_specs 不一致。车流工况同口径：只优化累计位移，
# 不纳入塔底剪力和弯矩——车流是竖向移动荷载，塔底内力不是它的控制响应。
OPTIMIZATION_RESPONSE_CATALOG_BY_LOAD_KIND = {
    'EARTHQUAKE': frozenset({
        'max_girder_end_displacement',
        'max_tower_base_shear',
        'max_tower_base_moment',
        'max_damper_force',
        'max_damper_stroke',
        'dissipated_energy',
        'cumulative_displacement',
    }),
    'WIND': frozenset({'cumulative_displacement'}),
    'TRAFFIC': frozenset({'cumulative_displacement'}),
}
ENGINEERING_OPTIMIZATION_PLAN = [
    '校验标准荷载 Artifact 的 ID、SHA256、时间轴和作用方式。',
    '检查求解器、Dispatcher、模型、模板和阻尼器校准证据。',
    '从 STbridge 受控候选表选择阻尼器布置，不接受模型生成任意节点。',
    f'运行无控基线和审批冻结的初始 DOE 设计（{DOE_INITIAL_MIN}–{DOE_INITIAL_MAX}，默认 {DOE_INITIAL_DEFAULT}）。',
    '使用稳定 10 折交叉验证比较代理模型，最多执行 2 轮主动学习。',
    '枚举 728 个离散候选并应用无控基线物理约束。',
    '生成 Pareto 前沿与熵权 TOPSIS 推荐。',
    '执行独立 FEM validation 和最多 1 轮 final review 修正。',
    '生成带 Job、Artifact、SHA256、运行模式和验收措辞的证据报告。',
]
REQUIRED_REAL_OPTIMIZATION_ARTIFACTS = {
    'real_workflow_summary.json',
    'real_optimization_summary.json',
    'real_baseline_summary.json',
    'real_earthquake_workflow_overview.json',
    'real_output_manifest.json',
}
# 概览制品按荷载类型命名，避免风工况产出叫 earthquake 的文件。
# 与 platform_store.OPTIMIZATION_OVERVIEW_ARTIFACT_NAMES 保持同一口径。
OPTIMIZATION_OVERVIEW_ARTIFACT_NAMES = {
    'EARTHQUAKE': 'real_earthquake_workflow_overview.json',
    'WIND': 'real_wind_workflow_overview.json',
    'TRAFFIC': 'real_traffic_workflow_overview.json',
}


def required_real_optimization_artifacts(load_kind: str) -> set[str]:
    """按荷载类型返回必需的真实优化制品清单。"""
    overview_name = OPTIMIZATION_OVERVIEW_ARTIFACT_NAMES.get(
        str(load_kind or 'EARTHQUAKE').upper(),
        OPTIMIZATION_OVERVIEW_ARTIFACT_NAMES['EARTHQUAKE'],
    )
    return {
        'real_workflow_summary.json',
        'real_optimization_summary.json',
        'real_baseline_summary.json',
        overview_name,
        'real_output_manifest.json',
    }


class EngineeringPlanner:
    def plan_engineering(self, goal: str, **kwargs: Any) -> Any: ...


@dataclass(frozen=True)
class OptimizationPlan:
    planner_mode: str
    intent: Any
    workflow_contract: dict[str, Any]
    plan: list[str]


class DamperOptimizationAgent(EngineeringAgent):
    """DAMPER_OPTIMIZATION 的统一 baseline-first 工程 Agent。

    优化严格度由 workflowContract.optimizationProfile / optimizationPolicy 决定；
    求解器、荷载、阻尼器和布置仍是独立工程配置，不再存在专用“完整优化”执行分支。
    """

    task_type = 'DAMPER_OPTIMIZATION'
    approval_action = 'RUN_ENGINEERING_WORKFLOW'
    workflow_paths = ENGINEERING_WORKFLOW_PATHS

    def __init__(
        self,
        *,
        planner: EngineeringPlanner,
        store: Any,
        dispatcher: Any,
        repo_root: Path = REPO_ROOT,
        readiness_builder: Any | None = None,
        preflight_runner: Any | None = None,
        solver_profile_builder: Any | None = None,
    ) -> None:
        self.planner = planner
        self.store = store
        self.dispatcher = dispatcher
        self.repo_root = repo_root
        self.readiness_builder = readiness_builder or build_readiness_report
        self.preflight_runner = preflight_runner or preflight_config
        self.solver_profile_builder = solver_profile_builder or build_solver_version_profile

    def plan(self, context: AgentContext) -> OptimizationPlan:
        result = self.planner.plan_engineering(
            context.goal,
            requested_task=context.requested_task,
            has_file=context.has_attachment,
            attachment_summary=context.attachment_summary,
        )
        intent = result.intent
        contract_task = context.requested_task if intent.task_type == 'CLARIFICATION' else intent.task_type
        selected_layout_id = getattr(intent, 'selected_layout_id', None)
        load_kind = getattr(intent, 'load_kind', None)
        response_ids = list(getattr(intent, 'response_ids', []) or [])
        optimization_profile = str(
            getattr(intent, 'optimization_profile', None) or 'STANDARD'
        ).upper()
        contract = (
            build_engineering_contract(
                task_type=contract_task,
                solver=intent.solver,
                damper_type=intent.damper_type,
                response_ids=response_ids,
                selected_layout_id=selected_layout_id or 'TWO_PER_TOWER',
                load_kind=load_kind or 'EARTHQUAKE',
                optimization_profile=optimization_profile,
                field_sources={
                    'solver': 'USER_SPECIFIED',
                    'loadKind': 'USER_SPECIFIED' if load_kind else 'DEFAULT',
                    'selectedLayoutId': 'USER_SPECIFIED' if selected_layout_id else 'DEFAULT',
                    'responseIds': 'USER_SPECIFIED' if response_ids else 'DEFAULT',
                    'optimizationProfile': (
                        'USER_SPECIFIED' if optimization_profile != 'STANDARD' else 'DEFAULT'
                    ),
                    'budget': 'DEFAULT',
                },
            )
            if not intent.missing_fields
            else {}
        )
        return OptimizationPlan(
            planner_mode=result.planner_mode,
            intent=intent,
            workflow_contract=contract,
            plan=list(ENGINEERING_OPTIMIZATION_PLAN),
        )

    def prepare_approval(
        self,
        run: dict[str, Any],
        *,
        mapping: dict[str, Any],
        standard_artifact_id: str | None,
        standard_sha256: str | None,
    ) -> PreparedApproval:
        return self._prepare_optimization(
            run,
            mapping=mapping,
            standard_artifact_id=standard_artifact_id,
            standard_sha256=standard_sha256,
        )

    def _prepare_optimization(
        self,
        run: dict[str, Any],
        *,
        mapping: dict[str, Any],
        standard_artifact_id: str | None,
        standard_sha256: str | None,
    ) -> PreparedApproval:
        contract = dict(run.get('workflowContract') or {})
        damper = dict(contract.get('damper') or {})
        solver = str(contract.get('solver') or 'ANSYS')
        load_kind = str(mapping.get('loadKind') or (run.get('intent') or {}).get('loadKind') or '')
        registered_paths = OPTIMIZATION_WORKFLOW_PATHS_BY_LOAD_KIND.get(load_kind) or {}
        failure_message = None
        if not damper.get('optimizationReady'):
            failure_message = f'{damper.get("type") or "所选"} 阻尼器缺少 {solver} 真实优化校准证据，未生成执行审批。'
        elif not registered_paths:
            failure_message = f'{load_kind or "未识别"} 荷载尚无已登记的 baseline-first 真实优化模板，未生成执行审批。'
        elif solver not in registered_paths:
            failure_message = f'{load_kind} 真实优化暂未放行 {solver} 求解器，未生成执行审批。'
        elif not set(contract.get('responseIds') or []) <= OPTIMIZATION_RESPONSE_CATALOG_BY_LOAD_KIND[load_kind]:
            failure_message = '所选响应超出当前真实 baseline-first 优化模板的已验证提取目录，未生成执行审批。'
        contract_updates = {
            'loadArtifactId': standard_artifact_id,
            'loadSha256': standard_sha256,
            'loadKind': load_kind,
        }
        if failure_message:
            return PreparedApproval(
                passed=False,
                failure_status='UNSUPPORTED',
                failure_message=failure_message,
                preflight={'passed': False, 'reason': 'PRODUCTION_GATE'},
                contract_updates=contract_updates,
                plan=list(ENGINEERING_OPTIMIZATION_PLAN),
            )
        workflow_path = registered_paths[solver]
        readiness = self.readiness_builder(
            self.store,
            self.dispatcher,
            require_platform_ui=False,
            require_ansys=solver == 'ANSYS',
            require_openseespy=solver == 'OPENSEESPY_INPROC',
        )
        try:
            config_preflight = self.preflight_runner(self.repo_root / workflow_path)
            solver_version_profile = self.solver_profile_builder(
                self.repo_root / workflow_path,
                solver=solver,
            )
        except Exception as exc:
            config_preflight = {'error': type(exc).__name__, 'message': str(exc)}
            solver_version_profile = {'error': type(exc).__name__, 'message': str(exc)}
        passed = (
            readiness.get('status') == 'READY'
            and engineering_preflight_passed(config_preflight, solver)
            and solver_version_profile_passed(
                solver_version_profile,
                require_user300=solver == 'ANSYS',
            )
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
                contract_updates=contract_updates,
                plan=list(ENGINEERING_OPTIMIZATION_PLAN),
                failure_status='FAILED',
                failure_message='真实求解环境或登记配置预检未通过，未生成执行审批和 Job。',
            )
        layout_candidates = contract.get('layoutCandidates') or {}
        frozen_action = {
            'jobType': 'MULTI_OBJECTIVE_OPTIMIZATION',
            'solver': solver,
            'scenario': load_kind,
            'executionTarget': 'OPTIMIZATION_DECISION',
            'runMode': 'REAL_BASELINE_OPTIMIZATION',
            'workflowConfigPath': workflow_path,
            'executionTimeoutS': 7200,
            'loadKind': load_kind,
            'loadDatasetArtifactId': standard_artifact_id,
            'loadDatasetSha256': standard_sha256,
            'loadMapping': mapping,
            'damper': damper,
            'layoutCandidates': layout_candidates,
            'layoutRegistrySha256': self._payload_sha256(layout_candidates),
            'selectedLayoutId': contract.get('selectedLayoutId'),
            'selectedLayout': contract.get('selectedLayout'),
            'responseIds': contract.get('responseIds') or [],
            'optimizationProfile': contract.get('optimizationProfile') or 'STANDARD',
            'optimizationPolicy': contract.get('optimizationPolicy') or {},
            'budget': contract.get('budget') or {},
            'solverVersionProfile': solver_version_profile,
        }
        target_set_id = ANALYSIS_TARGET_SET_BY_LOAD_KIND.get(load_kind)
        if target_set_id:
            # 目标集是审批冻结的施加对象。执行侧的节点力绑定（风的等权分配、
            # 车流的逐节点列映射）都强制要求这个字段，缺了会 422 失败关闭而不是
            # 回退求解器默认节点；与 ANALYSIS/比选/扫描三条链同一口径。
            frozen_action['loadTargetSetId'] = target_set_id
            contract_updates['loadTargetSetId'] = target_set_id
        if load_kind == 'TRAFFIC':
            # 车流的稠密矩阵制品必须与逐节点 mapping 制品配对冻结：只有矩阵时
            # 求解侧无法知道哪一列对应哪个节点，DOE 每个设计点都会取错列。
            frozen_action['loadPointMappingArtifactId'] = mapping.get('pointMappingArtifactId')
            frozen_action['loadPointMappingSha256'] = mapping.get('pointMappingSha256')
        frozen_action['inputProvenance'] = build_agent_input_provenance(
            frozen_action,
            task_type='DAMPER_OPTIMIZATION',
        )
        return PreparedApproval(
            passed=True,
            frozen_action=frozen_action,
            approval_action=self.approval_action,
            approval_summary=(
                '批准后将用审批冻结的标准荷载、工程配置和优化 Profile '
                '创建一个原子真实 baseline-first 优化 Job。'
            ),
            preflight=preflight,
            contract_updates=contract_updates,
            plan=list(ENGINEERING_OPTIMIZATION_PLAN),
            extra_run_fields={
                'solverVersionProfile': solver_version_profile,
                'inputProvenance': frozen_action['inputProvenance'],
            },
        )

    def review(
        self,
        job: dict[str, Any],
        *,
        workflow_contract: dict[str, Any] | None = None,
    ) -> ReviewOutcome:
        if job.get('status') != 'SUCCEEDED':
            status = str(job.get('status') or 'FAILED')
            return ReviewOutcome(
                accepted=False,
                run_status=status,
                evidence_mode=status,
                checks={'jobStatus': False},
                message='优化任务未正常完成，不构成最终工程方案。',
            )
        result = job.get('result') or {}
        artifacts = job.get('artifacts') or []
        artifact_names = {artifact.get('name') for artifact in artifacts if artifact.get('sha256')}
        validation = result.get('validationStatus') or {}
        review = result.get('reviewStatus') or {}
        contract = workflow_contract or {}
        checks = {
            'realRunMode': result.get('mode') == 'real_baseline_optimization',
            'baselineCompleted': result.get('baselineStatus') == 'completed',
            'validationVerified': validation.get('all_verified_execution') is True,
            'validationAccepted': validation.get('all_accepted') is True,
            'reviewVerified': review.get('all_verified_execution') is True,
            'reviewAccepted': review.get('all_accepted') is True,
            'recommendationAccepted': result.get('finalRecommendationStatus') == 'ACCEPTED',
            'requiredArtifacts': required_real_optimization_artifacts(
                str(contract.get('loadKind') or 'EARTHQUAKE'),
            ) <= artifact_names,
            'artifactHashes': all(len(str(artifact.get('sha256') or '')) == 64 for artifact in artifacts),
            'realArtifactEvidence': artifacts_have_no_non_real_markers(artifacts, self.store),
            'solverVersionProfile': solver_version_profile_passed(
                result.get('solverVersionProfile') or {},
                require_user300=str(contract.get('solver') or 'ANSYS').upper() == 'ANSYS',
            ),
            'inputProvenance': input_provenance_passed(result.get('inputProvenance')),
            'outputManifest': output_manifest_passed(result, artifacts, self.store),
            'resultCatalog': (
                result_catalog_passed(result, artifacts, self.store)
                if result.get('resultCatalogArtifactId')
                else True
            ),
        }
        if contract.get('taskType') == 'DAMPER_OPTIMIZATION' or contract.get('damper'):
            load_evidence = result.get('customLoadEvidence') or {}
            if contract.get('loadArtifactId'):
                checks['approvedLoadArtifact'] = (
                    load_evidence.get('artifactId') == contract.get('loadArtifactId')
                    and load_evidence.get('sha256') == contract.get('loadSha256')
                )
            else:
                checks['verifiedTemplateLoad'] = not load_evidence
            layout_evidence = result.get('damperLayoutEvidence') or {}
            checks['approvedDamperLayout'] = (
                layout_evidence.get('layoutId') == contract.get('selectedLayoutId')
                and {key: value for key, value in layout_evidence.items() if key != 'layoutId'}
                == (contract.get('selectedLayout') or {})
            )
        accepted = all(checks.values())
        return ReviewOutcome(
            accepted=accepted,
            run_status='SUCCEEDED' if accepted else 'COMPLETED_DIAGNOSTIC',
            evidence_mode='REAL_FEM' if accepted else 'DIAGNOSTIC_ONLY',
            checks=checks,
            message=(
                '全部真实 FEM 门槛通过，可作为最终推荐方案。'
                if accepted
                else 'Job 已完成，但至少一个工程门槛未通过；结果仅供诊断，不是最终方案。'
            ),
            extra={
                'validationStatus': validation,
                'reviewStatus': review,
                'finalRecommendationStatus': result.get('finalRecommendationStatus'),
                'executionEvidence': result.get('executionEvidence'),
            },
        )

    def build_report(
        self,
        run: dict[str, Any],
        job: dict[str, Any],
        outcome: ReviewOutcome,
    ) -> dict[str, Any]:
        artifacts = [
            {
                'artifactId': artifact['artifactId'],
                'name': artifact['name'],
                'kind': artifact['kind'],
                'sha256': artifact['sha256'],
            }
            for artifact in job.get('artifacts', [])
        ]
        result = job.get('result') or {}
        task_type = run.get('taskType', 'DAMPER_OPTIMIZATION')
        contract = run.get('workflowContract') or {}
        report = {
            'agentRunId': run['runId'],
            'taskType': task_type,
            'goal': run['goal'],
            'plannerMode': run.get('plannerMode'),
            'jobId': job['jobId'],
            'jobStatus': job['status'],
            'runMode': result.get('mode'),
            'evidenceMode': outcome.evidence_mode,
            'isFinalSolution': outcome.accepted,
            'conclusion': outcome.message,
            'checks': outcome.checks,
            'artifacts': artifacts,
            'workflowContract': contract,
            'optimizationProfile': contract.get('optimizationProfile'),
            'optimizationPolicy': contract.get('optimizationPolicy'),
            'solverVersionProfile': result.get('solverVersionProfile'),
            'inputProvenance': result.get('inputProvenance') or [],
            'outputManifestArtifactId': result.get('outputManifestArtifactId'),
            'resultCatalogArtifactId': result.get('resultCatalogArtifactId'),
            'limitations': (
                '本次优化使用审批冻结的标准荷载 Artifact；阻尼器布置仅来自 STbridge 受控候选表。'
                if contract.get('loadArtifactId')
                else '本次优化使用已登记模板荷载；阻尼器布置仅来自 STbridge 受控候选表。'
            ),
        }
        return {
            **report,
            **outcome.extra,
        }

    def narrative_facts(self, report: dict[str, Any], job: dict[str, Any]) -> dict[str, Any]:
        result = job.get('result') or {}
        checks = report.get('checks') or {}
        return {
            'conclusion': report.get('conclusion'),
            'failedChecks': sorted(name for name, passed in checks.items() if not passed),
            'passedCheckCount': sum(1 for passed in checks.values() if passed),
            'totalCheckCount': len(checks),
            'finalRecommendationStatus': result.get('finalRecommendationStatus'),
            'baselineStatus': result.get('baselineStatus'),
            'validationStatus': narrative_safe(result.get('validationStatus')),
            'reviewStatus': narrative_safe(result.get('reviewStatus')),
            'recommendedParameters': narrative_safe(result.get('recommendedParameters')),
            'baselineObjectives': narrative_safe(result.get('baselineObjectives')),
            'recommendedObjectives': narrative_safe(result.get('recommendedObjectives')),
            'validationRelativeErrors': narrative_safe((result.get('validationStatus') or {}).get('relativeErrors')),
        }

    @staticmethod
    def _payload_sha256(payload: dict[str, Any]) -> str:
        import hashlib
        import json

        return hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
        ).hexdigest()
