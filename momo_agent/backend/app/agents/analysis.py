from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.agent_engineering import EngineeringIntent, build_engineering_contract
from app.services.agent_evidence import build_agent_input_provenance, build_solver_version_profile
from app.services.platform_readiness import build_readiness_report
from app.services.platform_store import (
    AGENT_LOAD_TARGET_SETS,
    AGENT_TRAFFIC_FORCE_COMPONENT,
    AGENT_WIND_FORCE_COMPONENT,
    utc_now,
)
from app.agents.engineering import EngineeringAgent, PreparedApproval, ReviewOutcome, narrative_safe
from app.agents.evidence_gates import (
    input_provenance_passed,
    output_manifest_passed,
    result_catalog_passed,
    solver_version_profile_passed,
)

from .core import AgentContext, RepositorySessionMemory
from .figure_contracts import FigureRenderInput, FigureRenderOutput
from .tools import ToolRisk, TypedAgentTool, TypedToolRegistry


ANALYSIS_WORKFLOW_PATHS = {
    'ANSYS': 'docs/examples/templates/ansys_run_earthquake_baseline_template.json',
    'OPENSEESPY_INPROC': (
        'docs/examples/templates/openseespy_inproc_run_earthquake_baseline_template.json'
    ),
}
ANALYSIS_WIND_WORKFLOW_PATHS = {
    'ANSYS': 'docs/examples/templates/ansys_run_wind_baseline_template.json',
    'OPENSEESPY_INPROC': (
        'docs/examples/templates/openseespy_inproc_run_wind_baseline_template.json'
    ),
}
ANALYSIS_TRAFFIC_WORKFLOW_PATHS = {
    'ANSYS': 'docs/examples/templates/ansys_run_traffic_baseline_template.json',
    'OPENSEESPY_INPROC': (
        'docs/examples/templates/openseespy_inproc_run_traffic_baseline_template.json'
    ),
}
ANALYSIS_WORKFLOW_PATHS_BY_LOAD_KIND = {
    'EARTHQUAKE': ANALYSIS_WORKFLOW_PATHS,
    'WIND': ANALYSIS_WIND_WORKFLOW_PATHS,
    'TRAFFIC': ANALYSIS_TRAFFIC_WORKFLOW_PATHS,
}
ANALYSIS_REGISTERED_WORKFLOW_PATHS = frozenset(
    path
    for paths in ANALYSIS_WORKFLOW_PATHS_BY_LOAD_KIND.values()
    for path in paths.values()
)
ANALYSIS_WIND_TARGET_SET_ID = 'STBRIDGE_WIND_DECK_NODES'
ANALYSIS_WIND_FORCE_UNITS = {'N', 'kN'}
ANALYSIS_TRAFFIC_TARGET_SET_ID = 'STBRIDGE_TRAFFIC_DECK_NODES'
ANALYSIS_TRAFFIC_FORCE_UNITS = {'N', 'kN'}
# 荷载类型到登记施加目标集的唯一映射：地震是一致激励没有节点目标集，
# 风与车流都必须显式声明目标集，dispatch 侧按这张表反查强绑定。
ANALYSIS_TARGET_SET_BY_LOAD_KIND = {
    'WIND': ANALYSIS_WIND_TARGET_SET_ID,
    'TRAFFIC': ANALYSIS_TRAFFIC_TARGET_SET_ID,
}
# 节点力工况的审批门参数：风与车流走同一套校验顺序，差别在荷载形状。
# 两者的方向都锁死 UY——风被 pyansys_bridge 固定施加在 +Y；车流虽由模板
# direction (0,-1,0) 声明施加方向，通道声明的分量仍须是 UY，否则冻结的
# 分量与模板方向不在同一轴上。
#
# perNodeChannelTargetSet 决定放行的通道数形态：
#   * 风声明目标集，因此放行单通道总力（执行侧等权分配到各节点）或通道数等于
#     目标节点数的逐节点独立力时程（第 i 通道对应第 i 节点）；
#   * 车流为 None，只放行单通道——它的逐节点信息在矩阵制品的列里，由 mapping
#     制品声明列与节点的对应关系，不靠多通道表达。
ANALYSIS_NODAL_FORCE_LOAD_KINDS = {
    'WIND': {
        'label': '风荷载',
        'solverLabel': 'ANSYS 与 OPENSEESPY_INPROC',
        'units': ANALYSIS_WIND_FORCE_UNITS,
        'component': AGENT_WIND_FORCE_COMPONENT,
        'applicationType': 'NODAL_FORCE',
        'requiresPointMapping': False,
        'perNodeChannelTargetSet': ANALYSIS_WIND_TARGET_SET_ID,
    },
    'TRAFFIC': {
        'label': '车流荷载',
        'solverLabel': 'ANSYS 与 OPENSEESPY_INPROC',
        'units': ANALYSIS_TRAFFIC_FORCE_UNITS,
        'component': AGENT_TRAFFIC_FORCE_COMPONENT,
        # 车流是移动荷载：163 个节点各有独立时程，制品是稠密矩阵而不是单列总力，
        # 所以应用类型与风不同，并且必须同时冻结逐节点 mapping 制品——只有矩阵
        # 没有 mapping 时，求解侧无法知道哪一列对应哪个节点。
        'applicationType': 'NODAL_FORCE_MATRIX',
        'requiresPointMapping': True,
    },
}
ANALYSIS_FROZEN_ACTION_FIELDS = {
    'solver',
    'caseSetId',
    'runMode',
    'workflowConfigPath',
    'loadKind',
    'loadTargetSetId',
    'loadDatasetArtifactId',
    'loadDatasetSha256',
    # 车流的稠密矩阵制品必须配一份逐节点 mapping 制品才可解释（哪一列对应哪个
    # 节点）。两个 SHA 都要冻结：只钉矩阵会让 mapping 换了列序仍复用旧审批。
    'loadPointMappingArtifactId',
    'loadPointMappingSha256',
    'loadMapping',
    'responseIds',
    'modelArtifactId',
    'modelSha256',
    'responseNodes',
    'responseElementIds',
    'responseComponent',
    'resources',
    'solverVersionProfile',
    'inputProvenance',
}
REPO_ROOT = Path(__file__).resolve().parents[4]


class AnalysisPreflightInput(BaseModel):
    solver: Literal['ANSYS', 'OPENSEESPY_INPROC']
    load_kind: Literal['EARTHQUAKE', 'WIND', 'TRAFFIC'] = 'EARTHQUAKE'


class AnalysisPreflightOutput(BaseModel):
    passed: bool
    workflow_path: str
    readiness: dict[str, Any]
    config: dict[str, Any]
    solver_version_profile: dict[str, Any]


class AnalysisDispatchInput(BaseModel):
    model_config = ConfigDict(extra='forbid')

    run_id: str = Field(min_length=1)
    frozen_action: dict[str, Any]

    @model_validator(mode='after')
    def validate_frozen_action(self) -> 'AnalysisDispatchInput':
        path = self.frozen_action.get('workflowConfigPath')
        if path not in ANALYSIS_REGISTERED_WORKFLOW_PATHS:
            raise ValueError('workflowConfigPath 必须是已登记的 ANALYSIS 模板')
        if self.frozen_action.get('runMode') != 'REAL_AGENT_ANALYSIS':
            raise ValueError('runMode 必须是 REAL_AGENT_ANALYSIS')
        load_kind = self.frozen_action.get('loadKind') or 'EARTHQUAKE'
        registered_paths = ANALYSIS_WORKFLOW_PATHS_BY_LOAD_KIND.get(load_kind)
        if registered_paths is None:
            raise ValueError(f'loadKind {load_kind} 不受 ANALYSIS 工具支持')
        solver = self.frozen_action.get('solver')
        if solver not in registered_paths:
            raise ValueError('solver 不受 ANALYSIS 工具支持')
        if path != registered_paths[solver]:
            raise ValueError('solver 与 workflowConfigPath 不匹配')
        target_set_id = self.frozen_action.get('loadTargetSetId')
        expected_target = ANALYSIS_TARGET_SET_BY_LOAD_KIND.get(load_kind)
        if target_set_id != expected_target:
            raise ValueError(f'loadTargetSetId 与 loadKind {load_kind} 不匹配')
        unknown_fields = set(self.frozen_action) - ANALYSIS_FROZEN_ACTION_FIELDS
        if unknown_fields:
            raise ValueError(f'冻结动作包含未登记字段: {sorted(unknown_fields)}')
        return self


class AnalysisDispatchOutput(BaseModel):
    job_id: str = Field(min_length=1)


class AnalysisReviewInput(BaseModel):
    job: dict[str, Any]
    workflow_contract: dict[str, Any]


class AnalysisReviewOutput(BaseModel):
    run_status: Literal['SUCCEEDED', 'COMPLETED_DIAGNOSTIC', 'FAILED', 'CANCELLED']
    evidence_mode: str
    accepted: bool
    checks: dict[str, bool]
    message: str


class AnalysisRuntimeTools:
    """ANALYSIS 对 Agent 开放的受控高层工具。"""

    def __init__(
        self,
        *,
        preflight_handler: Any,
        dispatch_handler: Any,
        review_handler: Any,
        visualize_handler: Any | None = None,
    ) -> None:
        self.registry = TypedToolRegistry()
        self.registry.register(TypedAgentTool(
            name='analysis.prepare',
            description='当 ANALYSIS 已明确求解器、需要在审批前检查登记模板和真实环境时使用；不创建 Job。',
            input_model=AnalysisPreflightInput,
            output_model=AnalysisPreflightOutput,
            risk=ToolRisk.READ_ONLY,
            requires_approval=False,
            handler=preflight_handler,
        ))
        self.registry.register(TypedAgentTool(
            name='analysis.run',
            description='当 ANALYSIS 已通过预检和审批、需要创建唯一真实分析 Job 时使用；不得用于预览。',
            input_model=AnalysisDispatchInput,
            output_model=AnalysisDispatchOutput,
            risk=ToolRisk.MUTATING,
            requires_approval=True,
            handler=dispatch_handler,
        ))
        self.registry.register(TypedAgentTool(
            name='analysis.review',
            description='当真实分析 Job 已终态且结果制品已登记、需要核对证据门槛时使用；不执行求解。',
            input_model=AnalysisReviewInput,
            output_model=AnalysisReviewOutput,
            risk=ToolRisk.READ_ONLY,
            requires_approval=False,
            handler=review_handler,
        ))
        if visualize_handler is not None:
            self.registry.register(TypedAgentTool(
                name='analysis.visualize',
                description='当分析结论已有登记证据、需要按 figure contract 生成可审计图件包时使用；不补造数据。',
                input_model=FigureRenderInput,
                output_model=FigureRenderOutput,
                risk=ToolRisk.ARTIFACT_WRITE,
                requires_approval=False,
                artifact_kinds=('FIGURE_BUNDLE', 'SOURCE_DATA'),
                handler=visualize_handler,
            ))

    def preflight(self, payload: dict[str, Any]) -> AnalysisPreflightOutput:
        return self.registry.execute('analysis.prepare', payload)  # type: ignore[return-value]

    def dispatch(
        self,
        payload: dict[str, Any],
        *,
        approved: bool = False,
        idempotency_key: str | None = None,
    ) -> AnalysisDispatchOutput:
        return self.registry.execute(
            'analysis.run',
            payload,
            approved=approved,
            idempotency_key=idempotency_key,
        )  # type: ignore[return-value]

    def review(self, payload: dict[str, Any]) -> AnalysisReviewOutput:
        return self.registry.execute('analysis.review', payload)  # type: ignore[return-value]

    def visualize(
        self,
        payload: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> Any:
        return self.registry.execute(
            'analysis.visualize',
            payload,
            idempotency_key=idempotency_key,
        )


class EngineeringPlanner(Protocol):
    def plan_engineering(
        self,
        goal: str,
        *,
        requested_task: str,
        has_file: bool,
        attachment_summary: dict[str, Any] | None = None,
    ) -> Any: ...


@dataclass(frozen=True)
class AnalysisPlan:
    planner_mode: str
    intent: EngineeringIntent
    workflow_contract: dict[str, Any]
    recent_message_count: int


class AnalysisAgent(EngineeringAgent):
    """ANALYSIS 的最小规划切片；LLM 不拥有工程执行参数。"""

    task_type = 'ANALYSIS'
    approval_action = 'RUN_SOLVER'
    workflow_paths = ANALYSIS_WORKFLOW_PATHS

    def __init__(
        self,
        *,
        planner: EngineeringPlanner,
        memory: RepositorySessionMemory,
        store: Any | None = None,
        dispatcher: Any | None = None,
        figure_service: Any | None = None,
        preflight_handler: Any | None = None,
        dispatch_handler: Any | None = None,
        review_handler: Any | None = None,
        visualize_handler: Any | None = None,
        readiness_builder: Any | None = None,
        preflight_runner: Any | None = None,
        solver_profile_builder: Any | None = None,
    ) -> None:
        self.planner = planner
        self.memory = memory
        self.store = store
        self.dispatcher = dispatcher
        self.figure_service = figure_service
        self.readiness_builder = readiness_builder or build_readiness_report
        self.preflight_runner = preflight_runner
        self.solver_profile_builder = solver_profile_builder or build_solver_version_profile
        self.tools = AnalysisRuntimeTools(
            preflight_handler=preflight_handler or self._preflight_tool,
            dispatch_handler=dispatch_handler or self._dispatch_tool,
            review_handler=review_handler or self._review_tool,
            visualize_handler=visualize_handler or self._visualize_tool,
        )

    def plan(self, context: AgentContext) -> AnalysisPlan:
        recent_messages = self.memory.load(context.session_id)
        planner_result = self.planner.plan_engineering(
            context.goal,
            requested_task=context.requested_task,
            has_file=context.has_attachment,
            attachment_summary=context.attachment_summary,
        )
        intent = planner_result.intent
        selected_layout_id = getattr(intent, 'selected_layout_id', None)
        load_kind = getattr(intent, 'load_kind', None)
        response_ids = list(getattr(intent, 'response_ids', []) or [])
        if intent.task_type not in {'ANALYSIS', 'CLARIFICATION'}:
            raise ValueError(f'ANALYSIS 规划器返回了不支持的任务类型: {intent.task_type}')
        contract = (
            {}
            if intent.missing_fields
            else build_engineering_contract(
                task_type='ANALYSIS',
                solver=intent.solver,
                damper_type=intent.damper_type,
                response_ids=response_ids,
                selected_layout_id=selected_layout_id if intent.damper_type else None,
                load_kind=load_kind or 'EARTHQUAKE',
                model_artifact_id=getattr(intent, 'model_artifact_id', None),
                response_nodes=list(getattr(intent, 'response_nodes', []) or []),
                response_element_ids=list(getattr(intent, 'response_element_ids', []) or []),
                response_direction=getattr(intent, 'response_direction', None),
                field_sources={
                    'solver': 'USER_SPECIFIED',
                    'loadKind': 'USER_SPECIFIED' if load_kind else 'DEFAULT',
                    'responseIds': 'USER_SPECIFIED' if response_ids else 'DEFAULT',
                    'budget': 'DEFAULT',
                    **({'selectedLayoutId': 'USER_SPECIFIED' if selected_layout_id else 'DEFAULT'} if intent.damper_type else {}),
                },
            )
        )
        return AnalysisPlan(
            planner_mode=planner_result.planner_mode,
            intent=intent,
            workflow_contract=contract,
            recent_message_count=len(recent_messages),
        )

    def prepare_approval(
        self,
        run: dict[str, Any],
        *,
        mapping: dict[str, Any],
        standard_artifact_id: str | None,
        standard_sha256: str | None,
    ) -> PreparedApproval:
        """只计算 ANALYSIS 的预检、契约和审批冻结动作。"""
        contract = dict(run.get('workflowContract') or {})
        solver = str(contract.get('solver') or 'ANSYS')
        load_kind = str(mapping.get('loadKind') or (run.get('intent') or {}).get('loadKind') or '')
        channels = list(mapping.get('channels') or [])
        contract_updates = {
            'loadArtifactId': standard_artifact_id,
            'loadSha256': standard_sha256,
            'loadKind': load_kind,
        }
        load_gate = self._load_kind_gate(
            load_kind,
            solver=solver,
            channels=channels,
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
                contract_updates=contract_updates,
            )
        model_artifact_id = contract.get('modelArtifactId')
        model_sha256: str | None = None
        if model_artifact_id:
            if solver != 'ANSYS':
                return PreparedApproval(
                    passed=False,
                    failure_status='UNSUPPORTED',
                    failure_message='用户上传的 FEM 模型首期仅支持 ANSYS 求解器；未生成执行审批。',
                    preflight={'passed': False, 'reason': 'CUSTOM_MODEL_SOLVER_UNSUPPORTED'},
                    contract_updates={'loadArtifactId': standard_artifact_id, 'loadSha256': standard_sha256, 'loadKind': load_kind},
                )
            model_record = self._fem_model_record(str(model_artifact_id))
            if model_record is None:
                return PreparedApproval(
                    passed=False,
                    failure_status='FAILED',
                    failure_message=f'模型制品 {model_artifact_id} 不存在或不是已登记的 FEM 模型；未生成执行审批。',
                    preflight={'passed': False, 'reason': 'FEM_MODEL_NOT_REGISTERED'},
                    contract_updates={'loadArtifactId': standard_artifact_id, 'loadSha256': standard_sha256, 'loadKind': load_kind},
                )
            model_sha256 = model_record
        preflight = self.tools.preflight({'solver': solver, 'load_kind': load_kind})
        preflight_payload = {
            'passed': preflight.passed,
            'readiness': preflight.readiness,
            'config': preflight.config,
            'solverVersionProfile': preflight.solver_version_profile,
        }
        if model_sha256:
            contract_updates['modelSha256'] = model_sha256
        if not preflight.passed:
            return PreparedApproval(
                passed=False,
                preflight=preflight_payload,
                contract_updates=contract_updates,
                failure_status='FAILED',
                failure_message='真实分析环境或登记配置预检未通过，未生成执行审批和 Job。',
            )
        frozen_action = {
            'solver': solver,
            'caseSetId': run['runId'],
            'runMode': 'REAL_AGENT_ANALYSIS',
            'workflowConfigPath': preflight.workflow_path,
            'loadKind': load_kind,
            'loadDatasetArtifactId': standard_artifact_id,
            'loadDatasetSha256': standard_sha256,
            'loadMapping': mapping,
            'responseIds': contract.get('responseIds') or [],
            'resources': {'processCount': 1, 'coresPerProcess': 1, 'executionTimeoutS': 7200},
            'solverVersionProfile': preflight.solver_version_profile,
        }
        expected_target_set_id = ANALYSIS_TARGET_SET_BY_LOAD_KIND.get(load_kind)
        if expected_target_set_id:
            frozen_action['loadTargetSetId'] = expected_target_set_id
            contract_updates['loadTargetSetId'] = expected_target_set_id
        # 矩阵制品的工况（车流）要把逐节点 mapping 制品一起冻结，执行侧才能按列取数。
        # 门已在 _load_kind_gate 校验过两个字段都存在，这里只做搬运。
        if (ANALYSIS_NODAL_FORCE_LOAD_KINDS.get(load_kind) or {}).get('requiresPointMapping'):
            frozen_action['loadPointMappingArtifactId'] = mapping['pointMappingArtifactId']
            frozen_action['loadPointMappingSha256'] = mapping['pointMappingSha256']
            contract_updates['loadPointMappingArtifactId'] = mapping['pointMappingArtifactId']
            contract_updates['loadPointMappingSha256'] = mapping['pointMappingSha256']
        if model_artifact_id:
            frozen_action['modelArtifactId'] = str(model_artifact_id)
            frozen_action['modelSha256'] = model_sha256
        response_nodes = [int(node) for node in contract.get('responseNodes') or []]
        response_elements = [int(element) for element in contract.get('responseElementIds') or []]
        if response_nodes or response_elements:
            frozen_action['responseNodes'] = response_nodes
            frozen_action['responseElementIds'] = response_elements
            frozen_action['responseComponent'] = int(contract.get('responseComponent') or 0)
        frozen_action['inputProvenance'] = build_agent_input_provenance(
            frozen_action,
            task_type='ANALYSIS',
        )
        return PreparedApproval(
            passed=True,
            frozen_action=frozen_action,
            approval_action=self.approval_action,
            approval_summary=f'使用 {solver} 和审批冻结的标准荷载创建真实单次分析 Job。',
            preflight=preflight_payload,
            contract_updates=contract_updates,
            extra_run_fields={
                'solverVersionProfile': preflight.solver_version_profile,
                'inputProvenance': frozen_action['inputProvenance'],
            },
        )

    @staticmethod
    def _load_kind_gate(
        load_kind: str,
        *,
        solver: str,
        channels: list[dict[str, Any]],
        standard_artifact_id: str | None,
        standard_sha256: str | None,
        mapping: dict[str, Any] | None = None,
    ) -> tuple[str, str] | None:
        """返回 (reason, message) 表示该荷载类型未放行；None 表示可以继续预检。

        `mapping` 是完整的荷载映射契约。矩阵型工况（车流）的逐节点 mapping 制品
        ID/SHA 挂在它的顶层，不在 channels 里；调用方不传时按缺失处理，即失败关闭。
        """
        if load_kind == 'EARTHQUAKE':
            if channels and len(channels) != 1:
                return (
                    'PRODUCTION_GATE',
                    '真实分析首期仅放行单通道地震一致激励；未生成执行审批。',
                )
            return None
        if load_kind not in ANALYSIS_NODAL_FORCE_LOAD_KINDS:
            return (
                'PRODUCTION_GATE',
                '真实分析首期仅放行单通道地震一致激励；未生成执行审批。',
            )
        gate = ANALYSIS_NODAL_FORCE_LOAD_KINDS[load_kind]
        label = gate['label']
        if solver not in ANALYSIS_WORKFLOW_PATHS_BY_LOAD_KIND[load_kind]:
            return (
                f'{load_kind}_SOLVER_UNSUPPORTED',
                f'真实{label}分析仅放行 {gate["solverLabel"]} 求解器；未生成执行审批。',
            )
        if not standard_artifact_id or not standard_sha256:
            return (
                f'{load_kind}_LOAD_ARTIFACT_REQUIRED',
                f'{label}分析必须引用已登记并标准化的荷载制品；未生成执行审批。',
            )
        # 放行的通道数形态由 perNodeChannelTargetSet 声明：声明了目标集的工况
        # （风）放行单通道＝目标集总力（执行侧按等权分配到各节点），或通道数等于
        # 目标节点数＝逐节点独立力时程（第 i 个通道对应第 i 个节点）；其余通道数
        # 无法判定通道与节点的对应关系，执行侧的 `_apply_agent_standard_wind_load`
        # 用同一口径失败关闭。未声明目标集的工况（车流）只放行单通道：它的逐节点
        # 信息在矩阵制品的列里，由 mapping 制品声明列与节点的对应关系。
        per_node_target_set = gate.get('perNodeChannelTargetSet')
        allowed_channel_counts = {1}
        node_count = 0
        if per_node_target_set:
            node_count = len(AGENT_LOAD_TARGET_SETS.get(per_node_target_set) or ())
            allowed_channel_counts.add(node_count)
        if len(channels) not in allowed_channel_counts:
            return (
                f'{load_kind}_LOAD_MAPPING_UNSUPPORTED',
                (
                    f'{label}分析放行单通道总力或与目标节点数 {node_count} 相等的'
                    '逐节点通道；未生成执行审批。'
                    if per_node_target_set
                    else f'{label}分析首期仅放行单通道节点力时程；未生成执行审批。'
                ),
            )
        expected_component = gate['component']
        expected_application = gate['applicationType']
        # 逐节点形态下每一条通道都要单独校验：只查首通道会让后续通道的单位或
        # 方向偏差整列漏过，量纲上看不出错。
        if any(
            channel.get('applicationType') != expected_application
            or channel.get('quantity') != 'FORCE'
            or channel.get('sourceUnit') not in gate['units']
            or (expected_component is not None and channel.get('component') != expected_component)
            for channel in channels
        ):
            component_note = f'、{expected_component} 方向' if expected_component else ''
            return (
                f'{load_kind}_LOAD_MAPPING_UNSUPPORTED',
                f'{label}通道必须是 N 或 kN{component_note}的 {expected_application} 力时程；未生成执行审批。',
            )
        target_set_id = ANALYSIS_TARGET_SET_BY_LOAD_KIND[load_kind]
        if any(
            channel.get('targetType') != 'NODE_GROUP'
            or channel.get('targetId') != target_set_id
            for channel in channels
        ):
            return (
                f'{load_kind}_LOAD_TARGET_UNSUPPORTED',
                f'{label}必须显式作用于登记目标集 {target_set_id}；未生成执行审批。',
            )
        point_mapping = mapping or {}
        if gate['requiresPointMapping'] and not (
            point_mapping.get('pointMappingArtifactId') and point_mapping.get('pointMappingSha256')
        ):
            # 矩阵制品单独存在是不可解释的：没有 mapping 就不知道哪一列对应哪个
            # 节点，求解侧只能猜列序。缺 mapping 必须失败关闭，不允许按列号顺序假定。
            return (
                f'{load_kind}_LOAD_POINT_MAPPING_REQUIRED',
                f'{label}必须同时引用已登记的逐节点 mapping 制品；未生成执行审批。',
            )
        return None

    def _fem_model_record(self, artifact_id: str) -> str | None:
        """返回已登记 FEM 模型制品的 SHA256；不存在或类型不符时返回 None。"""
        if self.store is None:
            return None
        try:
            record = self.store.get_artifact(artifact_id)
        except Exception:
            return None
        if getattr(record.artifact, 'kind', None) != 'FEM_MODEL':
            return None
        return str(record.artifact.sha256)

    def review(
        self,
        job: dict[str, Any],
        *,
        workflow_contract: dict[str, Any] | None = None,
    ) -> ReviewOutcome:
        result = self.tools.review({
            'job': job,
            'workflow_contract': workflow_contract or {},
        })
        return ReviewOutcome(
            accepted=result.accepted,
            run_status=result.run_status,
            evidence_mode=result.evidence_mode,
            checks=result.checks,
            message=result.message,
        )

    def build_report(
        self,
        run: dict[str, Any],
        job: dict[str, Any],
        outcome: ReviewOutcome,
    ) -> dict[str, Any]:
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
            'checks': outcome.checks,
            'workflowContract': run.get('workflowContract'),
            'solverVersionProfile': result.get('solverVersionProfile'),
            'inputProvenance': result.get('inputProvenance') or [],
            'outputManifestArtifactId': result.get('outputManifestArtifactId'),
            'resultCatalogArtifactId': result.get('resultCatalogArtifactId'),
            'artifacts': [
                {
                    'artifactId': item['artifactId'],
                    'name': item['name'],
                    'sha256': item['sha256'],
                }
                for item in job.get('artifacts', [])
            ],
        }

    def narrative_facts(self, report: dict[str, Any], job: dict[str, Any]) -> dict[str, Any]:
        result = job.get('result') or {}
        contract = report.get('workflowContract') or {}
        checks = report.get('checks') or {}
        return {
            'conclusion': report.get('conclusion'),
            'failedChecks': sorted(name for name, passed in checks.items() if not passed),
            'passedCheckCount': sum(1 for passed in checks.values() if passed),
            'totalCheckCount': len(checks),
            'solver': contract.get('solver'),
            'responseIds': contract.get('responseIds'),
            'objectives': narrative_safe(result.get('objectives')),
            'runMode': report.get('runMode'),
            'isVerifiedSolverOutput': result.get('isVerifiedSolverOutput'),
        }

    def _preflight_tool(self, request: AnalysisPreflightInput) -> dict[str, Any]:
        if self.store is None or self.dispatcher is None:
            raise RuntimeError('AnalysisAgent 需要注入 store 和 dispatcher 才能执行预检')
        from pyansys_bridge.optimization.config_runner import preflight_config
        solver = request.solver
        registered_paths = ANALYSIS_WORKFLOW_PATHS_BY_LOAD_KIND[request.load_kind]
        if solver not in registered_paths:
            return {
                'passed': False,
                'workflow_path': '',
                'readiness': {'status': 'UNSUPPORTED'},
                'config': {
                    'error': 'UNREGISTERED_ANALYSIS_TEMPLATE',
                    'message': f'{request.load_kind} 分析没有登记 {solver} 模板',
                },
                'solver_version_profile': {},
            }
        template_path = registered_paths[solver]
        readiness = self.readiness_builder(
            self.store,
            self.dispatcher,
            require_platform_ui=False,
            require_ansys=solver == 'ANSYS',
            require_openseespy=solver == 'OPENSEESPY_INPROC',
        )
        try:
            config_preflight = (self.preflight_runner or preflight_config)(REPO_ROOT / template_path)
            solver_version_profile = self.solver_profile_builder(
                REPO_ROOT / template_path,
                solver=solver,
            )
        except Exception as exc:
            config_preflight = {'error': type(exc).__name__, 'message': str(exc)}
            solver_version_profile = {'error': type(exc).__name__, 'message': str(exc)}
        expected_solver = 'ansys' if solver == 'ANSYS' else 'openseespy_inproc'
        expected_load_type = request.load_kind.lower()
        checks = list((config_preflight.get('path_checks') or {}).values())
        passed = (
            readiness.get('status') == 'READY'
            and config_preflight.get('kind') == 'undamped_baseline'
            and config_preflight.get('solver') == expected_solver
            and config_preflight.get('load_type', expected_load_type) == expected_load_type
            and config_preflight.get('execution_mode') == 'run'
            and bool(checks)
            and all(check.get('exists') is True for check in checks)
            and solver_version_profile_passed(solver_version_profile, require_user300=False)
        )
        return {
            'passed': passed,
            'workflow_path': template_path,
            'readiness': readiness,
            'config': config_preflight,
            'solver_version_profile': solver_version_profile,
        }

    def _dispatch_tool(self, request: AnalysisDispatchInput) -> dict[str, str]:
        if self.store is None:
            raise RuntimeError('AnalysisAgent 需要注入 store 才能创建 Job')
        job = self.store.create_job(
            'SOLVER_BATCH',
            {**request.frozen_action, 'agentRunId': request.run_id},
        )
        return {'job_id': job.job_id}

    def _review_tool(self, request: AnalysisReviewInput) -> dict[str, Any]:
        job = request.job
        result = job.get('result') or {}
        contract = request.workflow_contract
        artifacts = job.get('artifacts') or []
        artifact_names = {item.get('name') for item in artifacts if item.get('sha256')}
        load_evidence = result.get('customLoadEvidence') or {}
        model_evidence = result.get('customModelEvidence') or {}
        checks = {
            'jobSucceeded': job.get('status') == 'SUCCEEDED',
            'realRunMode': result.get('mode') == 'real_agent_analysis',
            'verifiedSolverOutput': result.get('isVerifiedSolverOutput') is True,
            'approvedLoadArtifact': (
                (
                    load_evidence.get('artifactId') == contract.get('loadArtifactId')
                    and load_evidence.get('sha256') == contract.get('loadSha256')
                )
                if contract.get('loadArtifactId')
                else not load_evidence
            ),
            'approvedLoadTargetSet': (
                load_evidence.get('targetSetId') == contract.get('loadTargetSetId')
                if contract.get('loadTargetSetId')
                else not load_evidence.get('targetSetId')
            ),
            'approvedModelArtifact': (
                (
                    model_evidence.get('artifactId') == contract.get('modelArtifactId')
                    and model_evidence.get('sha256') == contract.get('modelSha256')
                )
                if contract.get('modelArtifactId')
                else not model_evidence
            ),
            'requiredArtifacts': {
                'real_analysis_summary.json',
                'real_analysis_overview.json',
                'real_output_manifest.json',
            } <= artifact_names,
            'artifactHashes': all(len(str(item.get('sha256') or '')) == 64 for item in artifacts),
            'solverVersionProfile': solver_version_profile_passed(
                result.get('solverVersionProfile') or {},
                require_user300=False,
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
        if job.get('status') in {'FAILED', 'CANCELLED'}:
            status = job['status']
            evidence_mode = job['status']
        else:
            status = 'SUCCEEDED' if accepted else 'COMPLETED_DIAGNOSTIC'
            evidence_mode = 'REAL_FEM' if accepted else 'DIAGNOSTIC_ONLY'
        return {
            'accepted': accepted,
            'run_status': status,
            'evidence_mode': evidence_mode,
            'checks': checks,
            'message': (
                '真实 FEM 分析及输入证据校验通过。'
                if accepted
                else '分析 Job 已结束，但真实求解或输入证据门槛未全部通过。'
            ),
        }

    def _visualize_tool(self, request: Any) -> Any:
        if self.figure_service is None:
            raise RuntimeError('AnalysisAgent 未注入 figure_service')
        return self.figure_service.render(request)
