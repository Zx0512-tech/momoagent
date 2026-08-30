from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pyansys_bridge.core.ansys_damper import ansys_damper_commands
from pyansys_bridge.core.opensees_common import opensees_damper_block, opensees_direction
from pyansys_bridge.models import DamperParams, RealizableDamper
from pyansys_bridge.optimization.config_runner import preflight_config

from app.agents.analysis import (
    ANALYSIS_TARGET_SET_BY_LOAD_KIND,
    ANALYSIS_TRAFFIC_WORKFLOW_PATHS,
    ANALYSIS_WIND_WORKFLOW_PATHS,
    AnalysisAgent,
)
from app.agents.core import AgentContext
from app.agents.engineering import EngineeringAgent, PreparedApproval, ReviewOutcome, narrative_safe
from app.agents.evidence_gates import (
    artifacts_have_no_non_real_markers,
    input_provenance_passed,
    output_manifest_passed,
    result_catalog_passed,
    solver_version_profile_passed,
)
from app.services.agent_engineering import COMPARISON_LOAD_KINDS, build_damper_comparison_contract
from app.services.agent_evidence import (
    build_agent_input_provenance,
    build_damper_calibration_profiles,
    build_solver_version_profile,
)
from app.services.platform_readiness import build_readiness_report


REPO_ROOT = Path(__file__).resolve().parents[4]
ANALYSIS_WORKFLOW_PATHS = {
    'ANSYS': 'docs/examples/templates/ansys_run_earthquake_baseline_template.json',
    'OPENSEESPY_INPROC': 'docs/examples/templates/openseespy_inproc_run_earthquake_baseline_template.json',
}
# 比选链维护自己的登记表，不复用 ANALYSIS_WORKFLOW_PATHS_BY_LOAD_KIND：那是
# ANALYSIS 的放行口径，给它新增工况或求解器会让比选链在没有验收的情况下被顺带
# 放行（车流工况撞过这个坑）。新工况和新求解器必须显式加进来。
COMPARISON_WORKFLOW_PATHS_BY_LOAD_KIND = {
    'EARTHQUAKE': ANALYSIS_WORKFLOW_PATHS,
    'WIND': ANALYSIS_WIND_WORKFLOW_PATHS,
    'TRAFFIC': ANALYSIS_TRAFFIC_WORKFLOW_PATHS,
}
# USER300 标定证据所在的联合模板，按求解器分开登记。
COMPARISON_SOLVER_PROFILE_PATHS = {
    'ANSYS': 'docs/examples/templates/ansys_run_joint_baseline_workflow_template.json',
    'OPENSEESPY_INPROC': (
        'docs/examples/templates/openseespy_inproc_run_joint_baseline_workflow_template.json'
    ),
}
COMPARISON_PREFLIGHT_SOLVERS = {'ANSYS': 'ansys', 'OPENSEESPY_INPROC': 'openseespy_inproc'}
COMPARISON_LOAD_KIND_LABELS = {'EARTHQUAKE': '地震', 'WIND': '风', 'TRAFFIC': '车流'}
# 审批卡片里的环境检查项按求解器区分：OpenSeesPy 进程内执行不涉及 MAPDL 许可。
COMPARISON_ENVIRONMENT_LABELS = {
    'ANSYS': 'ANSYS、Dispatcher、MAPDL',
    'OPENSEESPY_INPROC': 'OpenSeesPy 进程内运行时、Dispatcher',
}
# 节点力工况：荷载制品、通道和目标集口径与单次 ANALYSIS 一致，直接复用
# AnalysisAgent._load_kind_gate。地震是一致激励，不走这条分支。
COMPARISON_NODAL_FORCE_LOAD_KINDS = frozenset({'WIND', 'TRAFFIC'})


def comparison_plan(load_kind: str = 'EARTHQUAKE', solver: str = 'ANSYS') -> list[str]:
    """按荷载类型和求解器生成比选计划文案。

    计划文案会进入审批卡片和 LLM grounding，风工况沿用"地震荷载"会让用户
    看到与实际冻结荷载不符的描述；求解器同理，OpenSeesPy 比选不应声称检查
    MAPDL 许可。
    """
    label = COMPARISON_LOAD_KIND_LABELS.get(load_kind, load_kind)
    environment = COMPARISON_ENVIRONMENT_LABELS.get(
        str(solver).upper(),
        COMPARISON_ENVIRONMENT_LABELS['ANSYS'],
    )
    return [
        f'冻结同一已验证{label}荷载、STbridge 布置和响应提取口径。',
        '按等最大出力剖面设计两个或三个阻尼器工况的参数。',
        f'检查 {environment}、USER300 校准证据和模板。',
        '生成各阻尼器 PRE_EXECUTION 命令流并记录 SHA256。',
        '整单批准后创建一个 REAL_DAMPER_COMPARISON Job。',
        '依次执行各真实 FEM 工况并提取相同响应。',
        '生成响应差异、命令流、Artifact 和哈希证据报告。',
    ]


DAMPER_COMPARISON_PLAN = comparison_plan()


class EngineeringPlanner:
    def plan_engineering(self, goal: str, **kwargs: Any) -> Any: ...


@dataclass(frozen=True)
class DamperComparisonPlan:
    planner_mode: str
    intent: Any
    workflow_contract: dict[str, Any]
    plan: list[str]


class DamperComparisonAgent(EngineeringAgent):
    task_type = 'DAMPER_COMPARISON'
    approval_action = 'RUN_DAMPER_COMPARISON'
    workflow_paths = ANALYSIS_WORKFLOW_PATHS

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
        calibration_builder: Any | None = None,
    ) -> None:
        self.planner = planner
        self.store = store
        self.dispatcher = dispatcher
        self.repo_root = repo_root
        self.readiness_builder = readiness_builder or build_readiness_report
        self.preflight_runner = preflight_runner or preflight_config
        self.solver_profile_builder = solver_profile_builder or build_solver_version_profile
        self.calibration_builder = calibration_builder or build_damper_calibration_profiles

    def plan(self, context: AgentContext) -> DamperComparisonPlan:
        result = self.planner.plan_engineering(
            context.goal,
            requested_task=context.requested_task,
            has_file=context.has_attachment,
            attachment_summary=context.attachment_summary,
        )
        intent = result.intent
        selected_layout_id = getattr(intent, 'selected_layout_id', None)
        response_ids = list(getattr(intent, 'response_ids', []) or [])
        requested_load_kind = getattr(intent, 'load_kind', None)
        load_kind = requested_load_kind or 'EARTHQUAKE'
        contract = (
            build_damper_comparison_contract(
                solver=intent.solver,
                damper_types=intent.damper_types,
                response_ids=response_ids,
                selected_layout_id=selected_layout_id or 'TWO_PER_TOWER',
                load_kind=load_kind,
                field_sources={
                    'solver': 'USER_SPECIFIED',
                    'loadKind': 'USER_SPECIFIED' if requested_load_kind else 'DEFAULT',
                    'selectedLayoutId': 'USER_SPECIFIED' if selected_layout_id else 'DEFAULT',
                    'responseIds': 'USER_SPECIFIED' if response_ids else 'DEFAULT',
                    'budget': 'DEFAULT',
                },
            )
            if not intent.missing_fields
            and len(intent.damper_types) in {2, 3}
            and load_kind in COMPARISON_LOAD_KINDS
            else {}
        )
        return DamperComparisonPlan(
            planner_mode=result.planner_mode,
            intent=intent,
            workflow_contract=contract,
            plan=comparison_plan(load_kind, getattr(intent, 'solver', 'ANSYS') or 'ANSYS'),
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
        solver = str(contract.get('solver') or 'ANSYS').upper()
        load_kind = str(mapping.get('loadKind') or (run.get('intent') or {}).get('loadKind') or 'EARTHQUAKE')
        channels = list(mapping.get('channels') or [])
        cases = list(contract.get('cases') or [])
        registered_paths = COMPARISON_WORKFLOW_PATHS_BY_LOAD_KIND.get(load_kind) or {}
        failure_message = None
        failure_reason = 'PRODUCTION_GATE'
        if solver not in registered_paths:
            failure_message = '真实多工况对比仅放行已登记的求解器和工况组合；未生成执行审批。'
        elif load_kind == 'EARTHQUAKE':
            if channels and len(channels) != 1:
                failure_message = '真实双工况对比仅放行单通道地震一致激励；未生成执行审批。'
        elif load_kind in COMPARISON_NODAL_FORCE_LOAD_KINDS:
            # 风与车流的荷载制品、通道和目标集口径都与单次 ANALYSIS 完全一致，
            # 复用同一判定，避免多条链路的放行口径各自漂移。车流还要校验
            # 逐节点 mapping 制品，因此把整个 mapping 传进门。
            gate = AnalysisAgent._load_kind_gate(
                load_kind,
                solver=solver,
                channels=channels,
                standard_artifact_id=standard_artifact_id,
                standard_sha256=standard_sha256,
                mapping=mapping,
            )
            if gate is not None:
                failure_reason, failure_message = gate
        else:
            failure_message = '真实双工况对比仅放行单通道地震一致激励；未生成执行审批。'
        if not failure_message:
            if len(cases) not in {2, 3} or len({case.get('damperType') for case in cases}) != len(cases):
                failure_message = '阻尼器对比必须包含两个或三个不同的受控工况；未生成执行审批。'
            elif not all(case.get('productionReady') is True for case in cases):
                failure_message = '至少一个阻尼器缺少该求解器的 USER300 校准证据；未生成执行审批。'
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
                preflight={'passed': False, 'reason': failure_reason},
                contract_updates=contract_updates,
                plan=comparison_plan(load_kind, solver),
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
                self.repo_root / COMPARISON_SOLVER_PROFILE_PATHS[solver],
                solver=solver,
            )
            # 标定目录按求解器分开取：两侧都有已登记的逐型 USER300 证据，
            # 但证据类别不同（ANSYS 是单元验收，OpenSeesPy 是运行时力法则一致性）。
            calibration_profiles = self.calibration_builder(
                [str(case['damperType']) for case in cases],
                solver=solver,
            )
        except Exception as exc:
            config_preflight = {'error': type(exc).__name__, 'message': str(exc)}
            solver_version_profile = {'error': type(exc).__name__, 'message': str(exc)}
            calibration_profiles = []
        checks = list((config_preflight.get('path_checks') or {}).values())
        passed = (
            readiness.get('status') == 'READY'
            and config_preflight.get('kind') == 'undamped_baseline'
            and config_preflight.get('solver') == COMPARISON_PREFLIGHT_SOLVERS[solver]
            and config_preflight.get('execution_mode') == 'run'
            and bool(checks)
            and all(check.get('exists') is True for check in checks)
            # 比选的等峰值反算 C 直接依赖阻尼器本构，两个求解器都要求 USER300 证据。
            and solver_version_profile_passed(solver_version_profile, require_user300=True)
            and len(calibration_profiles) == len(cases)
            and all(item.get('status') == 'VERIFIED' for item in calibration_profiles)
        )
        preflight = {
            'passed': passed,
            'readiness': readiness,
            'config': config_preflight,
            'solverVersionProfile': solver_version_profile,
            'damperCalibrationProfiles': calibration_profiles,
        }
        contract_updates.update({'damperCalibrationProfiles': calibration_profiles})
        if not passed:
            return PreparedApproval(
                passed=False,
                preflight=preflight,
                contract_updates=contract_updates,
                plan=comparison_plan(load_kind, solver),
                failure_status='FAILED',
                failure_message='双工况真实求解环境或登记配置预检未通过，未生成审批和 Job。',
            )

        command_streams = self._build_command_streams(run['runId'], contract)
        contract_updates['preExecutionCommandStreams'] = command_streams
        frozen_action = {
            'jobType': 'SOLVER_BATCH',
            'solver': solver,
            'scenario': load_kind,
            'runMode': 'REAL_DAMPER_COMPARISON',
            'workflowConfigPath': template_path,
            'executionTimeoutS': 7200,
            'comparisonBasis': contract.get('comparisonBasis'),
            'forceCapN': contract.get('forceCapN'),
            'forceCapScope': contract.get('forceCapScope'),
            'designVelocityMps': contract.get('designVelocityMps'),
            'cases': cases,
            'selectedLayoutId': contract.get('selectedLayoutId'),
            'selectedLayout': contract.get('selectedLayout'),
            'responseIds': contract.get('responseIds') or [],
            'budget': contract.get('budget') or {},
            'loadKind': load_kind,
            'loadDatasetArtifactId': standard_artifact_id,
            'loadDatasetSha256': standard_sha256,
            'loadMapping': mapping,
            'preExecutionCommandStreams': command_streams,
            'solverVersionProfile': solver_version_profile,
            'damperCalibrationProfiles': calibration_profiles,
        }
        target_set_id = ANALYSIS_TARGET_SET_BY_LOAD_KIND.get(load_kind)
        if target_set_id:
            # 目标集是审批冻结的施加对象，与单次 ANALYSIS 同一口径。
            frozen_action['loadTargetSetId'] = target_set_id
            contract_updates['loadTargetSetId'] = target_set_id
        if load_kind == 'TRAFFIC':
            # 车流的稠密矩阵制品必须与逐节点 mapping 制品配对冻结：只有矩阵时
            # 求解侧无法知道哪一列对应哪个节点。门已校验存在性，这里落进冻结动作。
            frozen_action['loadPointMappingArtifactId'] = mapping.get('pointMappingArtifactId')
            frozen_action['loadPointMappingSha256'] = mapping.get('pointMappingSha256')
        frozen_action['inputProvenance'] = build_agent_input_provenance(
            frozen_action,
            task_type='DAMPER_COMPARISON',
        )
        return PreparedApproval(
            passed=True,
            frozen_action=frozen_action,
            approval_action=self.approval_action,
            approval_summary=f'批准后将创建一个原子 Job，依次运行 {len(cases)} 个阻尼器真实 FEM 工况。',
            preflight=preflight,
            contract_updates=contract_updates,
            plan=comparison_plan(load_kind, solver),
            extra_run_fields={
                'solverVersionProfile': solver_version_profile,
                'damperCalibrationProfiles': calibration_profiles,
                'inputProvenance': frozen_action['inputProvenance'],
            },
            pending_command_streams=command_streams,
        )

    @staticmethod
    def _build_command_streams(run_id: str, contract: dict[str, Any]) -> list[dict[str, Any]]:
        layout = contract.get('selectedLayout') or {}
        node_pairs = list(layout.get('nodePairs') or [])
        direction = str(layout.get('direction') or 'X')
        solver = str(contract.get('solver') or 'ANSYS').upper()
        streams = []
        for case in contract.get('cases') or []:
            parameters = case['parameters']
            if case['damperType'] == 'VISCOUS':
                params = DamperParams(
                    c=float(parameters['c']),
                    alpha=float(parameters['alpha']),
                    regularization_velocity=float(parameters['vfloor']),
                )
            elif case['damperType'] == 'EDDY_CURRENT':
                params = DamperParams(c=float(parameters['fmax']), alpha=float(parameters['vcr']))
            else:
                params = DamperParams(c=float(parameters['fc']), alpha=float(parameters['vs']))
            dampers = tuple(
                RealizableDamper(
                    placement_name=f'{case["caseId"]}_{index}',
                    unit_index=1,
                    node_i=int(pair[0]),
                    node_j=int(pair[1]),
                    direction=direction,
                    params=params,
                )
                for index, pair in enumerate(node_pairs, start=1)
            )
            # 等峰值对比的 C 从 N 制力上限反算，两个求解器都按 c_scale=1.0 导出，
            # 命令流不可再按界面工程单位换算。
            if solver == 'OPENSEESPY_INPROC':
                # tag 由布置顺序确定：同一审批重复渲染必须逐字节一致，命令流
                # SHA256 才能作为冻结证据。
                lines: list[str] = []
                for index, damper in enumerate(dampers, start=1):
                    lines.extend(opensees_damper_block(
                        case['solverModule'],
                        damper,
                        mat_tag=9000 + index,
                        ele_tag=9100 + index,
                        direction=opensees_direction(direction),
                        c_scale=1.0,
                    ))
                command_text = '\n'.join(lines) + '\n'
            else:
                command_text = ansys_damper_commands(case['solverModule'], dampers, c_scale=1.0)
            streams.append({
                'caseId': case['caseId'],
                'damperType': case['damperType'],
                'solverModule': case['solverModule'],
                'phase': 'PRE_EXECUTION',
                'commandText': command_text,
            })
        return streams

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
                message='双工况对比 Job 未正常完成，不构成工程对比结论。',
            )
        result = job.get('result') or {}
        artifacts = job.get('artifacts') or []
        case_results = list(result.get('caseResults') or [])
        calibration_profiles = list(result.get('damperCalibrationProfiles') or [])
        command_artifacts = [item for item in artifacts if item.get('kind') == 'COMMAND_STREAM']
        distinct_cases = (
                len(case_results) in {2, 3}
                and len({case.get('damperType') for case in case_results}) == len(case_results)
        )
        checks = {
            'realRunMode': result.get('mode') == 'real_damper_comparison',
            'distinctCases': distinct_cases,
            # 保留旧审计字段，避免历史报告/客户端按旧键读取时丢失诊断。
            'twoDistinctCases': distinct_cases,
            'allCasesCompleted': all(case.get('status') == 'completed' for case in case_results),
            'allCasesVerified': all(case.get('isVerifiedSolverOutput') is True for case in case_results),
            'equalPeakForceBasis': result.get('comparisonBasis') == 'EQUAL_PEAK_FORCE',
            'forceCapScope': result.get('forceCapScope') == 'PER_PHYSICAL_DAMPER',
            # 没有可对比的共有数值指标就不构成对比结论，只能降级为诊断。
            'responseComparison': bool((result.get('responseComparison') or {}).get('metrics')),
            'executedCommandStreams': len(command_artifacts) == len(case_results),
            'artifactHashes': bool(artifacts) and all(
                len(str(item.get('sha256') or '')) == 64 for item in artifacts
            ),
            'realArtifactEvidence': artifacts_have_no_non_real_markers(artifacts, self.store),
            # 两个求解器都要求 USER300 证据，与 prepare_approval 的预检同口径：
            # 等峰值反算 C 直接依赖阻尼器本构，缺证据的结果不构成对比结论。
            # 参数扫描按结果求解器放宽，比选不放宽——两侧都有逐型标定目录。
            'solverVersionProfile': solver_version_profile_passed(
                result.get('solverVersionProfile') or {},
                require_user300=True,
            ),
            'damperCalibrations': (
                len(calibration_profiles) == len(case_results)
                and all(
                    item.get('status') == 'VERIFIED'
                    and len(str(item.get('sha256') or '')) == 64
                    for item in calibration_profiles
                )
            ),
            'inputProvenance': input_provenance_passed(result.get('inputProvenance')),
            'outputManifest': output_manifest_passed(result, artifacts, self.store),
            'resultCatalog': (
                result_catalog_passed(result, artifacts, self.store)
                if result.get('resultCatalogArtifactId')
                else True
            ),
        }
        accepted = all(checks.values())
        return ReviewOutcome(
            accepted=accepted,
            run_status='SUCCEEDED' if accepted else 'COMPLETED_DIAGNOSTIC',
            evidence_mode='REAL_FEM' if accepted else 'DIAGNOSTIC_ONLY',
            checks=checks,
            message=(
                '两个真实 FEM 工况及等最大出力证据均通过，可作为阻尼器对比结论。'
                if accepted
                else '对比 Job 已结束，但至少一个真实求解或证据门槛未通过；结果仅供诊断。'
            ),
            extra={
                'caseResults': case_results,
            },
        )

    def build_report(
        self,
        run: dict[str, Any],
        job: dict[str, Any],
        outcome: ReviewOutcome,
    ) -> dict[str, Any]:
        result = job.get('result') or {}
        report = {
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
            'comparisonBasis': result.get('comparisonBasis'),
            'forceCapScope': result.get('forceCapScope'),
            'caseResults': result.get('caseResults') or [],
            'responseComparison': result.get('responseComparison') or {},
            'checks': outcome.checks,
            'workflowContract': run.get('workflowContract'),
            'solverVersionProfile': result.get('solverVersionProfile'),
            'damperCalibrationProfiles': result.get('damperCalibrationProfiles') or [],
            'inputProvenance': result.get('inputProvenance') or [],
            'outputManifestArtifactId': result.get('outputManifestArtifactId'),
            'resultCatalogArtifactId': result.get('resultCatalogArtifactId'),
            'artifacts': [
                {
                    'artifactId': item['artifactId'],
                    'name': item['name'],
                    'kind': item['kind'],
                    'sha256': item['sha256'],
                }
                for item in job.get('artifacts', [])
            ],
            'limitations': '两个工况使用同一登记荷载、同一布置和等最大出力参数剖面；只有 REAL_FEM 状态可作为最终结论。',
        }
        return report

    def narrative_facts(self, report: dict[str, Any], job: dict[str, Any]) -> dict[str, Any]:
        checks = report.get('checks') or {}
        return {
            'conclusion': report.get('conclusion'),
            'failedChecks': sorted(name for name, passed in checks.items() if not passed),
            'passedCheckCount': sum(1 for passed in checks.values() if passed),
            'totalCheckCount': len(checks),
            'comparisonBasis': report.get('comparisonBasis'),
            'forceCapScope': report.get('forceCapScope'),
            'cases': [
                {
                    'damperType': case.get('damperType'),
                    'status': case.get('status'),
                    'objectives': narrative_safe(case.get('objectives')),
                }
                for case in (report.get('caseResults') or [])
            ],
            'responseComparison': narrative_safe(report.get('responseComparison')),
            'limitations': report.get('limitations'),
            'jobStatus': job.get('status'),
        }
