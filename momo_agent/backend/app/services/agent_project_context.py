from __future__ import annotations

import re
from typing import Any

from app.services.agent_project_repository import EngineeringProjectRepository
from app.services.agent_repository import AgentRepository, DEFAULT_OWNER
from app.services.platform_store import platform_store, utc_now


_TRUSTED_RUN_STATUSES = {'SUCCEEDED'}
_TRUSTED_EVIDENCE_MODES = {'REAL_FEM'}
_MAX_RELEVANT_RUNS = 8

_WORKSPACE_FACT_FIELDS = (
    'modelArtifactId',
    'modelFileName',
    'modelSha256',
    'solver',
    'loadKind',
    'loadArtifactId',
    'loadSha256',
    'damperType',
    'selectedLayoutId',
    'responseIds',
    'optimizationProfile',
)

_RESPONSE_ANCHORS = {
    'max_girder_end_displacement': ('梁端', '位移', 'displacement'),
    'max_acceleration': ('加速度', 'acceleration'),
    'max_tower_base_shear': ('塔底剪力', '剪力', 'shear'),
    'max_tower_base_moment': ('塔底弯矩', '弯矩', 'moment'),
    'max_damper_force': ('阻尼器力', '阻尼力', 'damper force'),
    'max_damper_stroke': ('行程', 'stroke'),
    'cumulative_displacement': ('累积位移', '累计位移', 'cumulative displacement'),
}


def _contains_any(text: str, anchors: tuple[str, ...]) -> bool:
    lowered = text.casefold()
    return any(anchor.casefold() in lowered for anchor in anchors)


def _explicit_solver(text: str) -> str | None:
    lowered = text.casefold()
    values: set[str] = set()
    if 'opensees' in lowered or '开放体系' in text:
        values.add('OPENSEESPY_INPROC')
    if 'ansys' in lowered or 'mapdl' in lowered:
        values.add('ANSYS')
    return next(iter(values)) if len(values) == 1 else None


def _explicit_load_kind(text: str) -> str | None:
    matches: list[str] = []
    if _contains_any(text, ('地震', 'earthquake', 'seismic')):
        matches.append('EARTHQUAKE')
    if _contains_any(text, ('风', 'wind')):
        matches.append('WIND')
    if _contains_any(text, ('车流', '交通', 'traffic')):
        matches.append('TRAFFIC')
    return matches[0] if len(set(matches)) == 1 else None


def _explicit_damper_type(text: str) -> str | None:
    matches: list[str] = []
    if _contains_any(text, ('粘滞', '黏滞', 'viscous')):
        matches.append('VISCOUS')
    if _contains_any(text, ('摩擦', 'friction')):
        matches.append('FRICTION')
    if _contains_any(text, ('电涡流', '涡流阻尼', 'eddy current')):
        matches.append('EDDY_CURRENT')
    return matches[0] if len(set(matches)) == 1 else None


def _explicit_layout(text: str) -> str | None:
    normalized = ''.join(text.casefold().split())
    if any(token in normalized for token in ('one_per_tower', '每塔一个', '每塔1个', '每塔一台')):
        return 'ONE_PER_TOWER'
    if any(token in normalized for token in ('two_per_tower', '每塔两个', '每塔2个', '每塔两台')):
        return 'TWO_PER_TOWER'
    return None


def _explicit_profile(text: str) -> str | None:
    lowered = text.casefold()
    if any(token in lowered for token in ('full', '完整', '全流程')):
        return 'FULL'
    if any(token in lowered for token in ('custom', '自定义优化')):
        return 'CUSTOM'
    if any(token in lowered for token in ('standard', '标准优化')):
        return 'STANDARD'
    return None


def _explicit_response_ids(text: str) -> list[str]:
    return [
        response_id
        for response_id, anchors in _RESPONSE_ANCHORS.items()
        if _contains_any(text, anchors)
    ]


class EngineeringProjectContextService:
    """Project/Workspace 的工程记忆读取、解析与受控写回边界。

    核心状态仍由 Project、Run、Artifact 和 Evidence 的结构化记录提供；这里不做
    embedding 检索，也不把自然语言摘要提升为工程事实。LLM 只在 Python 已经
    缩小的相关 run 候选中做语义判断。
    """

    def project_repository(self) -> EngineeringProjectRepository:
        return EngineeringProjectRepository(platform_store.state_path)

    def project_for_session(
        self,
        session_id: str,
        *,
        owner: str = DEFAULT_OWNER,
    ) -> dict[str, Any] | None:
        return self.project_repository().project_for_session(session_id, owner=owner)

    def filter_runs_to_project(
        self,
        runs: list[dict[str, Any]],
        *,
        session_id: str,
        owner: str = DEFAULT_OWNER,
    ) -> list[dict[str, Any]]:
        """绑定 Project 时严格限域；未绑定时保持 PR4 前的 owner 级行为。"""
        project = self.project_for_session(session_id, owner=owner)
        if project is None:
            return list(runs)
        allowed = {str(item) for item in project.get('sessionIds') or []}
        return [run for run in runs if str(run.get('sessionId') or '') in allowed]

    def build(
        self,
        *,
        repository: AgentRepository,
        session_id: str,
        owner: str = DEFAULT_OWNER,
        query: str = '',
        requested_task: str | None = None,
    ) -> dict[str, Any] | None:
        project = self.project_for_session(session_id, owner=owner)
        if project is None:
            return None
        workspace = {
            key: (project.get('workspace') or {}).get(key)
            for key in _WORKSPACE_FACT_FIELDS
        }
        workspace_revision = int(project.get('workspaceRevision') or 0)
        session_ids = [str(item) for item in project.get('sessionIds') or []]
        runs: list[dict[str, Any]] = []
        for related_session_id in session_ids:
            runs.extend(repository.list_runs(related_session_id))

        trusted = [run for run in runs if self._trusted_run(run)]
        relevant = sorted(
            (self._run_memory(run, workspace, query=query, requested_task=requested_task) for run in trusted),
            key=lambda item: (int(item['relevanceScore']), str(item.get('updatedAt') or '')),
            reverse=True,
        )[:_MAX_RELEVANT_RUNS]
        resolved_facts = {
            key: {'value': value, 'source': 'PROJECT_WORKSPACE'}
            for key, value in workspace.items()
            if value not in (None, '', [])
        }
        return {
            'version': 1,
            'project': {
                'projectId': project.get('projectId'),
                'name': project.get('name'),
                'workspaceRevision': workspace_revision,
            },
            'workspace': workspace,
            'resolvedFacts': resolved_facts,
            'relevantRuns': relevant,
            'trustPolicy': {
                'currentUserOverridesWorkspace': True,
                'workspaceOverridesHistoricalRuns': True,
                'historicalRunRequiresStatus': 'SUCCEEDED',
                'historicalRunRequiresEvidenceMode': 'REAL_FEM',
                'numericClaimsRequireRegisteredEvidence': True,
            },
        }

    def resolve_intent(
        self,
        intent: Any,
        *,
        project_context: dict[str, Any] | None,
        user_content: str,
    ) -> tuple[Any, dict[str, str]]:
        """只继承用户本轮没有明确覆盖的 Workspace 槽位，并返回字段来源。"""
        if not project_context:
            return intent, {}
        workspace = dict(project_context.get('workspace') or {})
        updates: dict[str, Any] = {}
        sources: dict[str, str] = {}

        explicit_solver = _explicit_solver(user_content)
        if explicit_solver is None and workspace.get('solver'):
            updates['solver'] = workspace['solver']
            sources['solver'] = 'PROJECT_WORKSPACE'
        elif explicit_solver is not None:
            sources['solver'] = 'USER_SPECIFIED'

        explicit_load = _explicit_load_kind(user_content)
        if explicit_load is None and workspace.get('loadKind'):
            updates['load_kind'] = workspace['loadKind']
            sources['loadKind'] = 'PROJECT_WORKSPACE'
        elif explicit_load is not None:
            updates['load_kind'] = explicit_load
            sources['loadKind'] = 'USER_SPECIFIED'

        explicit_damper = _explicit_damper_type(user_content)
        if (
            explicit_damper is None
            and workspace.get('damperType')
            and getattr(intent, 'task_type', None) == 'DAMPER_OPTIMIZATION'
        ):
            updates['damper_type'] = workspace['damperType']
            sources['damperType'] = 'PROJECT_WORKSPACE'
        elif explicit_damper is not None:
            updates['damper_type'] = explicit_damper
            sources['damperType'] = 'USER_SPECIFIED'

        explicit_layout = _explicit_layout(user_content)
        if explicit_layout is None and workspace.get('selectedLayoutId'):
            updates['selected_layout_id'] = workspace['selectedLayoutId']
            sources['selectedLayoutId'] = 'PROJECT_WORKSPACE'
        elif explicit_layout is not None:
            updates['selected_layout_id'] = explicit_layout
            sources['selectedLayoutId'] = 'USER_SPECIFIED'

        explicit_responses = _explicit_response_ids(user_content)
        if not explicit_responses and workspace.get('responseIds'):
            updates['response_ids'] = list(workspace['responseIds'])
            sources['responseIds'] = 'PROJECT_WORKSPACE'
        elif explicit_responses:
            sources['responseIds'] = 'USER_SPECIFIED'

        explicit_profile = _explicit_profile(user_content)
        if (
            explicit_profile is None
            and workspace.get('optimizationProfile')
            and getattr(intent, 'task_type', None) == 'DAMPER_OPTIMIZATION'
        ):
            updates['optimization_profile'] = workspace['optimizationProfile']
            sources['optimizationProfile'] = 'PROJECT_WORKSPACE'
        elif explicit_profile is not None:
            updates['optimization_profile'] = explicit_profile
            sources['optimizationProfile'] = 'USER_SPECIFIED'

        # PR4 Workspace 目前没有持久化自定义模型分析所需的 responseNodes /
        # responseElementIds。只记住 modelArtifactId/SHA 用于历史匹配，不能单独
        # 把模型引用灌入新 ANALYSIS，否则会制造不完整合同。用户本轮显式提供
        # artifactId 时仍由原始 EngineeringIntent 负责传入并标记来源。
        explicit_artifact = bool(re.search(r'art_[A-Za-z0-9_-]+', user_content))
        if explicit_artifact:
            sources['modelArtifactId'] = 'USER_SPECIFIED'

        inherited_slots = {
            key for key, source in sources.items() if source == 'PROJECT_WORKSPACE'
        }
        if inherited_slots and getattr(intent, 'missing_fields', None):
            updates['missing_fields'] = [
                slot for slot in intent.missing_fields if slot not in inherited_slots
            ]
        # build_engineering_contract treats an explicitly supplied fieldSources mapping as
        # authoritative, so memory resolution must return a complete provenance baseline.
        sources.setdefault('solver', 'DEFAULT')
        sources.setdefault('loadKind', 'DEFAULT')
        sources.setdefault('responseIds', 'DEFAULT')
        sources.setdefault('budget', 'DEFAULT')
        if getattr(intent, 'selected_layout_id', None) or workspace.get('selectedLayoutId'):
            sources.setdefault('selectedLayoutId', 'DEFAULT')
        if getattr(intent, 'task_type', None) == 'DAMPER_OPTIMIZATION':
            sources.setdefault('optimizationProfile', 'DEFAULT')
        resolved = intent.model_copy(update=updates) if updates else intent
        return resolved, sources

    def write_back_verified_run(
        self,
        run: dict[str, Any],
        *,
        owner: str | None = None,
    ) -> dict[str, Any] | None:
        """把已验证成功合同的稳定工程事实写回 Workspace；相同值不增 revision。"""
        if not self._trusted_run(run):
            return None
        session_id = str(run.get('sessionId') or '')
        if not session_id:
            return None
        resolved_owner = str(owner or run.get('ownerId') or DEFAULT_OWNER)
        project = self.project_for_session(session_id, owner=resolved_owner)
        if project is None:
            return None
        contract = run.get('workflowContract') if isinstance(run.get('workflowContract'), dict) else {}
        intent = run.get('intent') if isinstance(run.get('intent'), dict) else {}
        damper = contract.get('damper') if isinstance(contract.get('damper'), dict) else {}
        patch: dict[str, Any] = {
            'solver': contract.get('solver') or intent.get('solver'),
            'loadKind': contract.get('loadKind') or intent.get('loadKind'),
            'damperType': damper.get('type') or intent.get('damperType'),
            'selectedLayoutId': contract.get('selectedLayoutId') or intent.get('selectedLayoutId'),
            'responseIds': list(contract.get('responseIds') or intent.get('responseIds') or []),
            'modelArtifactId': contract.get('modelArtifactId') or intent.get('modelArtifactId'),
            'modelSha256': contract.get('modelSha256'),
            'loadArtifactId': contract.get('loadArtifactId'),
            'loadSha256': contract.get('loadSha256'),
        }
        if run.get('taskType') == 'DAMPER_OPTIMIZATION':
            patch['optimizationProfile'] = contract.get('optimizationProfile') or intent.get('optimizationProfile')
        patch = {key: value for key, value in patch.items() if value not in (None, '', [])}
        workspace = dict(project.get('workspace') or {})
        changed = {key: value for key, value in patch.items() if workspace.get(key) != value}
        if not changed:
            return project
        return self.project_repository().update_workspace(
            str(project['projectId']),
            changed,
            updated_at=utc_now(),
        )

    @staticmethod
    def _trusted_run(run: dict[str, Any]) -> bool:
        summary = run.get('resultSummary') if isinstance(run.get('resultSummary'), dict) else {}
        return (
            str(run.get('status') or '') in _TRUSTED_RUN_STATUSES
            and str(summary.get('evidenceMode') or '') in _TRUSTED_EVIDENCE_MODES
        )

    @staticmethod
    def _run_memory(
        run: dict[str, Any],
        workspace: dict[str, Any],
        *,
        query: str,
        requested_task: str | None,
    ) -> dict[str, Any]:
        intent = run.get('intent') if isinstance(run.get('intent'), dict) else {}
        contract = run.get('workflowContract') if isinstance(run.get('workflowContract'), dict) else {}
        damper = contract.get('damper') if isinstance(contract.get('damper'), dict) else {}
        task_type = str(run.get('taskType') or '')
        score = 0
        if requested_task and task_type == requested_task:
            score += 20
        run_id = str(run.get('runId') or '')
        if run_id and run_id.casefold() in query.casefold():
            score += 100
        query_profile = _explicit_profile(query)
        profile = contract.get('optimizationProfile') or intent.get('optimizationProfile')
        if query_profile and profile == query_profile:
            score += 15
        query_solver = _explicit_solver(query)
        solver = contract.get('solver') or intent.get('solver')
        if query_solver and solver == query_solver:
            score += 10
        query_load = _explicit_load_kind(query)
        load_kind = contract.get('loadKind') or intent.get('loadKind')
        if query_load and load_kind == query_load:
            score += 10
        query_damper = _explicit_damper_type(query)
        damper_type = damper.get('type') or intent.get('damperType')
        if query_damper and damper_type == query_damper:
            score += 10
        model_sha = contract.get('modelSha256')
        workspace_model_sha = workspace.get('modelSha256')
        compatible = not (
            model_sha and workspace_model_sha and str(model_sha) != str(workspace_model_sha)
        )
        if compatible:
            score += 5
        raw_summary = run.get('resultSummary') if isinstance(run.get('resultSummary'), dict) else {}
        # Project memory only carries selection metadata. Engineering numbers and narrative
        # stay behind registered result artifacts / inquiry tools, so bootstrap context can
        # never become an alternate numeric evidence channel.
        compact_summary = {
            key: raw_summary.get(key)
            for key in (
                'evidenceMode', 'validationStatus', 'reviewStatus',
                'finalRecommendationStatus',
            )
            if raw_summary.get(key) is not None
        }
        return {
            'runId': run_id,
            'sessionId': run.get('sessionId'),
            'taskType': task_type,
            'status': run.get('status'),
            'evidenceMode': (run.get('resultSummary') or {}).get('evidenceMode'),
            'solver': solver,
            'loadKind': load_kind,
            'damperType': damper_type,
            'selectedLayoutId': contract.get('selectedLayoutId') or intent.get('selectedLayoutId'),
            'responseIds': list(contract.get('responseIds') or intent.get('responseIds') or []),
            'optimizationProfile': profile,
            'modelArtifactId': contract.get('modelArtifactId') or intent.get('modelArtifactId'),
            'modelSha256': model_sha,
            'loadArtifactId': contract.get('loadArtifactId'),
            'loadSha256': contract.get('loadSha256'),
            'artifactIds': list(run.get('artifactIds') or []),
            'reportArtifactId': run.get('reportArtifactId'),
            'resultSummary': compact_summary,
            'compatibleWithWorkspace': compatible,
            'relevanceScore': score,
            'updatedAt': run.get('updatedAt') or run.get('createdAt'),
        }


engineering_project_context_service = EngineeringProjectContextService()
