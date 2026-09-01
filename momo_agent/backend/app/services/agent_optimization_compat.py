"""FULL_OPTIMIZATION -> DAMPER_OPTIMIZATION + FULL 的过渡兼容层。

PR2 只改变“新建运行”的规范形态：旧数据库中的 FULL_OPTIMIZATION run、旧
workflow snapshot 和旧证据仍按原类型读取/恢复，不做历史数据重写。这个模块在
API router 初始化后安装几个窄包装器，等 PR3 合并执行分支后可整体删除。
"""
from __future__ import annotations

import json
from contextvars import ContextVar
from typing import Any, Callable


LEGACY_FULL_TASK = 'FULL_OPTIMIZATION'
CANONICAL_OPTIMIZATION_TASK = 'DAMPER_OPTIMIZATION'
LEGACY_FULL_RESPONSE_IDS = [
    'max_girder_end_displacement',
    'max_tower_base_shear',
    'max_tower_base_moment',
    'cumulative_displacement',
]

_IN_MESSAGE_DISPATCH: ContextVar[bool] = ContextVar(
    'momo_in_message_dispatch', default=False,
)
_LEGACY_FULL_REQUEST: ContextVar[bool] = ContextVar(
    'momo_legacy_full_request', default=False,
)
_NORMALIZE_NEW_WORKFLOW_START: ContextVar[bool] = ContextVar(
    'momo_normalize_new_workflow_start', default=False,
)
_INSTALLED = False


def legacy_full_request_active() -> bool:
    return bool(_LEGACY_FULL_REQUEST.get())


def _legacy_full_engineering_intent(
    summary: str = '按完整优化 Profile 执行阻尼器参数优化。',
    *,
    solver: str = 'ANSYS',
    load_kind: str = 'EARTHQUAKE',
    requires_real_fem: bool = True,
) -> Any:
    """把旧的一键 FULL 预设表示成新的 canonical 工程意图。

    旧别名的 ANSYS/地震等字段只是兼容默认值；FULL Profile 本身不绑定求解器或
    工况。显式 DAMPER_OPTIMIZATION 始终按用户工程配置独立选择这些字段。
    """
    from app.services.agent_engineering import EngineeringIntent

    return EngineeringIntent.model_validate({
        'taskType': CANONICAL_OPTIMIZATION_TASK,
        'solver': solver,
        'damperType': 'VISCOUS',
        'damperTypes': [],
        'loadKind': load_kind,
        'selectedLayoutId': 'TWO_PER_TOWER',
        'responseIds': list(LEGACY_FULL_RESPONSE_IDS),
        'cases': [],
        'maxConcurrentCases': 4,
        'modelArtifactId': None,
        'responseNodes': [],
        'responseElementIds': [],
        'responseDirection': None,
        'budgetProfile': 'STANDARD',
        'optimizationProfile': 'FULL',
        'requiresRealFem': requires_real_fem,
        'missingFields': [],
        'summary': summary,
    })


def normalize_legacy_full_workflow_start(payload: Any) -> Any:
    """把旧 workflow.start FULL 参数转换为 canonical 工程意图。"""
    if not isinstance(payload, dict) or payload.get('taskType') != LEGACY_FULL_TASK:
        return payload
    full = payload.get('fullOptimizationIntent')
    full = full if isinstance(full, dict) else {}
    intent = _legacy_full_engineering_intent(
        str(full.get('summary') or '按完整优化 Profile 执行阻尼器参数优化。'),
        solver=str(full.get('solver') or 'ANSYS'),
        load_kind=str(full.get('scenario') or 'EARTHQUAKE'),
        requires_real_fem=bool(full.get('requiresRealFem', True)),
    )
    return {
        'taskType': CANONICAL_OPTIMIZATION_TASK,
        'engineeringIntent': intent.model_dump(by_alias=True),
    }


def _apply_legacy_defaults(intent: Any) -> Any:
    """旧 FULL 别名仅对缺失字段补旧预设默认值，不覆盖用户明确值。"""
    if intent is None:
        return intent
    if getattr(intent, 'solver', None) is None:
        intent.solver = 'ANSYS'
    if getattr(intent, 'damper_type', None) is None:
        intent.damper_type = 'VISCOUS'
    if getattr(intent, 'load_kind', None) is None:
        intent.load_kind = 'EARTHQUAKE'
    if getattr(intent, 'selected_layout_id', None) is None:
        intent.selected_layout_id = 'TWO_PER_TOWER'
    if not list(getattr(intent, 'response_ids', []) or []):
        intent.response_ids = list(LEGACY_FULL_RESPONSE_IDS)
    intent.optimization_profile = 'FULL'

    removable = {
        'solver', 'damperType', 'damper_type', 'loadKind', 'load_kind',
        'selectedLayoutId', 'selected_layout_id', 'responseIds', 'response_ids',
        'optimizationProfile', 'optimization_profile',
    }
    missing = [
        item for item in list(getattr(intent, 'missing_fields', []) or [])
        if str(item) not in removable
    ]
    intent.missing_fields = missing
    if not missing:
        intent.task_type = CANONICAL_OPTIMIZATION_TASK
    return intent


def _profile_for_intent(intent: Any) -> str:
    if legacy_full_request_active():
        _apply_legacy_defaults(intent)
        return 'FULL'
    return str(getattr(intent, 'optimization_profile', None) or 'STANDARD').upper()


def _build_optimization_contract(
    intent: Any,
    *,
    field_sources: dict[str, str] | None = None,
    load_artifact_id: str | None = None,
    load_sha256: str | None = None,
) -> dict[str, Any]:
    """兼容旧的简化 intent，同时冻结 Profile 与解析后的 Policy。"""
    from app.services.agent_engineering import build_engineering_contract

    profile = _profile_for_intent(intent)
    try:
        intent.optimization_profile = profile
    except Exception:
        pass
    sources = dict(field_sources or {})
    sources.setdefault(
        'optimizationProfile',
        'LEGACY_ALIAS' if legacy_full_request_active() else (
            'USER_SPECIFIED' if profile != 'STANDARD' else 'DEFAULT'
        ),
    )
    response_ids = list(getattr(intent, 'response_ids', []) or [])
    contract = build_engineering_contract(
        task_type=CANONICAL_OPTIMIZATION_TASK,
        solver=getattr(intent, 'solver', None),
        damper_type=getattr(intent, 'damper_type', None),
        response_ids=response_ids,
        selected_layout_id=(
            getattr(intent, 'selected_layout_id', None) or 'TWO_PER_TOWER'
        ),
        load_kind=getattr(intent, 'load_kind', None) or 'EARTHQUAKE',
        optimization_profile=profile,
        field_sources=sources or None,
        load_artifact_id=load_artifact_id,
        load_sha256=load_sha256,
    )
    if legacy_full_request_active():
        contract['legacyTaskType'] = LEGACY_FULL_TASK
    return contract


def _install_engineering_intent_parser() -> None:
    """让 PR1 新增的 optimizationProfile 真正穿过 LLM JSON 解析层。"""
    from app.services import agent_llm
    from app.services.agent_engineering import EngineeringIntent

    original = agent_llm._parse_engineering_intent
    if getattr(original, '_momo_full_profile_compat', False):
        return

    def parse(content: str) -> EngineeringIntent:
        intent = original(content)
        raw = json.loads(agent_llm._strip_think(content))
        if isinstance(raw, dict) and 'optimizationProfile' in raw:
            payload = intent.model_dump(by_alias=True)
            payload['optimizationProfile'] = raw['optimizationProfile']
            intent = EngineeringIntent.model_validate(payload)
        if legacy_full_request_active():
            _apply_legacy_defaults(intent)
        return intent

    parse._momo_full_profile_compat = True  # type: ignore[attr-defined]
    agent_llm._parse_engineering_intent = parse


def _install_route_normalizer() -> None:
    """LEGACY runtime 的 AUTO 路由若仍返回 FULL，改写为 canonical task。"""
    from app.services.agent_llm import OpenAICompatiblePlanner

    original = OpenAICompatiblePlanner.classify_task
    if getattr(original, '_momo_full_profile_compat', False):
        return

    def classify(self: Any, *args: Any, **kwargs: Any) -> Any:
        result = original(self, *args, **kwargs)
        route = getattr(result, 'route', None)
        if route is not None and getattr(route, 'task_type', None) == LEGACY_FULL_TASK:
            route.task_type = CANONICAL_OPTIMIZATION_TASK
            route.reason = (
                f'{route.reason}；旧 FULL_OPTIMIZATION 已规范化为 '
                'DAMPER_OPTIMIZATION + optimizationProfile=FULL。'
            )[:300]
            if _IN_MESSAGE_DISPATCH.get():
                _LEGACY_FULL_REQUEST.set(True)
        return result

    classify._momo_full_profile_compat = True  # type: ignore[attr-defined]
    OpenAICompatiblePlanner.classify_task = classify


def _install_optimization_plan_normalizer() -> None:
    """统一 DAMPER_OPTIMIZATION 计划中的 Profile 传播并补旧别名默认值。"""
    from app.agents.damper_optimization import DamperOptimizationAgent, OptimizationPlan

    original = DamperOptimizationAgent.plan
    if getattr(original, '_momo_full_profile_compat', False):
        return

    def plan(self: Any, context: Any) -> Any:
        # 历史 FULL run 仍允许旧 Agent 分支恢复；这里只处理 canonical 新任务。
        result = original(self, context)
        if context.requested_task != CANONICAL_OPTIMIZATION_TASK:
            return result
        intent = result.intent
        if legacy_full_request_active():
            _apply_legacy_defaults(intent)
        if getattr(intent, 'task_type', None) == 'CLARIFICATION' and getattr(intent, 'missing_fields', None):
            return result
        if getattr(intent, 'missing_fields', None):
            return result
        contract = _build_optimization_contract(
            intent,
            field_sources=dict((result.workflow_contract or {}).get('fieldSources') or {}),
            load_artifact_id=(result.workflow_contract or {}).get('loadArtifactId'),
            load_sha256=(result.workflow_contract or {}).get('loadSha256'),
        )
        return OptimizationPlan(
            planner_mode=result.planner_mode,
            intent=intent,
            workflow_contract=contract,
            plan=list(result.plan),
        )

    plan._momo_full_profile_compat = True  # type: ignore[attr-defined]
    DamperOptimizationAgent.plan = plan


def _install_task_handler_profile_propagation() -> None:
    """Harness intent_override 路径不经过 Agent.plan，因此 handler 也必须带 Profile。"""
    from app.services.agent_task_handlers import DamperOptimizationTaskHandler
    from app.services.agent_engineering import build_engineering_contract

    original_build = DamperOptimizationTaskHandler.build_contract_from_intent
    if getattr(original_build, '_momo_full_profile_compat', False):
        return

    def build_contract_from_intent(
        self: Any,
        intent: Any,
        *,
        load_import: dict[str, Any] | None,
        field_sources: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        if self.task_type != CANONICAL_OPTIMIZATION_TASK:
            return original_build(
                self,
                intent,
                load_import=load_import,
                field_sources=field_sources,
            )
        if legacy_full_request_active():
            _apply_legacy_defaults(intent)
        return _build_optimization_contract(
            intent,
            field_sources=field_sources,
            load_artifact_id=load_import.get('fileArtifactId') if load_import else None,
        )

    original_rebuild = DamperOptimizationTaskHandler.rebuild_contract

    def rebuild_contract(self: Any, update: Any) -> dict[str, Any]:
        if self.task_type != CANONICAL_OPTIMIZATION_TASK:
            return original_rebuild(self, update)
        profile = str(
            update.previous_contract.get('optimizationProfile')
            or update.frozen.get('optimizationProfile')
            or 'STANDARD'
        ).upper()
        contract = build_engineering_contract(
            task_type=CANONICAL_OPTIMIZATION_TASK,
            solver=update.solver,
            damper_type=update.damper_type,
            response_ids=list(update.response_ids),
            selected_layout_id=(
                update.changes.get('selectedLayoutId')
                or update.frozen.get('selectedLayoutId')
                or update.previous_contract.get('selectedLayoutId')
                or 'TWO_PER_TOWER'
            ),
            load_kind=(
                update.frozen.get('loadKind')
                or update.previous_contract.get('loadKind')
                or 'EARTHQUAKE'
            ),
            optimization_profile=profile,
            load_artifact_id=update.load_artifact_id,
            load_sha256=update.load_sha256,
        )
        if update.previous_contract.get('legacyTaskType') == LEGACY_FULL_TASK:
            contract['legacyTaskType'] = LEGACY_FULL_TASK
        return contract

    original_apply = DamperOptimizationTaskHandler.apply_intent_updates

    def apply_intent_updates(
        self: Any,
        intent: dict[str, Any],
        update: Any,
        *,
        contract: dict[str, Any],
    ) -> None:
        original_apply(self, intent, update, contract=contract)
        if self.task_type == CANONICAL_OPTIMIZATION_TASK:
            intent['optimizationProfile'] = contract.get('optimizationProfile', 'STANDARD')

    build_contract_from_intent._momo_full_profile_compat = True  # type: ignore[attr-defined]
    rebuild_contract._momo_full_profile_compat = True  # type: ignore[attr-defined]
    apply_intent_updates._momo_full_profile_compat = True  # type: ignore[attr-defined]
    DamperOptimizationTaskHandler.build_contract_from_intent = build_contract_from_intent
    DamperOptimizationTaskHandler.rebuild_contract = rebuild_contract
    DamperOptimizationTaskHandler.apply_intent_updates = apply_intent_updates


def _install_conversation_entry_normalizer() -> None:
    """显式 FULL 是固定兼容预设：无需 LLM，直接创建 canonical 工程运行。"""
    from app.services.agent_conversation import AgentConversationMixin

    original = AgentConversationMixin._dispatch_message
    if getattr(original, '_momo_full_profile_compat', False):
        return

    def dispatch(
        self: Any,
        repository: Any,
        session: dict[str, Any],
        content: str,
        now: str,
        load_import: dict[str, Any] | None,
        task_type: str,
        *,
        event_sink: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        dispatch_token = _IN_MESSAGE_DISPATCH.set(True)
        legacy_token = _LEGACY_FULL_REQUEST.set(task_type == LEGACY_FULL_TASK)
        try:
            if task_type == LEGACY_FULL_TASK:
                intent = _legacy_full_engineering_intent(
                    summary=(content.strip() or '按完整优化 Profile 执行阻尼器参数优化。'),
                )
                result = self._create_engineering_run(
                    repository,
                    session,
                    content,
                    now,
                    requested_task=CANONICAL_OPTIMIZATION_TASK,
                    load_import=load_import,
                    route_evidence={
                        'plannerMode': 'COMPAT_ALIAS',
                        'requestedTask': LEGACY_FULL_TASK,
                        'resolvedTask': CANONICAL_OPTIMIZATION_TASK,
                        'optimizationProfile': 'FULL',
                        'reason': (
                            '旧 FULL_OPTIMIZATION 输入别名已规范化为 '
                            'DAMPER_OPTIMIZATION + FULL Profile。'
                        ),
                    },
                    intent_override=intent,
                )
                stored = repository.get_run(result.get('runId'))
                if stored is not None:
                    stored['plannerMode'] = 'COMPAT_ALIAS'
                    repository.save_run(stored)
                    return self._decorate_run(stored)
                result['plannerMode'] = 'COMPAT_ALIAS'
                return result
            return original(
                self,
                repository,
                session,
                content,
                now,
                load_import,
                task_type,
                event_sink=event_sink,
            )
        finally:
            _LEGACY_FULL_REQUEST.reset(legacy_token)
            _IN_MESSAGE_DISPATCH.reset(dispatch_token)

    dispatch._momo_full_profile_compat = True  # type: ignore[attr-defined]
    AgentConversationMixin._dispatch_message = dispatch


def _install_harness_new_start_normalizer() -> None:
    """只规范化“新 workflow.start”；历史 FULL clarification/recovery 不改写。"""
    from app.services.agent_harness import WorkflowHarnessMixin, WorkflowStartInput

    original_validate = WorkflowStartInput.model_validate
    if not getattr(original_validate, '_momo_full_profile_compat', False):
        def model_validate(cls: Any, obj: Any, *args: Any, **kwargs: Any) -> Any:
            normalized = obj
            if _NORMALIZE_NEW_WORKFLOW_START.get():
                normalized = normalize_legacy_full_workflow_start(obj)
                if normalized is not obj and _IN_MESSAGE_DISPATCH.get():
                    _LEGACY_FULL_REQUEST.set(True)
            return original_validate(normalized, *args, **kwargs)

        model_validate._momo_full_profile_compat = True  # type: ignore[attr-defined]
        WorkflowStartInput.model_validate = classmethod(model_validate)

    original_dispatch = WorkflowHarnessMixin._dispatch_harness_message
    if getattr(original_dispatch, '_momo_full_profile_compat', False):
        return

    def dispatch_harness(
        self: Any,
        repository: Any,
        session: dict[str, Any],
        content: str,
        now: str,
        load_import: dict[str, Any] | None,
        inquirable_run: dict[str, Any] | None,
        requested_task: str | None = None,
        event_sink: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        normalize_token = _NORMALIZE_NEW_WORKFLOW_START.set(True)
        legacy_token = None
        canonical_requested = requested_task
        if requested_task == LEGACY_FULL_TASK:
            legacy_token = _LEGACY_FULL_REQUEST.set(True)
            canonical_requested = CANONICAL_OPTIMIZATION_TASK
        try:
            return original_dispatch(
                self,
                repository,
                session,
                content,
                now,
                load_import,
                inquirable_run,
                requested_task=canonical_requested,
                event_sink=event_sink,
            )
        finally:
            if legacy_token is not None:
                _LEGACY_FULL_REQUEST.reset(legacy_token)
            _NORMALIZE_NEW_WORKFLOW_START.reset(normalize_token)

    dispatch_harness._momo_full_profile_compat = True  # type: ignore[attr-defined]
    WorkflowHarnessMixin._dispatch_harness_message = dispatch_harness


def install_full_optimization_profile_compat() -> None:
    """幂等安装 PR2 兼容层。"""
    global _INSTALLED
    if _INSTALLED:
        return
    _install_engineering_intent_parser()
    _install_route_normalizer()
    _install_optimization_plan_normalizer()
    _install_task_handler_profile_propagation()
    _install_conversation_entry_normalizer()
    _install_harness_new_start_normalizer()
    _INSTALLED = True
