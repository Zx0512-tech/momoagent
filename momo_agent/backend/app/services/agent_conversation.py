from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

from fastapi import HTTPException

from app.agents.inquiry import RESPONSE_COLUMN_ALIASES, RESPONSE_METRIC_SPECS, InquiryTools, resolve_column
from app.agents.task_registry import engineering_task_spec
from app.agents.tools import ToolExecutionError, ToolRisk
from app.agents.workflows import WorkflowGuard, WorkflowToolCall
from app.core.exceptions import LLMUnavailableError
from app.services.agent_capabilities import build_capability_facts
from app.services.agent_engineering import (
    ENGINEERING_TASK_TYPES,
    build_damper_comparison_contract,
    build_engineering_contract,
    build_parameter_sweep_contract,
)
from app.services.agent_figure_service import AgentFigureService
from app.services.agent_llm import ApprovalReplyIntent, NarrativeResult
from app.services.agent_repository import AgentRepository, run_state_lock
from app.services.platform_store import gen_id, platform_store, utc_now
from app.services.result_inquiry import ResultInquiryError, ResultInquiryService


class AgentConversationMixin:
    """会话、澄清和结果追问的兼容编排。"""

    def _workflow_guard_reply(
        self,
        repository: AgentRepository,
        session: dict[str, Any],
        run: dict[str, Any],
        error: ToolExecutionError,
    ) -> dict[str, Any]:
        """Guard 拒绝旁路回复时保留原 run，并返回结构化可见错误。"""
        repository.add_message({
            'messageId': gen_id('msg'),
            'sessionId': session['sessionId'],
            'role': 'ASSISTANT',
            'content': f'工作流未执行：{error.message}',
            'runId': run['runId'],
            'createdAt': utc_now(),
        })
        session['updatedAt'] = utc_now()
        repository.save_session(session)
        return self._decorate_run(run)

    def _clarification_question(self, *, intent: Any, goal: str) -> str:
        """优先用 LLM 将本轮所有缺失槽位组织成一条问题。"""
        if not intent.missing_fields:
            return str(intent.summary)
        ask = getattr(self.planner, 'ask_for_slots', None)
        if not callable(ask):
            return str(intent.summary)
        return str(ask(
            missing_slots=list(intent.missing_fields),
            prior_intent=intent.model_dump(by_alias=True),
            goal=goal,
        ))

    @staticmethod
    def _reread_run_under_lock(
        repository: AgentRepository,
        run: dict[str, Any],
    ) -> dict[str, Any]:
        """锁内重读 run，避免使用取锁前的陈旧副本；测试替身可不实现 get_run。"""
        get_run = getattr(repository, 'get_run', None)
        if not callable(get_run):
            return run
        return get_run(str(run['runId'])) or run

    def _dispatch_message(
        self,
        repository: AgentRepository,
        session: dict[str, Any],
        content: str,
        now: str,
        load_import: dict[str, Any] | None,
        task_type: str,
        *,
        event_sink: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        if task_type == 'AUTO':
            find_pending_approval = getattr(repository, 'find_pending_approval_run', None)
            pending_approval = find_pending_approval(session['sessionId']) if callable(find_pending_approval) else None
            if pending_approval:
                # 后续路径会对既有 run 做读-改-写，必须与并发轮询/审批请求互斥；
                # 锁内重读避免拿到锁前的陈旧副本。
                with run_state_lock(str(pending_approval['runId'])):
                    pending_approval = self._reread_run_under_lock(repository, pending_approval)
                    if (
                        self._runtime_mode() == 'WORKFLOW_HARNESS'
                        and pending_approval.get('runtimeMode') == 'WORKFLOW_HARNESS'
                        and pending_approval.get('workflowSnapshot')
                    ):
                        return self._dispatch_harness_approval_reply(
                            repository,
                            session,
                            pending_approval,
                            content,
                            now,
                        )
                    return self._resolve_approval_reply(
                        repository,
                        session,
                        pending_approval,
                        content,
                        now,
                    )
            pending_clarification = repository.find_pending_clarification_run(session['sessionId'])
            if pending_clarification:
                with run_state_lock(str(pending_clarification['runId'])):
                    pending_clarification = self._reread_run_under_lock(repository, pending_clarification)
                    if (
                        self._runtime_mode() == 'WORKFLOW_HARNESS'
                        and pending_clarification.get('runtimeMode') == 'WORKFLOW_HARNESS'
                        and pending_clarification.get('workflowSnapshot')
                    ):
                        return self._dispatch_harness_clarification_reply(
                            repository,
                            session,
                            pending_clarification,
                            content,
                            now,
                            load_import,
                        )
                    return self._resolve_clarification(
                        repository,
                        session,
                        pending_clarification,
                        content,
                        now,
                        load_import,
                    )
            active_run = self._find_active_harness_run(repository, session['sessionId'])
            if active_run:
                with run_state_lock(str(active_run['runId'])):
                    active_run = self._reread_run_under_lock(repository, active_run)
                    return self._dispatch_harness_active_run_message(
                        repository,
                        session,
                        active_run,
                        content,
                        now,
                        load_import,
                        event_sink=event_sink,
                    )
            inquirable = self._find_inquirable_run(repository, session['sessionId'])
            if inquirable:
                if self._runtime_mode() == 'WORKFLOW_HARNESS' and load_import is None:
                    return self._create_native_inquiry_run(
                        repository,
                        session,
                        inquirable,
                        content,
                        now,
                        prior_messages=None,
                        event_sink=event_sink,
                    )
                if self._runtime_mode() != 'WORKFLOW_HARNESS':
                    inquiry = self._create_inquiry_run(
                        repository,
                        session,
                        inquirable,
                        content,
                        now,
                    )
                    if inquiry is not None:
                        return inquiry
        if (
            self._runtime_mode() == 'WORKFLOW_HARNESS'
            and task_type in {
                'AUTO', 'ANALYSIS', 'DAMPER_OPTIMIZATION', 'DAMPER_COMPARISON', 'DAMPER_PARAMETER_SWEEP',
            }
        ):
            inquirable = self._find_inquirable_run(repository, session['sessionId'])
            if inquirable is None:
                global_runs = self._find_all_inquirable_runs(
                    repository,
                    str(session.get('ownerId') or 'local'),
                )
                inquirable = global_runs[0] if global_runs else None
            # 结果查询与新求解的判定统一交给 LLM 路由（workflow.start），
            # 不再用关键词启发式短路——那会让路由行为无法测试且随措辞漂移。
            return self._dispatch_harness_message(
                repository,
                session,
                content,
                now,
                load_import,
                inquirable,
                requested_task=None if task_type == 'AUTO' else task_type,
                event_sink=event_sink,
            )
        if task_type != 'AUTO':
            resolved_task = task_type
            route_evidence = {
                'routeMode': 'USER_SPECIFIED',
                'resolvedTask': resolved_task,
                'confidence': 1.0,
                'reason': '用户在界面显式指定任务类型。',
                }
        else:
            route_result = self.planner.classify_task(content, has_file=load_import is not None)
            resolved_task = route_result.route.task_type
            route_evidence = {
                'routeMode': 'LLM',
                'resolvedTask': resolved_task,
                'confidence': route_result.route.confidence,
                'reason': route_result.route.reason,
            }
        if resolved_task == 'CLARIFICATION':
            return self._create_conversation_run(
                repository,
                session,
                content,
                now,
                intent='CLARIFICATION',
                route_evidence=route_evidence,
            )
        if resolved_task in {'ANALYSIS', 'DAMPER_OPTIMIZATION', 'DAMPER_COMPARISON', 'DAMPER_PARAMETER_SWEEP'}:
            return self._create_engineering_run(
                repository,
                session,
                content,
                now,
                requested_task=resolved_task,
                load_import=load_import,
                route_evidence=route_evidence,
            )
        if resolved_task in {'SMALL_TALK', 'CAPABILITY_QUERY'}:
            return self._create_conversation_run(
                repository,
                session,
                content,
                now,
                intent=resolved_task,
                route_evidence=route_evidence,
            )
        if resolved_task == 'UNSUPPORTED':
            return self._create_unsupported_run(
                repository,
                session,
                content,
                now,
                route_evidence=route_evidence,
            )
        if resolved_task == 'LOAD_IMPORT' and load_import is None:
            return self._create_conversation_run(
                repository,
                session,
                content,
                now,
                intent='CAPABILITY_QUERY',
                route_evidence=route_evidence,
            )
        return self._create_legacy_load_run(
            repository,
            session,
            content,
            now,
            load_import,
            route_evidence=route_evidence,
        )

    def _create_conversation_run(
        self,
        repository: AgentRepository,
        session: dict[str, Any],
        content: str,
        now: str,
        *,
        intent: str,
        route_evidence: dict[str, Any],
    ) -> dict[str, Any]:
        reply = self.planner.respond_conversationally(
            message=content,
            intent=intent,
            capability_facts=build_capability_facts(),
        )
        run = {
            'runId': gen_id('agr'),
            'sessionId': session['sessionId'],
            'goal': content,
            'taskType': 'CONVERSATION',
            'status': 'SUCCEEDED',
            'currentStage': 'COMPLETED',
            'importId': None,
            'artifactIds': [],
            'jobId': None,
            'routeEvidence': route_evidence,
            'resultSummary': {'message': reply, 'intent': intent},
            'createdAt': now,
            'updatedAt': utc_now(),
        }
        repository.save_run(run)
        repository.add_message({
            'messageId': gen_id('msg'),
            'sessionId': session['sessionId'],
            'role': 'ASSISTANT',
            'content': reply,
            'runId': run['runId'],
            'createdAt': utc_now(),
        })
        session['updatedAt'] = utc_now()
        repository.save_session(session)
        return self._decorate_run(run)

    def _resolve_approval_reply(
        self,
        repository: AgentRepository,
        session: dict[str, Any],
        run: dict[str, Any],
        content: str,
        now: str,
    ) -> dict[str, Any]:
        """在待审批运行上消费一条自然语言审批回复。"""
        approval_id = run.get('pendingApprovalId')
        approval = repository.get_approval(approval_id) if approval_id else None
        if not approval or approval.get('status') != 'PENDING':
            return self._decorate_run(run)
        if run.get('runtimeMode') == 'WORKFLOW_HARNESS' and run.get('workflowSnapshot'):
            # 审批回复不是自由更新状态的旁路；先验证仍停在冻结审批步骤。
            try:
                WorkflowGuard().authorize(
                    workflow_snapshot=run['workflowSnapshot'],
                    current_step=str(run.get('currentStep') or ''),
                    completed_steps=run.get('completedSteps') or [],
                    tool_call=WorkflowToolCall(
                        name='approval.request',
                        risk=ToolRisk.MUTATING,
                        approved=True,
                        idempotencyKey=f'{approval_id}:reply',
                    ),
                )
            except ToolExecutionError as exc:
                return self._workflow_guard_reply(repository, session, run, exc)
        classify = getattr(self.planner, 'classify_approval_reply', None)
        if callable(classify):
            decision = classify(
                content,
                {'summary': approval.get('summary') or ''},
            )
        else:
            decision = ApprovalReplyIntent(
                decision='UNCLEAR',
                confidence=0.0,
                reason='当前未配置审批回复分类器。',
            )
        decision_name = str(getattr(decision, 'decision', 'UNCLEAR'))
        if decision_name == 'APPROVE':
            result = self.decide_approval(str(approval_id), True)
            assistant_content = '已确认批准，开始执行审批冻结的工程任务。'
            updated_run = result['run']
        elif decision_name == 'REJECT':
            result = self.decide_approval(str(approval_id), False)
            assistant_content = '已拒绝本次执行，未启动工程求解。'
            updated_run = result['run']
        elif decision_name == 'MODIFY':
            modifications = getattr(decision, 'modifications', None)
            changes = modifications.model_dump(exclude_none=True, by_alias=True) if hasattr(modifications, 'model_dump') else dict(modifications or {})
            result = self.update_approval(run['runId'], changes)
            assistant_content = '已按你的要求重新生成方案，请继续确认是否允许执行。'
            updated_run = result['run']
        else:
            assistant_content = '未能确认您的意见，请回复“同意执行”或提出修改要求。'
            updated_run = self._decorate_run(run)
        repository.add_message({
            'messageId': gen_id('msg'),
            'sessionId': session['sessionId'],
            'role': 'ASSISTANT',
            'content': assistant_content,
            'runId': updated_run['runId'],
            'createdAt': utc_now(),
        })
        session['updatedAt'] = utc_now()
        repository.save_session(session)
        return updated_run

    def _create_llm_unavailable_run(
        self,
        repository: AgentRepository,
        session: dict[str, Any],
        content: str,
        now: str,
        exc: LLMUnavailableError,
    ) -> dict[str, Any]:
        run = {
            'runId': gen_id('agr'),
            'sessionId': session['sessionId'],
            'goal': content,
            'taskType': 'LLM_UNAVAILABLE',
            'status': 'FAILED',
            'currentStage': 'LLM_UNAVAILABLE',
            'importId': None,
            'artifactIds': [],
            'jobId': None,
            'llmFailure': {
                'stage': exc.stage,
                'reason': exc.reason,
                'detail': exc.detail,
            },
            'resultSummary': {'message': exc.user_message},
            'createdAt': now,
            'updatedAt': utc_now(),
        }
        repository.save_run(run)
        repository.add_message({
            'messageId': gen_id('msg'),
            'sessionId': session['sessionId'],
            'role': 'ASSISTANT',
            'content': exc.user_message,
            'runId': run['runId'],
            'createdAt': utc_now(),
        })
        session['updatedAt'] = utc_now()
        repository.save_session(session)
        return self._decorate_run(run)

    def _find_inquirable_run(
        self,
        repository: AgentRepository,
        session_id: str,
    ) -> dict[str, Any] | None:
        """返回最近一条带证据报告的终态工程运行。"""
        runs = self._find_inquirable_runs(repository, session_id)
        return runs[0] if runs else None

    @staticmethod
    def _find_active_harness_run(
        repository: AgentRepository,
        session_id: str,
    ) -> dict[str, Any] | None:
        """返回最近一条正在执行、验收或等待报告恢复的 Harness 工程运行。"""
        list_runs = getattr(repository, 'list_runs', None)
        if not callable(list_runs):
            return None
        runs = [
            run for run in list_runs(session_id)
            if run.get('runtimeMode') == 'WORKFLOW_HARNESS'
            and run.get('workflowSnapshot')
            and run.get('taskType') in ENGINEERING_TASK_TYPES
            and (
                run.get('status') in {'WAITING_JOB', 'REVIEWING'}
                or (
                    run.get('status') == 'FAILED'
                    and isinstance(run.get('workflowGateError'), dict)
                    and run['workflowGateError'].get('code') == 'REPORT_GENERATION_ERROR'
                )
            )
        ]
        if not runs:
            return None
        return max(
            runs,
            key=lambda run: str(run.get('updatedAt') or run.get('createdAt') or ''),
        )

    @staticmethod
    def _find_inquirable_runs(
        repository: AgentRepository,
        session_id: str,
    ) -> list[dict[str, Any]]:
        """返回会话中所有可追问的终态工程运行，按最近更新时间倒序。"""
        list_runs = getattr(repository, 'list_runs', None)
        if not callable(list_runs):
            return []
        runs = [
            run for run in list_runs(session_id)
            if run.get('reportArtifactId') and run.get('status') in {
                'SUCCEEDED', 'COMPLETED_DIAGNOSTIC',
            }
        ]
        return sorted(
            runs,
            key=lambda run: str(run.get('updatedAt') or run.get('createdAt') or ''),
            reverse=True,
        )

    @staticmethod
    def _find_all_inquirable_runs(
        repository: AgentRepository,
        owner: str | None = None,
    ) -> list[dict[str, Any]]:
        """返回可安全查询的终态工程结果；给出 owner 时只返回其名下运行。"""
        list_all_runs = getattr(repository, 'list_all_runs', None)
        if not callable(list_all_runs):
            return []
        runs = [
            run for run in list_all_runs()
            if run.get('taskType') in ENGINEERING_TASK_TYPES
            and run.get('reportArtifactId')
            and run.get('status') in {'SUCCEEDED', 'COMPLETED_DIAGNOSTIC'}
            and (owner is None or str(run.get('ownerId') or 'local') == owner)
        ]
        return sorted(
            runs,
            key=lambda run: str(run.get('updatedAt') or run.get('createdAt') or ''),
            reverse=True,
        )

    @staticmethod
    def _result_metadata(run: dict[str, Any]) -> dict[str, Any]:
        """从已持久化合同中提取结果标记，不推测缺失的工程参数。"""
        intent = run.get('intent') if isinstance(run.get('intent'), dict) else {}
        contract = (
            run.get('workflowContract')
            if isinstance(run.get('workflowContract'), dict)
            else {}
        )
        summary = run.get('resultSummary') if isinstance(run.get('resultSummary'), dict) else {}
        damper = contract.get('damper') if isinstance(contract.get('damper'), dict) else {}

        damper_types: list[str] = []
        candidates = [
            intent.get('damperType'),
            contract.get('damperType'),
            damper.get('type'),
        ]
        candidates.extend(intent.get('damperTypes') or [])
        candidates.extend(contract.get('damperTypes') or [])
        for candidate in candidates:
            if candidate is None:
                continue
            value = str(candidate)
            if value and value not in damper_types:
                damper_types.append(value)

        parameters: dict[str, Any] = {}
        configured_parameters = damper.get('parameters')
        if isinstance(configured_parameters, dict):
            parameters.update({str(key): value for key, value in configured_parameters.items()})
        cases = contract.get('cases')
        if isinstance(cases, list):
            for case in cases:
                if not isinstance(case, dict):
                    continue
                case_type = case.get('damperType')
                if case_type is not None and str(case_type) not in damper_types:
                    damper_types.append(str(case_type))
                case_parameters = case.get('parameters')
                if isinstance(case_parameters, dict):
                    case_id = str(case.get('caseId') or case_type or len(parameters))
                    parameters[case_id] = {
                        str(key): value for key, value in case_parameters.items()
                    }
        for key in (
            'dampingCoefficient', 'velocityExponent', 'forceCapN', 'designVelocityMps',
            'yieldForceN', 'postYieldRatio', 'stiffnessNPerM',
        ):
            if contract.get(key) is not None:
                parameters[key] = contract[key]
        recommended = summary.get('recommendedParameters')
        if isinstance(recommended, dict):
            parameters.update({str(key): value for key, value in recommended.items()})

        return {
            'runId': run.get('runId'),
            'sessionId': run.get('sessionId'),
            'taskType': run.get('taskType'),
            'status': run.get('status'),
            'condition': intent.get('loadKind') or contract.get('loadKind'),
            'model': contract.get('model') or intent.get('model'),
            'solver': intent.get('solver') or contract.get('solver'),
            'hasDamper': bool(damper_types or damper or parameters),
            'damperTypes': damper_types,
            'damperParameters': parameters,
            'updatedAt': run.get('updatedAt') or run.get('createdAt'),
        }

    def _global_inquiry_context(
        self,
        repository: AgentRepository,
        source_run: dict[str, Any],
        service: ResultInquiryService,
    ) -> tuple[dict[str, Any], dict[str, str], list[dict[str, Any]], dict[str, Any]]:
        """构造跨会话结果目录，实际读取仍限于逐 run 校验后的 CSV 白名单。"""
        source_artifacts = self._inquiry_artifacts(source_run)
        source_catalog = self._build_inquiry_catalog(source_run, service, source_artifacts)
        registered_artifacts = dict(source_artifacts)
        available_runs = self._find_all_inquirable_runs(
            repository,
            str(source_run.get('ownerId') or 'local'),
        )
        if not any(run.get('runId') == source_run.get('runId') for run in available_runs):
            available_runs.insert(0, source_run)

        available_results: list[dict[str, Any]] = []
        catalogs_by_run_id: dict[str, Any] = {}
        unavailable_run_ids: list[str] = []
        for candidate in available_runs:
            run_id = str(candidate.get('runId') or '')
            if not run_id:
                continue
            available_results.append(self._result_metadata(candidate))
            if run_id == source_run.get('runId'):
                candidate_artifacts = source_artifacts
                candidate_catalog = source_catalog
                scoped_artifacts = dict(candidate_artifacts)
            else:
                try:
                    candidate_artifacts = self._inquiry_artifacts(candidate)
                    candidate_catalog = self._build_inquiry_catalog(
                        candidate, service, candidate_artifacts,
                    )
                except ResultInquiryError:
                    unavailable_run_ids.append(run_id)
                    continue
                scoped_artifacts = {
                    f'{run_id}/{name}': artifact_id
                    for name, artifact_id in candidate_artifacts.items()
                }
                registered_artifacts.update(scoped_artifacts)
            artifact_bindings = {
                name: {
                    'registeredName': (
                        name if run_id == source_run.get('runId') else f'{run_id}/{name}'
                    ),
                    'artifactId': artifact_id,
                }
                for name, artifact_id in candidate_artifacts.items()
            }
            catalogs_by_run_id[run_id] = {
                'catalog': candidate_catalog,
                'registeredArtifacts': scoped_artifacts,
                'artifactBindings': artifact_bindings,
            }
        context = {
            'availableResults': available_results,
            'catalogsByRunId': catalogs_by_run_id,
            'unavailableResultRunIds': unavailable_run_ids,
        }
        return source_catalog, registered_artifacts, available_results, context

    def _create_inquiry_run(
        self,
        repository: AgentRepository,
        session: dict[str, Any],
        source_run: dict[str, Any],
        content: str,
        now: str,
    ) -> dict[str, Any] | None:
        service = ResultInquiryService(platform_store)
        artifacts = self._inquiry_artifacts(source_run)
        catalog = self._build_inquiry_catalog(source_run, service, artifacts)
        all_runs = self._find_inquirable_runs(repository, session['sessionId'])
        catalog['runs'] = []
        catalog_runs = [
            candidate for candidate in all_runs
            if candidate.get('runId') != source_run.get('runId')
        ][:3]
        for candidate in catalog_runs:
            candidate_artifacts = self._inquiry_artifacts(candidate)
            for name, artifact_id in candidate_artifacts.items():
                scoped_name = f'{candidate.get("runId")}/{name}'
                artifacts[scoped_name] = artifact_id
                if name in {'real_optimization_summary.json', 'optimization_summary.json'}:
                    try:
                        topsis = service.topsis(artifact_id, limit=1)
                    except ResultInquiryError:
                        continue
                    catalog['artifacts'][scoped_name] = {
                        'artifactId': artifact_id,
                        'kind': 'JSON_SUMMARY',
                        'topsis': {
                            'availableCount': topsis['availableCount'],
                            'objectiveNames': topsis['objectiveNames'],
                        },
                    }
                    continue
                try:
                    catalog['artifacts'][scoped_name] = {
                        'artifactId': artifact_id,
                        'columns': service.columns(artifact_id),
                    }
                except ResultInquiryError:
                    continue
            summary_catalog = self._build_inquiry_catalog(
                candidate,
                service,
                candidate_artifacts,
            )
            catalog['runs'].append({
                'runId': candidate.get('runId'),
                'taskType': candidate.get('taskType'),
                'status': candidate.get('status'),
                'objectives': summary_catalog.get('objectives') or {},
                'baselineObjectives': summary_catalog.get('baselineObjectives') or {},
                'recommendedObjectives': summary_catalog.get('recommendedObjectives') or {},
                'objectiveChanges': summary_catalog.get('objectiveChanges') or {},
                'responseComparison': summary_catalog.get('responseComparison') or {},
                'responseComparisonPercent': summary_catalog.get('responseComparisonPercent') or {},
                'sampleResponses': summary_catalog.get('sampleResponses') or [],
            })
        evidence_mode = str((source_run.get('resultSummary') or {}).get('evidenceMode') or 'UNKNOWN')
        prior_results = self._prior_inquiry_results(repository, session['sessionId'])
        plan_inquiry = getattr(self.planner, 'plan_inquiry', None)
        if not callable(plan_inquiry):
            raise LLMUnavailableError('INQUIRY_PLANNING', 'LLM_NOT_CONFIGURED')
        plan = plan_inquiry(
            question=content,
            catalog=catalog,
            prior_results=[prior_results] if prior_results else None,
        )
        if (
            bool(getattr(plan, 'needs_followup', False))
            and not list(getattr(plan, 'queries', []) or [])
            and getattr(plan, 'figure', None) is None
        ):
            return None
        query_results = self._execute_inquiry_plan(
            plan,
            service=service,
            artifacts=artifacts,
        )
        inquiry_run_id = gen_id('agr')
        figures: list[dict[str, Any]] = []
        figure_artifact_ids: list[str] = []
        if getattr(plan, 'figure', None) is not None:
            try:
                figure_service = AgentFigureService(platform_store)
                render_input = figure_service.build_render_input(
                    run_id=inquiry_run_id,
                    figure_request=plan.figure,
                    catalog=catalog,
                )
                rendered = figure_service.render(render_input)
            except (KeyError, ValueError, ResultInquiryError) as exc:
                raise HTTPException(status_code=422, detail={
                    'code': 'FIGURE_REQUEST_INVALID',
                    'message': str(exc),
                }) from exc
            figure_artifact_ids = [item.artifact_id for item in rendered.figure_artifacts]
            figures = [{
                'claim': plan.figure.claim,
                'artifactId': item.artifact_id,
                'metrics': list(plan.figure.metrics),
            } for item in rendered.figure_artifacts]
        facts: dict[str, Any] = {
            'objectives': catalog['objectives'],
            'objectiveChanges': catalog.get('objectiveChanges') or {},
            'responseComparison': catalog.get('responseComparison') or {},
            'responseComparisonPercent': catalog.get('responseComparisonPercent') or {},
            'caseResults': catalog.get('caseResults') or [],
            'sampleResponses': catalog.get('sampleResponses') or [],
            'figures': figures,
            'queries': query_results,
            'planReason': plan.reason,
        }
        if prior_results:
            facts['priorResults'] = [prior_results]
        unavailable = [item['artifact'] for item in query_results if item.get('error')]
        if unavailable:
            facts['unavailable'] = unavailable

        explanation = self._safe_explain_inquiry(
            question=content,
            question_type='FREEFORM',
            evidence_mode=evidence_mode,
            facts=facts,
        )
        plan_payload = plan.model_dump(by_alias=True, mode='json') if hasattr(plan, 'model_dump') else {
            'queries': [query.model_dump(by_alias=True, mode='json') for query in plan.queries],
            'needsFollowup': bool(getattr(plan, 'needs_followup', False)),
            'reason': str(getattr(plan, 'reason', '')),
        }
        run = {
            'runId': inquiry_run_id,
            'sessionId': session['sessionId'],
            'goal': content,
            'taskType': 'INQUIRY',
            'status': 'SUCCEEDED',
            'currentStage': 'COMPLETED',
            'importId': None,
            'artifactIds': figure_artifact_ids,
            'figureArtifactIds': figure_artifact_ids,
            'jobId': None,
            'sourceRunId': source_run['runId'],
            'inquiryPlan': plan_payload,
            'inquiryFacts': facts,
            'resultSummary': {
                'evidenceMode': evidence_mode,
                'message': explanation.text,
                'narrativeSummary': explanation.text,
                'narrativeMode': explanation.narrative_mode,
                'narrativeFallbackReason': explanation.fallback_reason,
                'figures': figures,
                'figureArtifactIds': figure_artifact_ids,
            },
            'createdAt': now,
            'updatedAt': utc_now(),
        }
        repository.save_run(run)
        repository.save_step({
            'stepId': gen_id('step'),
            'runId': run['runId'],
            'idempotencyKey': f'{run["runId"]}:INQUIRY_PLAN',
            'title': '规划并执行只读结果查询',
            'status': 'SUCCEEDED',
            'createdAt': utc_now(),
        })
        repository.add_message({
            'messageId': gen_id('msg'),
            'sessionId': session['sessionId'],
            'role': 'ASSISTANT',
            'content': explanation.text,
            'runId': run['runId'],
            'createdAt': utc_now(),
        })
        session['updatedAt'] = utc_now()
        repository.save_session(session)
        return self._decorate_run(run)

    def _build_inquiry_catalog(
        self,
        run: dict[str, Any],
        service: ResultInquiryService,
        artifacts: dict[str, str],
    ) -> dict[str, Any]:
        """列出实际可查的结果文件、语义指标和有限标量结论。"""
        summary = run.get('resultSummary') or {}
        objectives = summary.get('objectives') or {}
        baseline_objectives = summary.get('baselineObjectives') or {}
        recommended_objectives = summary.get('recommendedObjectives') or {}
        case_results = [
            {
                'caseId': case.get('caseId'),
                'damperType': case.get('damperType'),
                'status': case.get('status'),
                'objectives': case.get('objectives') or {},
            }
            for case in (summary.get('caseResults') or [])
            if isinstance(case, dict)
        ]
        sample_responses = [
            {
                key: value
                for key, value in sample.items()
                if key in {'sampleType', 'sampleIndex', 'caseId', 'status', 'design', 'responses'}
            }
            for sample in (summary.get('sampleResponses') or [])
            if isinstance(sample, dict)
        ]
        catalog: dict[str, Any] = {
            'objectives': objectives,
            'baselineObjectives': baseline_objectives,
            'recommendedObjectives': recommended_objectives,
            'objectiveChanges': self._objective_changes(baseline_objectives, recommended_objectives),
            'responseComparison': summary.get('responseComparison') or {},
            'responseComparisonPercent': self._comparison_percentages(summary.get('responseComparison') or {}),
            'caseResults': case_results,
            'sampleResponses': sample_responses,
            'evidenceMode': summary.get('evidenceMode'),
            'taskType': run.get('taskType'),
            'artifacts': {},
            'metrics': {},
        }
        result_catalog = self._load_result_catalog(run)
        if result_catalog is not None:
            catalog['resultCatalog'] = {
                'artifactId': result_catalog['artifactId'],
                'schemaVersion': result_catalog['schemaVersion'],
                'entryCount': result_catalog['entryCount'],
                'verified': True,
            }
        for name, artifact_id in artifacts.items():
            if name.endswith('real_optimization_summary.json') or name.endswith('optimization_summary.json'):
                try:
                    topsis = service.topsis(artifact_id, limit=1)
                except ResultInquiryError:
                    continue
                catalog['artifacts'][name] = {
                    'artifactId': artifact_id,
                    'kind': 'JSON_SUMMARY',
                    'topsis': {
                        'availableCount': topsis['availableCount'],
                        'objectiveNames': topsis['objectiveNames'],
                    },
                }
                catalog['topsis'] = {
                    'artifact': name,
                    'availableCount': topsis['availableCount'],
                    'objectiveNames': topsis['objectiveNames'],
                }
                continue
            try:
                columns = service.columns(artifact_id)
            except ResultInquiryError:
                continue
            artifact_info: dict[str, Any] = {
                'artifactId': artifact_id,
                'columns': columns,
            }
            if result_catalog is not None:
                entry = result_catalog['byArtifactId'].get(artifact_id)
                if entry is not None:
                    artifact_info.update({
                        'units': dict(entry['units']),
                        'sourceSha256': entry['sha256'],
                        'verified': True,
                    })
            catalog['artifacts'][name] = artifact_info
            for metric_id, spec in RESPONSE_METRIC_SPECS.items():
                column = resolve_column(columns, spec['semantic'], RESPONSE_COLUMN_ALIASES)
                if not column:
                    continue
                metric = catalog['metrics'].setdefault(metric_id, {
                    'label': spec['label'],
                    'unit': spec['unit'],
                    'semantic': spec['semantic'],
                    'sources': [],
                })
                metric['sources'].append({
                    'artifact': name,
                    'column': column,
                })
                metric.setdefault('artifact', name)
                metric.setdefault('column', column)
        return catalog

    @staticmethod
    def _objective_changes(
        baseline: dict[str, Any],
        recommended: dict[str, Any],
    ) -> dict[str, dict[str, float | None]]:
        """只对有限标量目标计算基线变化，禁止把时程数组带入 facts。"""
        changes: dict[str, dict[str, float | None]] = {}
        for key in sorted(set(baseline) & set(recommended)):
            left = baseline.get(key)
            right = recommended.get(key)
            if not isinstance(left, (int, float)) or isinstance(left, bool):
                continue
            if not isinstance(right, (int, float)) or isinstance(right, bool):
                continue
            difference = float(right) - float(left)
            relative = None if float(left) == 0 else difference / abs(float(left))
            changes[key] = {
                'baseline': float(left),
                'recommended': float(right),
                'difference': difference,
                'relativeChange': relative,
                'relativeChangePercent': None if relative is None else round(relative * 100.0, 6),
            }
        return changes

    @staticmethod
    def _comparison_percentages(comparison: dict[str, Any]) -> dict[str, Any]:
        """把登记的相对小数转换为独立百分比字段，避免模型自行乘 100。"""
        metrics: dict[str, dict[str, float]] = {}
        for name, values in (comparison.get('metrics') or {}).items():
            if not isinstance(values, dict):
                continue
            relative = values.get('relativeToFirst')
            if not isinstance(relative, (int, float)) or isinstance(relative, bool):
                continue
            metrics[str(name)] = {
                'relativeChangePercent': round(float(relative) * 100.0, 6),
            }
        return {
            'firstCaseId': comparison.get('firstCaseId'),
            'secondCaseId': comparison.get('secondCaseId'),
            'metrics': metrics,
        }

    @staticmethod
    def _prior_inquiry_results(
        repository: AgentRepository,
        session_id: str,
    ) -> dict[str, Any] | None:
        runs = sorted(
            repository.list_runs(session_id),
            key=lambda run: str(run.get('updatedAt') or run.get('createdAt') or ''),
            reverse=True,
        )
        for run in runs:
            facts = run.get('inquiryFacts') or {}
            if run.get('taskType') == 'INQUIRY' and facts:
                summary = {'queries': facts.get('queries') or []}
                if facts.get('objectives'):
                    summary['objectives'] = facts['objectives']
                return summary
        return None

    @staticmethod
    def _execute_inquiry_plan(
        plan: Any,
        *,
        service: ResultInquiryService,
        artifacts: dict[str, str],
    ) -> list[dict[str, Any]]:
        """执行最多八条只读查询；单条失败只记录 error，不影响其他查询。"""
        results: list[dict[str, Any]] = []
        tools = InquiryTools(service=service)
        for query in list(getattr(plan, 'queries', []) or [])[:8]:
            operation = str(query.op)
            artifact_name = str(query.artifact)
            columns = list(query.columns or [])
            base = {
                'op': operation,
                'artifact': artifact_name,
                'columns': columns,
            }
            artifact_id = artifacts.get(artifact_name)
            if not artifact_id:
                results.append({**base, 'error': 'ARTIFACT_NOT_FOUND'})
                continue
            try:
                if operation == 'peak':
                    if not columns:
                        raise ResultInquiryError('peak 需要一个列名')
                    data = tools.call('result.peak', {
                        'artifact_id': artifact_id,
                        'column': columns[0],
                    })
                elif operation == 'at_time':
                    target_time = query.target_time
                    if target_time is None or not columns:
                        raise ResultInquiryError('at_time 需要 targetTime 和列名')
                    data = tools.call('result.at_time', {
                        'artifact_id': artifact_id,
                        'columns': columns,
                        'target_time': target_time,
                    })
                elif operation in {'correlate', 'compare'}:
                    if len(columns) != 2:
                        raise ResultInquiryError(f'{operation} 需要恰好两个列名')
                    data = tools.call(
                        'result.correlate' if operation == 'correlate' else 'result.compare',
                        {
                            'artifact_id': artifact_id,
                            'columns': columns,
                        } if operation != 'correlate' else {
                            'artifact_id': artifact_id,
                            'column_a': columns[0],
                            'column_b': columns[1],
                        },
                    )
                elif operation == 'topsis':
                    data = tools.call('result.topsis', {
                        'artifact_id': artifact_id,
                        'limit': int(getattr(query, 'limit', 10)),
                    })
                else:
                    raise ResultInquiryError(f'不支持的查询 {operation}')
                if hasattr(data, 'model_dump'):
                    data = data.model_dump(by_alias=True, mode='json')
            except (ResultInquiryError, ToolExecutionError) as exc:
                results.append({**base, 'error': str(exc)})
                continue
            results.append({**base, 'data': data})
        return results

    def _collect_inquiry_artifact_ids(self, run: dict[str, Any]) -> list[str]:
        """收集运行白名单和旧 Job 上挂载的制品 ID。"""

        artifact_ids = list(run.get('artifactIds') or [])
        # 兼容阶段六/七产生的旧 run：CSV 可能仍只挂在 Job 上。
        if run.get('jobId'):
            try:
                platform_store.refresh()
                job = platform_store.get_job(str(run['jobId']))
                artifact_ids.extend(
                    item['artifactId']
                    for item in job.model_dump(by_alias=True, mode='json').get('artifacts', [])
                    if item.get('artifactId')
                )
            except (AttributeError, KeyError, TypeError):
                pass
        return list(dict.fromkeys(str(artifact_id) for artifact_id in artifact_ids))

    def _load_result_catalog(self, run: dict[str, Any]) -> dict[str, Any] | None:
        """读取并验证当前运行的统一结果目录；旧运行没有目录时返回 None。"""

        artifact_ids = self._collect_inquiry_artifact_ids(run)
        catalog_record = None
        catalog_id = None
        records: dict[str, Any] = {}
        for artifact_id in artifact_ids:
            try:
                record = platform_store.get_artifact(artifact_id)
            except (KeyError, HTTPException):
                continue
            records[artifact_id] = record
            if getattr(record.artifact, 'name', None) == 'result_catalog.json':
                catalog_record = record
                catalog_id = artifact_id
        if catalog_record is None or catalog_id is None:
            return None
        artifact = catalog_record.artifact
        if getattr(artifact, 'kind', None) != 'JSON_SUMMARY':
            raise ResultInquiryError('结果目录制品类型无效')
        content = bytes(catalog_record.content)
        if sha256(content).hexdigest() != str(getattr(artifact, 'sha256', '')):
            raise ResultInquiryError('结果目录内容 SHA256 与登记元数据不一致')
        try:
            catalog = json.loads(content.decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ResultInquiryError('结果目录不是有效 JSON') from exc
        if not isinstance(catalog, dict):
            raise ResultInquiryError('结果目录结构无效')
        if (
            catalog.get('schemaVersion') != '1.0'
            or not isinstance(catalog.get('runId'), str)
            or not catalog.get('runId')
            or catalog.get('verified') is not True
        ):
            raise ResultInquiryError('结果目录未通过结构化校验')
        raw_entries = catalog.get('entries')
        if not isinstance(raw_entries, list) or catalog.get('entryCount') != len(raw_entries):
            raise ResultInquiryError('结果目录条目数量不一致')
        # 旧版求解器会把内部索引文件写入目录，但它们不是用户结果，也未登记为
        # 可查询制品。读取旧目录时兼容性忽略 index.csv；新目录生成时则直接排除。
        entries = []
        for entry in raw_entries:
            if isinstance(entry, dict) and Path(str(entry.get('artifactPath') or '')).name.lower() == 'index.csv':
                continue
            entries.append(entry)
        by_path: dict[str, dict[str, Any]] = {}
        by_artifact_id: dict[str, dict[str, Any]] = {}
        for entry in entries:
            if not isinstance(entry, dict):
                raise ResultInquiryError('结果目录包含无效条目')
            path = str(entry.get('artifactPath') or '')
            path_parts = Path(path).parts
            columns = entry.get('columns')
            units = entry.get('units')
            source_sha = str(entry.get('sha256') or '')
            valid_columns = (
                isinstance(columns, list)
                and bool(columns)
                and all(isinstance(column, str) and bool(column) for column in columns)
            )
            if (
                not path
                or Path(path).is_absolute()
                or '..' in path_parts
                or not valid_columns
                or len(set(columns)) != len(columns)
                or not isinstance(units, dict)
                or any(column not in units or not str(units[column]) for column in columns)
                or len(source_sha) != 64
                or entry.get('verified') is not True
                or path in by_path
            ):
                raise ResultInquiryError('结果目录条目未通过路径、列或单位校验')
            source_id = next(
                (
                    artifact_id
                    for artifact_id, record in records.items()
                    if str(getattr(record.artifact, 'path', '') or '') == path
                ),
                None,
            )
            if source_id is None:
                raise ResultInquiryError(f'目录来源制品不存在: {path}')
            source = records[source_id].artifact
            if getattr(source, 'kind', None) not in {'CSV_TIMESERIES', 'CSV_TABLE'}:
                raise ResultInquiryError(f'目录来源不是 CSV 制品: {path}')
            if str(getattr(source, 'sha256', '')) != source_sha:
                raise ResultInquiryError(f'目录来源 SHA256 不一致: {path}')
            actual_columns = ResultInquiryService(platform_store).columns(source_id)
            if actual_columns != [str(column) for column in columns]:
                raise ResultInquiryError(f'目录列定义与制品不一致: {path}')
            normalized = {
                'artifactId': source_id,
                'path': path,
                'columns': list(actual_columns),
                'units': {str(key): str(value) for key, value in units.items()},
                'sha256': source_sha,
            }
            by_path[path] = normalized
            by_artifact_id[source_id] = normalized
        return {
            'artifactId': catalog_id,
            'schemaVersion': str(catalog['schemaVersion']),
            'entryCount': len(entries),
            'entries': list(by_path.values()),
            'byArtifactId': by_artifact_id,
        }

    def _inquiry_artifacts(self, run: dict[str, Any]) -> dict[str, str]:
        """把 run 的 artifactIds 映射成 name → artifactId。"""
        artifact_ids = self._collect_inquiry_artifact_ids(run)
        result_catalog = self._load_result_catalog(run)
        catalog_sources = {
            entry['artifactId']: entry
            for entry in (result_catalog['entries'] if result_catalog is not None else [])
        }
        records: list[tuple[str, str, str]] = []
        for artifact_id in dict.fromkeys(artifact_ids):
            try:
                record = platform_store.get_artifact(artifact_id)
            except (KeyError, HTTPException):
                continue
            name = str(record.artifact.name)
            is_topsis_summary = name in {'real_optimization_summary.json', 'optimization_summary.json'}
            if result_catalog is not None and artifact_id not in catalog_sources and not is_topsis_summary:
                continue
            path = str(getattr(record.artifact, 'path', '') or '')
            parent = Path(path).parent.name
            scoped_name = f'{parent}/{name}' if parent and parent != '.' else name
            records.append((name, scoped_name, artifact_id))
        counts: dict[str, int] = {}
        for name, _scoped_name, _artifact_id in records:
            counts[name] = counts.get(name, 0) + 1
        mapping: dict[str, str] = {}
        for name, scoped_name, artifact_id in records:
            mapping[scoped_name if counts[name] > 1 else name] = artifact_id
        return mapping

    def _safe_explain_inquiry(self, **kwargs: Any) -> NarrativeResult:
        fallback = '已查询到相关结果数据，但暂时无法生成可靠解释。'
        try:
            explain = getattr(self.planner, 'explain_inquiry')
            result = explain(**kwargs)
            if isinstance(result, NarrativeResult):
                return result
            return NarrativeResult(
                narrativeMode=str(result.narrative_mode),
                text=str(result.text),
                fallbackReason=result.fallback_reason,
            )
        except Exception:
            return NarrativeResult(
                narrativeMode='TEMPLATE_FALLBACK',
                text=fallback,
                fallbackReason='LLM_INQUIRY_EXCEPTION',
            )

    def _resolve_clarification(
        self,
        repository: AgentRepository,
        session: dict[str, Any],
        run: dict[str, Any],
        content: str,
        now: str,
        load_import: dict[str, Any] | None,
        *,
        intent_override: Any | None = None,
        planner_mode_override: str | None = None,
    ) -> dict[str, Any]:
        requested_task = str(run.get('taskType') or '')
        if requested_task not in ENGINEERING_TASK_TYPES:
            return self._create_legacy_load_run(
                repository,
                session,
                content,
                now,
                load_import,
            )
        if run.get('runtimeMode') == 'WORKFLOW_HARNESS' and run.get('workflowSnapshot'):
            requested_spec = engineering_task_spec(requested_task)
            plan_tool = requested_spec.plan_tool if requested_spec else 'analysis.plan'
            # 只读当前游标；规划器失败时在此之前不写 run，保证澄清可重试。
            try:
                WorkflowGuard().authorize(
                    workflow_snapshot=run['workflowSnapshot'],
                    current_step=str(run.get('currentStep') or 'REQUIREMENTS'),
                    completed_steps=run.get('completedSteps') or [],
                    tool_call=WorkflowToolCall(name=plan_tool),
                )
            except ToolExecutionError as exc:
                return self._workflow_guard_reply(repository, session, run, exc)
        prior_goal = str(run.get('goal') or '')
        planner_result = (
            SimpleNamespace(intent=intent_override, planner_mode=planner_mode_override or 'LLM_TOOL_CALL')
            if intent_override is not None
            else self.planner.plan_engineering_clarification(
                content,
                prior_intent=dict(run.get('intent') or {}),
                prior_goal=prior_goal,
                requested_task=requested_task,
                has_file=load_import is not None,
                attachment_summary=load_import.get('inspection') if load_import else None,
            )
        )
        intent = planner_result.intent
        clarification_question = (
            str(intent.summary)
            if intent_override is not None
            else self._clarification_question(
                intent=intent,
                goal=f'{prior_goal}\n{content}' if prior_goal else content,
            )
            if intent.missing_fields
            else str(intent.summary)
        )
        contract_task = requested_task if intent.task_type == 'CLARIFICATION' else intent.task_type
        contract: dict[str, Any] = {}
        if not intent.missing_fields:
            if contract_task == 'DAMPER_COMPARISON':
                contract = build_damper_comparison_contract(
                    solver=intent.solver,
                    damper_types=intent.damper_types,
                    response_ids=intent.response_ids,
                    selected_layout_id=intent.selected_layout_id or 'TWO_PER_TOWER',
                    field_sources={
                        'solver': 'USER_SPECIFIED',
                        'loadKind': 'USER_SPECIFIED' if intent.load_kind else 'DEFAULT',
                        'responseIds': 'USER_SPECIFIED' if intent.response_ids else 'DEFAULT',
                        'budget': 'DEFAULT',
                        'selectedLayoutId': 'USER_SPECIFIED' if intent.selected_layout_id else 'DEFAULT',
                    },
                )
            elif contract_task == 'DAMPER_PARAMETER_SWEEP':
                contract = build_parameter_sweep_contract(
                    solver=intent.solver,
                    cases=[case.model_dump(by_alias=True) for case in intent.cases],
                    response_ids=list(intent.response_ids),
                    selected_layout_id=intent.selected_layout_id or 'TWO_PER_TOWER',
                    load_kind=intent.load_kind or 'EARTHQUAKE',
                    max_concurrent_cases=intent.max_concurrent_cases,
                )
            else:
                contract = build_engineering_contract(
                    task_type=contract_task,
                    solver=intent.solver,
                    damper_type=intent.damper_type,
                    response_ids=intent.response_ids,
                    selected_layout_id=intent.selected_layout_id if intent.damper_type else None,
                    load_kind=intent.load_kind or 'EARTHQUAKE',
                    field_sources={
                        'solver': 'USER_SPECIFIED',
                        'loadKind': 'USER_SPECIFIED' if intent.load_kind else 'DEFAULT',
                        'responseIds': 'USER_SPECIFIED' if intent.response_ids else 'DEFAULT',
                        'budget': 'DEFAULT',
                        **({'selectedLayoutId': 'USER_SPECIFIED' if intent.selected_layout_id else 'DEFAULT'} if intent.damper_type else {}),
                    },
                )
        run.update({
            'goal': f'{prior_goal}\n{content}' if prior_goal else content,
            'taskType': contract_task,
            'status': 'NEEDS_CLARIFICATION' if intent.missing_fields else (
                'WAITING_MAPPING' if load_import else 'PLANNING'
            ),
            'currentStage': 'CLARIFICATION' if intent.missing_fields else (
                'LOAD_MAPPING' if load_import else 'PLANNING'
            ),
            'importId': load_import['importId'] if load_import else run.get('importId'),
            'artifactIds': (
                [load_import['fileArtifactId']] if load_import
                else list(run.get('artifactIds') or [])
            ),
            'plannerMode': planner_result.planner_mode,
            'intent': intent.model_dump(by_alias=True),
            'workflowContract': contract,
            'resultSummary': {
                'message': clarification_question,
            },
            'pendingApprovalId': None,
            'updatedAt': utc_now(),
        })
        repository.save_run(run)
        repository.save_step({
            'stepId': gen_id('step'),
            'runId': run['runId'],
            'idempotencyKey': f'{run["runId"]}:CLARIFICATION:{sha256(content.encode("utf-8")).hexdigest()}',
            'title': '合并用户澄清信息',
            'status': 'SUCCEEDED',
            'createdAt': utc_now(),
        })
        if not intent.missing_fields and load_import is None:
            template_mapping = {'loadKind': intent.load_kind or 'EARTHQUAKE', 'channels': []}
            if contract_task == 'DAMPER_PARAMETER_SWEEP':
                self._prepare_damper_parameter_sweep_approval(
                    repository,
                    run,
                    mapping=template_mapping,
                    standard_artifact_id=None,
                    standard_sha256=None,
                )
                legacy_prepare = None
            else:
                legacy_prepare = getattr(self, f'_prepare_engineering_{"optimization" if contract_task == "DAMPER_OPTIMIZATION" else "analysis"}_approval', None)
            if contract_task != 'DAMPER_PARAMETER_SWEEP':
                legacy_function = getattr(legacy_prepare, '__func__', None)
                if legacy_function is not getattr(type(self), '_prepare_engineering_optimization_approval', None) and contract_task == 'DAMPER_OPTIMIZATION':
                    legacy_prepare(
                        repository,
                        run,
                        mapping=template_mapping,
                        standard_artifact_id=None,
                        standard_sha256=None,
                    )
                else:
                    self._prepare_agent_approval(
                        repository,
                        run,
                        self._agent_for(contract_task),
                        mapping=template_mapping,
                        standard_artifact_id=None,
                        standard_sha256=None,
                    )
            repository.save_run(run)
        approval = repository.get_approval(run.get('pendingApprovalId')) if run.get('pendingApprovalId') else None
        if approval:
            self._record_approval_message(repository, run, approval)
            clarification_question = str(approval.get('summary') or clarification_question)
        if not approval:
            repository.add_message({
                'messageId': gen_id('msg'),
                'sessionId': session['sessionId'],
                'role': 'ASSISTANT',
                'content': clarification_question,
                'runId': run['runId'],
                'createdAt': utc_now(),
            })
        session['updatedAt'] = utc_now()
        repository.save_session(session)
        return self._decorate_run(run)

    def _create_legacy_load_run(
        self,
        repository: AgentRepository,
        session: dict[str, Any],
        content: str,
        now: str,
        load_import: dict[str, Any] | None,
        *,
        route_evidence: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        run = {
            'runId': gen_id('agr'),
            'sessionId': session['sessionId'],
            'goal': content,
            'taskType': 'LOAD_IMPORT',
            'status': 'WAITING_MAPPING' if load_import else 'WAITING_FILE',
            'currentStage': 'LOAD_MAPPING' if load_import else 'LOAD_IMPORT',
            'importId': load_import['importId'] if load_import else None,
            'artifactIds': [load_import['fileArtifactId']] if load_import else [],
            'jobId': None,
            **({'routeEvidence': route_evidence} if route_evidence else {}),
            'createdAt': now,
            'updatedAt': now,
        }
        repository.save_run(run)
        assistant_content = (
            '文件检查已完成，请确认时间列、数值列、单位、方向和作用对象。'
            if load_import else '请先上传 CSV、TXT 或 XLSX 荷载文件。'
        )
        repository.add_message({
            'messageId': gen_id('msg'),
            'sessionId': session['sessionId'],
            'role': 'ASSISTANT',
            'content': assistant_content,
            'runId': run['runId'],
            'createdAt': utc_now(),
        })
        session['updatedAt'] = utc_now()
        repository.save_session(session)
        return self._decorate_run(run)

    def _create_unsupported_run(
        self,
        repository: AgentRepository,
        session: dict[str, Any],
        content: str,
        now: str,
        *,
        route_evidence: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        message = self.planner.respond_conversationally(
            message=content,
            intent='CAPABILITY_QUERY',
            capability_facts=build_capability_facts(),
        )
        run = {
            'runId': gen_id('agr'),
            'sessionId': session['sessionId'],
            'goal': content,
            'taskType': 'UNSUPPORTED',
            'status': 'UNSUPPORTED',
            'currentStage': 'UNSUPPORTED',
            'importId': None,
            'artifactIds': [],
            'jobId': None,
            'resultSummary': {'message': message},
            **({'routeEvidence': route_evidence} if route_evidence else {}),
            'createdAt': now,
            'updatedAt': now,
        }
        repository.save_run(run)
        repository.add_message({
            'messageId': gen_id('msg'),
            'sessionId': session['sessionId'],
            'role': 'ASSISTANT',
            'content': message,
            'runId': run['runId'],
            'createdAt': utc_now(),
        })
        session['updatedAt'] = utc_now()
        repository.save_session(session)
        return self._decorate_run(run)
