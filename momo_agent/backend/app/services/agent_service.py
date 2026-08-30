from __future__ import annotations

import json
import math
from contextlib import nullcontext
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from typing import Any, Callable

from fastapi import HTTPException
from pyansys_bridge.optimization.config_runner import preflight_config

from app.agents.analysis import (
    ANALYSIS_TRAFFIC_TARGET_SET_ID,
    ANALYSIS_WIND_TARGET_SET_ID,
    AnalysisAgent,
    AnalysisRuntimeTools,
)
from app.agents.core import AgentContext, AgentState, AgentStateMachine, RepositorySessionMemory
from app.agents.damper_comparison import DamperComparisonAgent
from app.agents.damper_parameter_sweep import DamperParameterSweepAgent
from app.agents.damper_optimization import (
    DamperOptimizationAgent,
    FULL_OPTIMIZATION_CONTRACT,
    FULL_OPTIMIZATION_PLAN,
)
from app.agents.engineering import EngineeringAgent, PreparedApproval, ReviewOutcome
from app.agents.task_registry import engineering_task_spec, report_file_name
from app.agents.tools import ToolExecutionError
from app.core.exceptions import LLMUnavailableError
from app.core.engineering_limits import (
    DOE_INITIAL_MAX,
    DOE_INITIAL_MIN,
)
from app.core.logging_config import get_platform_logger
from app.services.agent_engineering import ENGINEERING_TASK_TYPES, STBRIDGE_LAYOUTS
from app.services.agent_task_handlers import (
    ApprovalUpdateContext,
    orchestration_handler,
    orchestration_handler_or_generic,
)
from app.services.agent_figure_service import AgentFigureService
from app.services.agent_evidence import (
    build_solver_version_profile,
)
from app.services.agent_capabilities import build_capability_facts
from app.services.agent_conversation import AgentConversationMixin
from app.services.agent_harness import WorkflowHarnessMixin
from app.services.agent_llm import (
    NarrativeResult,
    llm_planner,
    message_telemetry_summary,
    message_time_budget,
)
from app.services.agent_repository import AgentRepository, DEFAULT_OWNER, run_state_lock
from app.services.load_import_service import load_import_service
from app.services.load_artifact_service import load_artifact_service
from app.services.load_mapping_inference import (
    DECISION_AUTO,
    infer_traffic_mapping,
    infer_wind_mapping,
)
from app.services.load_mapping_llm import refine_suggestion
from app.services.platform_dispatcher import platform_dispatcher
from app.services.platform_readiness import build_readiness_report
from app.services.platform_store import (
    AGENT_LOAD_TARGET_SETS,
    AGENT_TRAFFIC_FORCE_COMPONENT,
    AGENT_WIND_FORCE_COMPONENT,
    OPTIMIZATION_OVERVIEW_ARTIFACT_NAMES,
    gen_id,
    platform_store,
    utc_now,
)
from app.services.result_inquiry import ResultInquiryError, ResultInquiryService


logger = get_platform_logger('agent_service')


def _coerce_floats(value: Any) -> Any:
    """递归把非有限浮点转成 None，其余 JSON 值保持原样。"""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {key: _coerce_floats(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_coerce_floats(item) for item in value]
    return value


def _json_artifact_payload(value: Any) -> tuple[Any, bytes]:
    """返回同一份 JSON 安全预览和严格 JSON 字节。"""
    safe_value = _coerce_floats(value)
    content = json.dumps(
        safe_value,
        ensure_ascii=False,
        indent=2,
        allow_nan=False,
    ).encode('utf-8')
    return safe_value, content


class AgentService(WorkflowHarnessMixin, AgentConversationMixin):
    planner = llm_planner
    state_machine = AgentStateMachine()

    def repository(self) -> AgentRepository:
        return AgentRepository(platform_store.state_path)

    def _validate_run_transition(self, run: dict[str, Any], target: str) -> None:
        """在落库前校验已知工程运行状态的关键转换。"""
        try:
            current_state = AgentState(str(run.get('status')))
            target_state = AgentState(target)
        except ValueError:
            # 兼容旧数据或非运行状态字段；新状态由状态机覆盖。
            return
        self.state_machine.transition(current_state, target_state)

    def _agents(self) -> dict[str, EngineeringAgent]:
        """返回工程任务注册表；Agent 不持有持久化生命周期。"""
        repository = self.repository()
        memory = RepositorySessionMemory(getattr(repository, 'list_messages', lambda _session_id: []))
        return {
            'ANALYSIS': AnalysisAgent(
                planner=self.planner,
                memory=memory,
                store=platform_store,
                dispatcher=platform_dispatcher,
                figure_service=AgentFigureService(platform_store),
                readiness_builder=build_readiness_report,
                preflight_runner=preflight_config,
                solver_profile_builder=build_solver_version_profile,
            ),
            'DAMPER_OPTIMIZATION': DamperOptimizationAgent(
                planner=self.planner,
                store=platform_store,
                dispatcher=platform_dispatcher,
                readiness_builder=build_readiness_report,
                preflight_runner=preflight_config,
                solver_profile_builder=build_solver_version_profile,
            ),
            'DAMPER_COMPARISON': DamperComparisonAgent(
                planner=self.planner,
                store=platform_store,
                dispatcher=platform_dispatcher,
                readiness_builder=build_readiness_report,
                preflight_runner=preflight_config,
                solver_profile_builder=build_solver_version_profile,
            ),
            'DAMPER_PARAMETER_SWEEP': DamperParameterSweepAgent(
                planner=self.planner,
                store=platform_store,
                dispatcher=platform_dispatcher,
                readiness_builder=build_readiness_report,
                preflight_runner=preflight_config,
                solver_profile_builder=build_solver_version_profile,
            ),
        }

    def _agent_for(self, task_type: str) -> EngineeringAgent:
        spec = engineering_task_spec(task_type)
        try:
            return self._agents()[spec.agent_key if spec else task_type]
        except KeyError as exc:
            raise HTTPException(
                status_code=422,
                detail={'code': 'UNSUPPORTED_TASK_TYPE', 'message': task_type},
            ) from exc

    @staticmethod
    def _required_report_file_name(task_type: str) -> str:
        name = report_file_name(task_type)
        if not name:
            raise HTTPException(
                status_code=422,
                detail={'code': 'UNSUPPORTED_TASK_TYPE', 'message': task_type},
            )
        return name

    def _analysis_runtime_tools(self) -> AnalysisRuntimeTools:
        return self._agent_for('ANALYSIS').tools  # type: ignore[return-value]

    @staticmethod
    def _ensure_owner(
        record: dict[str, Any],
        owner: str | None,
        *,
        code: str,
        message: str,
    ) -> None:
        """给出 owner 时校验记录归属；用 404 掩蔽存在性，防止跨用户探测。

        owner 为 None 表示服务内部调用（信任边界之内），不做校验；
        历史数据没有 ownerId 字段，统一视为 DEFAULT_OWNER。
        """
        if owner is None:
            return
        if str(record.get('ownerId') or DEFAULT_OWNER) != owner:
            raise HTTPException(status_code=404, detail={'code': code, 'message': message})

    def _ensure_run_owner(self, run_id: str, owner: str | None) -> None:
        if owner is None:
            return
        run = self._required(
            self.repository().get_run(run_id),
            'RUN_NOT_FOUND',
            f'运行 {run_id} 不存在',
        )
        self._ensure_owner(run, owner, code='RUN_NOT_FOUND', message=f'运行 {run_id} 不存在')

    def create_session(self, title: str, owner: str = DEFAULT_OWNER) -> dict[str, Any]:
        now = utc_now()
        session = {
            'sessionId': gen_id('ags'),
            'ownerId': owner,
            'title': title,
            'status': 'ACTIVE',
            'createdAt': now,
            'updatedAt': now,
        }
        self.repository().save_session(session)
        return session

    def list_sessions(self, owner: str | None = None) -> list[dict[str, Any]]:
        return self.repository().list_sessions(owner=owner)

    def delete_session(self, session_id: str, owner: str | None = None) -> dict[str, Any]:
        repository = self.repository()
        session = self._required(
            repository.get_session(session_id),
            'SESSION_NOT_FOUND',
            f'会话 {session_id} 不存在',
        )
        self._ensure_owner(session, owner, code='SESSION_NOT_FOUND', message=f'会话 {session_id} 不存在')
        runs = repository.list_runs(session_id)
        terminal_statuses = {
            'SUCCEEDED', 'COMPLETED_DIAGNOSTIC', 'FAILED', 'CANCELLED', 'UNSUPPORTED',
        }
        active_runs = [
            {'runId': run.get('runId'), 'status': run.get('status')}
            for run in runs
            if run.get('status') not in terminal_statuses
        ]
        cancelled_run_count = 0
        for active_run in active_runs:
            run_id = str(active_run.get('runId') or '')
            if not run_id:
                continue
            self.cancel_run(run_id)
            cancelled_run_count += 1
        deleted = repository.delete_session_history(session_id)
        if not deleted:
            raise HTTPException(
                status_code=404,
                detail={'code': 'SESSION_NOT_FOUND', 'message': f'会话 {session_id} 不存在'},
            )
        return {
            'sessionId': session_id,
            'deleted': True,
            'retainedRunCount': len(runs),
            'cancelledRunCount': cancelled_run_count,
        }

    def get_session(self, session_id: str, owner: str | None = None) -> dict[str, Any]:
        repository = self.repository()
        session = self._required(repository.get_session(session_id), 'SESSION_NOT_FOUND', f'会话 {session_id} 不存在')
        self._ensure_owner(session, owner, code='SESSION_NOT_FOUND', message=f'会话 {session_id} 不存在')
        runs = [
            self._with_inquiry_projection(self._with_result_metadata(run))
            for run in repository.list_runs(session_id)
        ]
        visible_inquiry_messages = {
            str(run.get('runId')): str((run.get('resultSummary') or {}).get('message') or '')
            for run in runs
            if run.get('taskType') == 'INQUIRY'
        }
        messages = []
        for message in repository.list_messages(session_id):
            if message.get('messageType') in {'HARNESS_TOOL_CALL', 'HARNESS_TOOL_RESULT'}:
                # 原生协议消息仅供 Harness 恢复上下文，不在用户聊天窗口重复展示。
                continue
            approval_id = message.get('approvalId')
            approval = repository.get_approval(approval_id) if approval_id else None
            inquiry_message = visible_inquiry_messages.get(str(message.get('runId') or ''))
            messages.append({
                **message,
                **({'content': inquiry_message} if inquiry_message else {}),
                **({'approval': approval} if approval else {}),
            })
        existing_approval_ids = {
            message.get('approvalId') for message in messages if message.get('approvalId')
        }
        list_approvals = getattr(repository, 'list_approvals', None)
        approvals = list_approvals({run.get('runId') for run in runs}) if callable(list_approvals) else []
        for approval in approvals:
            approval_id = approval.get('approvalId')
            if not approval_id or approval_id in existing_approval_ids:
                continue
            messages.append({
                'messageId': f'approval_msg_{approval_id}',
                'sessionId': session_id,
                'role': 'ASSISTANT',
                'messageType': 'APPROVAL',
                'approvalId': approval_id,
                'content': str(approval.get('summary') or '请确认是否执行该工程计划。'),
                'runId': approval.get('runId'),
                'createdAt': approval.get('createdAt') or approval.get('updatedAt') or '',
                'approval': approval,
            })
        messages.sort(key=lambda message: str(message.get('createdAt') or ''))
        return {
            **session,
            'messages': messages,
            'runs': runs,
        }

    def upload_file(self, file_name: str, content: bytes) -> dict[str, Any]:
        uploaded = load_artifact_service.upload(file_name, content)
        file_id = gen_id('file')
        import_id = gen_id('loadimp')
        now = utc_now()
        payload = {
            'importId': import_id,
            'fileId': file_id,
            'fileArtifactId': uploaded['artifactId'],
            'fileName': file_name,
            'sourceSha256': uploaded['sha256'],
            'status': 'INSPECTED',
            'inspection': uploaded['inspection'],
            'createdAt': now,
            'updatedAt': now,
        }
        self.repository().save_import(payload)
        return payload

    def get_import(self, import_id: str) -> dict[str, Any]:
        return self._required(
            self.repository().get_import(import_id),
            'LOAD_IMPORT_NOT_FOUND',
            f'荷载导入 {import_id} 不存在',
        )

    def create_message(
        self,
        session_id: str,
        content: str,
        file_id: str | None,
        task_type: str = 'AUTO',
        *,
        owner: str | None = None,
        event_sink: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        repository = self.repository()
        session = self._required(repository.get_session(session_id), 'SESSION_NOT_FOUND', f'会话 {session_id} 不存在')
        self._ensure_owner(session, owner, code='SESSION_NOT_FOUND', message=f'会话 {session_id} 不存在')
        load_import = repository.find_import_by_file(file_id) if file_id else None
        if file_id and load_import is None:
            raise HTTPException(status_code=404, detail={'code': 'LOAD_FILE_NOT_FOUND', 'message': f'文件 {file_id} 不存在'})
        now = utc_now()
        repository.add_message({
            'messageId': gen_id('msg'),
            'sessionId': session['sessionId'],
            'role': 'USER',
            'content': content,
            'fileId': file_id,
            'createdAt': now,
        })
        # 单条消息的全部链式 LLM 调用共享一个总时间预算；
        # 超预算按 LLMUnavailableError 走结构化失败，不再无限占用工作线程。
        # 失败路径也在预算上下文内处理，保证遥测汇总覆盖失败消息。
        with message_time_budget():
            try:
                run = self._dispatch_message(
                    repository,
                    session,
                    content,
                    now,
                    load_import,
                    task_type,
                    event_sink=event_sink,
                )
            except LLMUnavailableError as exc:
                run = self._create_llm_unavailable_run(repository, session, content, now, exc)
            except ToolExecutionError as exc:
                run = self._create_harness_failure_run(
                    repository,
                    session,
                    content,
                    now,
                    code=exc.code,
                    message=exc.message,
                    details=exc.details,
                )
            self._attach_message_telemetry(repository, run)
            return run

    def _attach_message_telemetry(
        self,
        repository: AgentRepository,
        run: dict[str, Any] | None,
    ) -> None:
        """把本条消息的 LLM 遥测汇总合并进 run 的累计遥测；尽力而为，不阻断消息流。"""
        summary = message_telemetry_summary()
        if not summary or not isinstance(run, dict):
            return
        run_id = run.get('runId')
        if not run_id:
            return
        try:
            with run_state_lock(run_id):
                stored = repository.get_run(run_id)
                target = stored if stored is not None else run
                target['llmTelemetry'] = self._merged_run_telemetry(target.get('llmTelemetry'), summary)
                if stored is not None:
                    repository.save_run(stored)
                    run['llmTelemetry'] = stored['llmTelemetry']
        except Exception:
            logger.warning('写入 run %s 的 LLM 遥测失败', run_id, exc_info=True)

    @staticmethod
    def _merged_run_telemetry(
        existing: dict[str, Any] | None,
        message_summary: dict[str, Any],
    ) -> dict[str, Any]:
        totals = dict((existing or {}).get('totals') or {})
        totals['messageCount'] = int(totals.get('messageCount') or 0) + 1
        totals['llmCallCount'] = int(totals.get('llmCallCount') or 0) + message_summary['llmCallCount']
        totals['failedCallCount'] = int(totals.get('failedCallCount') or 0) + message_summary['failedCallCount']
        totals['llmTimeS'] = round(float(totals.get('llmTimeS') or 0.0) + message_summary['llmTimeS'], 3)
        totals['promptTokens'] = int(totals.get('promptTokens') or 0) + message_summary['promptTokens']
        totals['completionTokens'] = int(totals.get('completionTokens') or 0) + message_summary['completionTokens']
        totals['cachedTokens'] = int(totals.get('cachedTokens') or 0) + message_summary['cachedTokens']
        totals['compressionCount'] = (
            int(totals.get('compressionCount') or 0) + int(message_summary.get('compressionCount') or 0)
        )
        if totals['promptTokens'] > 0:
            totals['kvCacheHitRatio'] = round(totals['cachedTokens'] / totals['promptTokens'], 4)
        return {'lastMessage': message_summary, 'totals': totals}


    def _create_engineering_run(
        self,
        repository: AgentRepository,
        session: dict[str, Any],
        content: str,
        now: str,
        *,
        requested_task: str,
        load_import: dict[str, Any] | None,
        route_evidence: dict[str, Any] | None = None,
        intent_override: Any | None = None,
    ) -> dict[str, Any]:
        agent_runtime = None
        if intent_override is not None:
            intent = intent_override
            planner_mode = 'LLM_TOOL_CALL'
            contract_task = requested_task if intent.task_type == 'CLARIFICATION' else intent.task_type
            if intent.missing_fields:
                contract = {}
            else:
                contract = orchestration_handler_or_generic(contract_task).build_contract_from_intent(
                    intent,
                    load_import=load_import,
                )
            if requested_task == 'ANALYSIS':
                agent_runtime = {
                    'name': 'MOMO_TYPED_AGENT',
                    'version': '1',
                    'recentMessageCount': 0,
                    'tools': ['analysis.preflight', 'analysis.dispatch', 'analysis.review'],
                }
        elif requested_task == 'ANALYSIS':
            analysis_plan = self._agent_for('ANALYSIS').plan(AgentContext(
                session_id=session['sessionId'],
                goal=content,
                requested_task='ANALYSIS',
                has_attachment=load_import is not None,
                attachment_summary=load_import.get('inspection') if load_import else None,
            ))
            intent = analysis_plan.intent
            planner_mode = analysis_plan.planner_mode
            contract_task = 'ANALYSIS'
            contract = analysis_plan.workflow_contract
            agent_runtime = {
                'name': 'MOMO_TYPED_AGENT',
                'version': '1',
                'recentMessageCount': analysis_plan.recent_message_count,
                'tools': ['analysis.preflight', 'analysis.dispatch', 'analysis.review'],
            }
        else:
            engineering_plan = self._agent_for(requested_task).plan(AgentContext(
                session_id=session['sessionId'],
                goal=content,
                requested_task=requested_task,
                has_attachment=load_import is not None,
                attachment_summary=load_import.get('inspection') if load_import else None,
            ))
            intent = engineering_plan.intent
            planner_mode = engineering_plan.planner_mode
            contract_task = requested_task if intent.task_type == 'CLARIFICATION' else intent.task_type
            contract = engineering_plan.workflow_contract
        clarification_question = (
            self._clarification_question(intent=intent, goal=content)
            if intent.missing_fields
            else str(intent.summary)
        )
        status = 'NEEDS_CLARIFICATION' if intent.missing_fields else (
            'WAITING_MAPPING' if load_import else 'PLANNING'
        )
        stage = 'CLARIFICATION' if intent.missing_fields else (
            'LOAD_MAPPING' if load_import else 'PLANNING'
        )
        run = {
            'runId': gen_id('agr'),
            'sessionId': session['sessionId'],
            'goal': content,
            'taskType': contract_task,
            'status': status,
            'currentStage': stage,
            'importId': load_import['importId'] if load_import else None,
            'artifactIds': [load_import['fileArtifactId']] if load_import else [],
            'jobId': None,
            'plannerMode': planner_mode,
            'intent': intent.model_dump(by_alias=True),
            'workflowContract': contract,
            'resultSummary': {
                'message': clarification_question,
            },
            **({'routeEvidence': route_evidence} if route_evidence else {}),
            **({'agentRuntime': agent_runtime} if agent_runtime else {}),
            'createdAt': now,
            'updatedAt': now,
        }
        if load_import:
            platform_store.claim_artifact_for_run(load_import['fileArtifactId'], run['runId'])
        repository.save_run(run)
        repository.save_step({
            'stepId': gen_id('step'),
            'runId': run['runId'],
            'idempotencyKey': f'{run["runId"]}:UNDERSTANDING',
            'title': '解析受控工程意图',
            'status': 'SUCCEEDED',
            'createdAt': utc_now(),
        })
        bundled_load = None
        if (
            not intent.missing_fields
            and load_import is None
            and contract_task in {'ANALYSIS', 'DAMPER_COMPARISON', 'DAMPER_PARAMETER_SWEEP', 'DAMPER_OPTIMIZATION'}
        ):
            load_kind = str((run.get('intent') or {}).get('loadKind') or 'EARTHQUAKE')
            if load_kind == 'EARTHQUAKE':
                bundled_load = self._provision_bundled_earthquake(repository, run)
            elif load_kind == 'WIND' and contract_task in {
                'ANALYSIS', 'DAMPER_COMPARISON', 'DAMPER_PARAMETER_SWEEP', 'DAMPER_OPTIMIZATION',
            }:
                bundled_load = self._provision_bundled_wind(repository, run)
            elif load_kind == 'TRAFFIC' and contract_task in {
                'ANALYSIS', 'DAMPER_COMPARISON', 'DAMPER_PARAMETER_SWEEP', 'DAMPER_OPTIMIZATION',
            }:
                # 车流四条链都已登记模板，与风同口径：不在这里补登记的话，
                # 阻尼器三链会因为没有荷载制品而失败关闭，失败原因指向"缺制品"
                # 而不是真实状态（能力已放行、只是没给荷载）。
                bundled_load = self._provision_bundled_traffic(repository, run)
        if not intent.missing_fields and load_import is None:
            template_mapping = (
                bundled_load['mapping']
                if bundled_load
                else {'loadKind': intent.load_kind or 'EARTHQUAKE', 'channels': []}
            )
            standard_artifact_id = bundled_load['standardArtifactId'] if bundled_load else None
            standard_sha256 = bundled_load['standardSha256'] if bundled_load else None
            # 准备审批的方法按名称解析：这些方法是测试与澄清合并流程
            # 依赖的可覆盖接缝，路由差异由任务编排 handler 声明。
            prepare_approval = getattr(
                self,
                orchestration_handler_or_generic(contract_task).prepare_approval_method,
            )
            prepare_approval(
                repository,
                run,
                mapping=template_mapping,
                standard_artifact_id=standard_artifact_id,
                standard_sha256=standard_sha256,
            )
            repository.save_run(run)
        auto_standardized = (
            self._try_auto_standardize(repository, run, load_import)
            if load_import and not intent.missing_fields
            else None
        )
        assistant_content = (
            clarification_question
            if intent.missing_fields
            else auto_standardized
            or '文件检查已完成，请确认时间、荷载通道、单位、方向和作用对象。'
            if load_import
            else str((repository.get_approval(run.get('pendingApprovalId')) or {}).get('summary') or (
                run.get('resultSummary') or {}
            ).get('message', '工程意图已解析，正在生成受控执行计划。'))
        )
        repository.add_message({
            'messageId': gen_id('msg'),
            'sessionId': session['sessionId'],
            'role': 'ASSISTANT',
            'content': assistant_content,
            'runId': run['runId'],
            **({'messageType': 'APPROVAL', 'approvalId': run.get('pendingApprovalId')} if run.get('pendingApprovalId') else {}),
            'createdAt': utc_now(),
        })
        session['updatedAt'] = utc_now()
        repository.save_session(session)
        return self._decorate_run(run)

    def _prepare_agent_approval(
        self,
        repository: AgentRepository,
        run: dict[str, Any],
        agent: EngineeringAgent,
        *,
        mapping: dict[str, Any],
        standard_artifact_id: str | None,
        standard_sha256: str | None,
    ) -> PreparedApproval:
        """把 Agent 的纯审批准备结果应用到跨请求 run 生命周期。"""
        prepared = agent.prepare_approval(
            run,
            mapping=mapping,
            standard_artifact_id=standard_artifact_id,
            standard_sha256=standard_sha256,
        )
        if prepared.preflight:
            run['preflight'] = prepared.preflight
        if prepared.contract_updates:
            contract = dict(run.get('workflowContract') or {})
            contract.update(prepared.contract_updates)
            run['workflowContract'] = contract
        if prepared.plan:
            run['plan'] = list(prepared.plan)
        if agent.task_type != 'ANALYSIS' and prepared.preflight:
            repository.save_step({
                'stepId': gen_id('step'),
                'runId': run['runId'],
                'idempotencyKey': f'{run["runId"]}:PREFLIGHT:{standard_sha256}',
                'title': '检查工程工作流与真实求解环境',
                'status': 'SUCCEEDED' if prepared.passed else 'FAILED',
                'createdAt': utc_now(),
            })
        if not prepared.passed:
            self._validate_run_transition(run, str(prepared.failure_status or 'FAILED'))
            run.update({
                'status': prepared.failure_status or 'FAILED',
                'currentStage': 'PREFLIGHT',
                'pendingApprovalId': None,
                'resultSummary': {
                    **(run.get('resultSummary') or {}),
                    'message': prepared.failure_message or '工程预检未通过，未生成执行审批。',
                },
                'updatedAt': utc_now(),
            })
            return prepared

        frozen_action = dict(prepared.frozen_action or {})
        command_artifacts = []
        if prepared.pending_command_streams:
            command_artifacts = self._register_pending_command_streams(
                run['runId'],
                prepared.pending_command_streams,
            )
            frozen_action['preExecutionCommandStreams'] = command_artifacts
            contract = dict(run.get('workflowContract') or {})
            contract['preExecutionCommandStreams'] = command_artifacts
            run['workflowContract'] = contract
        fallback_summary = prepared.approval_summary or '批准后执行冻结的真实工程 Job。'
        approval_narrative = self._safe_describe_pending_action(
            task_type=agent.task_type,
            approval_action=prepared.approval_action or agent.approval_action,
            facts=self._approval_facts(frozen_action, run.get('workflowContract') or {}),
            template_message=fallback_summary,
        )
        approval_summary = approval_narrative.text.rstrip()
        if not approval_summary.endswith('是否允许执行？'):
            approval_summary = f'{approval_summary.rstrip("。！？")}。是否允许执行？'
        approval = self._create_approval(
            run_id=run['runId'],
            action=prepared.approval_action or agent.approval_action,
            frozen_action=frozen_action,
            summary=approval_summary,
            narrative_mode=approval_narrative.narrative_mode,
            narrative_fallback_reason=approval_narrative.fallback_reason,
        )
        self._validate_run_transition(run, 'WAITING_APPROVAL')
        run.update({
            'status': 'WAITING_APPROVAL',
            'currentStage': 'SOLVER_APPROVAL' if agent.task_type == 'ANALYSIS' else 'WAITING_APPROVAL',
            'pendingApprovalId': approval['approvalId'],
            **prepared.extra_run_fields,
            **({'artifactIds': list(dict.fromkeys([
                *run.get('artifactIds', []),
                *(item['artifactId'] for item in command_artifacts),
            ]))} if command_artifacts else {}),
            'updatedAt': utc_now(),
        })
        return prepared

    def _register_pending_command_streams(
        self,
        run_id: str,
        streams: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """在冻结动作前注册 Agent 生成的命令流并返回完整证据引用。"""
        registered = []
        for stream in streams:
            command_text = str(stream.get('commandText') or '')
            content = command_text.encode('utf-8')
            artifact = platform_store.register_artifact(
                kind='COMMAND_STREAM',
                name=f'{stream["caseId"]}_pre_execution_damper_commands.apdl',
                path=f'output/platform_store/agent_runs/{run_id}/{stream["caseId"]}_pre_execution_damper_commands.apdl',
                mime_type='text/plain; charset=utf-8',
                preview={
                    'phase': stream.get('phase', 'PRE_EXECUTION'),
                    'damperType': stream.get('damperType'),
                    'solverModule': stream.get('solverModule'),
                    'commandText': command_text,
                },
                content=content,
            )
            registered.append({
                'artifactId': artifact.artifact_id,
                'name': artifact.name,
                'caseId': stream['caseId'],
                'damperType': stream['damperType'],
                'phase': stream.get('phase', 'PRE_EXECUTION'),
                'sha256': artifact.sha256,
            })
        return registered

    def _create_full_optimization_run(
        self,
        repository: AgentRepository,
        session: dict[str, Any],
        content: str,
        now: str,
        *,
        route_evidence: dict[str, Any] | None = None,
        intent_override: Any | None = None,
    ) -> dict[str, Any]:
        run = {
            'runId': gen_id('agr'),
            'sessionId': session['sessionId'],
            'goal': content,
            'taskType': 'FULL_OPTIMIZATION',
            'status': 'PLANNING',
            'currentStage': 'PLANNING',
            'artifactIds': [],
            'jobId': None,
            **({'routeEvidence': route_evidence} if route_evidence else {}),
            'createdAt': now,
            'updatedAt': now,
        }
        agent = self._agent_for('FULL_OPTIMIZATION')
        optimization_plan = agent.plan(AgentContext(
            session_id=session['sessionId'],
            goal=content,
            requested_task='FULL_OPTIMIZATION',
        )) if intent_override is None else None
        intent = optimization_plan.intent if optimization_plan is not None else intent_override
        run.update({
            'plannerMode': optimization_plan.planner_mode if optimization_plan is not None else 'LLM_TOOL_CALL',
            'intent': intent.model_dump(by_alias=True),
            'plan': list(optimization_plan.plan) if optimization_plan is not None else list(FULL_OPTIMIZATION_PLAN),
            'workflowContract': (
                dict(optimization_plan.workflow_contract)
                if optimization_plan is not None
                else dict(FULL_OPTIMIZATION_CONTRACT)
            ),
            'resultSummary': {
                'message': intent.summary,
            },
            'updatedAt': utc_now(),
        })
        repository.save_step({
            'stepId': gen_id('step'),
            'runId': run['runId'],
            'idempotencyKey': f'{run["runId"]}:PLANNING',
            'title': '生成受控工程计划',
            'status': 'SUCCEEDED',
            'createdAt': utc_now(),
        })
        if not DamperOptimizationAgent.is_supported_full_optimization_intent(run['intent']):
            run.update({
                'status': 'UNSUPPORTED',
                'currentStage': 'UNSUPPORTED',
                'resultSummary': {
                    **run['resultSummary'],
                    'message': '仅支持使用已验证模板的 ANSYS 地震与运营联合完整优化。',
                },
                'updatedAt': utc_now(),
            })
            assistant_content = run['resultSummary']['message']
        else:
            run.update({'status': 'PREFLIGHT', 'currentStage': 'PREFLIGHT', 'updatedAt': utc_now()})
            bundled_load = self._provision_bundled_earthquake(repository, run)
            prepared = self._prepare_agent_approval(
                repository,
                run,
                agent,
                mapping=bundled_load['mapping'],
                standard_artifact_id=bundled_load['standardArtifactId'],
                standard_sha256=bundled_load['standardSha256'],
            )
            if prepared.passed:
                approval = repository.get_approval(run.get('pendingApprovalId')) if run.get('pendingApprovalId') else None
                assistant_content = str((approval or {}).get('summary') or '真实运行环境与配置预检通过，请审查并一次批准整单计划。')
            else:
                assistant_content = run['resultSummary']['message']
        repository.save_run(run)
        repository.add_message({
            'messageId': gen_id('msg'),
            'sessionId': session['sessionId'],
            'role': 'ASSISTANT',
            'content': assistant_content,
            'runId': run['runId'],
            **({'messageType': 'APPROVAL', 'approvalId': run.get('pendingApprovalId')} if run.get('pendingApprovalId') else {}),
            'createdAt': utc_now(),
        })
        session['updatedAt'] = utc_now()
        repository.save_session(session)
        return self._decorate_run(run)

    def _try_auto_standardize(
        self,
        repository: AgentRepository,
        run: dict[str, Any],
        load_import: dict[str, Any],
    ) -> str | None:
        """文件自己声明了单位与时间轴时，跳过映射确认直接生成标准荷载。

        放行条件严格：单位必须是从文件内容里读出来的声明（文件头、列名，或
        LLM 读出散文声明后经原文与量级回验），时间轴必须是实测等步长递增列。
        任何一项靠猜都回到审批界面——猜错 g 与 m/s² 是 9.8 倍荷载误差，而且会
        带着一个完全合法的 SHA256 冻结值走完整条链。

        自动通过的只是"标准化"这一步。求解仍是独立的 RUN_SOLVER 审批，人工
        闸门没有被绕过。返回给用户看的说明文字，无法自动时返回 None。
        """

        inspection = dict(load_import.get('inspection') or {})
        load_kind = str((run.get('intent') or {}).get('loadKind') or 'EARTHQUAKE')
        try:
            _, rows = load_import_service.inspect(
                load_import['fileName'],
                platform_store.get_artifact(load_import['fileArtifactId']).content,
            )
            if load_kind == 'WIND':
                # inspect() 不知道工况类型，填的是地震形状的单通道加速度建议。逐节点
                # 风荷载要按目标集登记顺序重排成 channel_1..channel_N，只能在这里
                # 重算——工况类型只有 run.intent 里有。
                inspection['suggestedMapping'] = infer_wind_mapping(
                    inspection,
                    rows,
                    target_set_id=ANALYSIS_WIND_TARGET_SET_ID,
                    target_nodes=tuple(AGENT_LOAD_TARGET_SETS[ANALYSIS_WIND_TARGET_SET_ID]),
                    component=AGENT_WIND_FORCE_COMPONENT,
                    file_name=load_import['fileName'],
                ).to_dict()
            elif load_kind == 'TRAFFIC':
                # 车流同理，但形态不同：单通道稠密矩阵，逐节点绑定由标准化时一并
                # 产出的 mapping 制品声明，不靠列序。
                inspection['suggestedMapping'] = infer_traffic_mapping(
                    inspection,
                    rows,
                    target_set_id=ANALYSIS_TRAFFIC_TARGET_SET_ID,
                    target_nodes=tuple(AGENT_LOAD_TARGET_SETS[ANALYSIS_TRAFFIC_TARGET_SET_ID]),
                    component=AGENT_TRAFFIC_FORCE_COMPONENT,
                    file_name=load_import['fileName'],
                ).to_dict()
            refined = refine_suggestion(inspection, rows)
        except Exception:  # noqa: BLE001 - 见下方回退说明
            # 自动标准化是加速路径，不是必经路径；失败一律退回人工确认。
            logger.warning('自动标准化预判失败，回退人工映射确认', exc_info=True)
            return None

        load_import['suggestion'] = refined.to_dict()
        repository.save_import(load_import)
        if refined.standardize_decision != DECISION_AUTO:
            return None

        try:
            outcome = self._set_mapping_locked(
                load_import['importId'],
                {**refined.mapping, 'runId': run['runId']},
            )
            approval_id = str(outcome['approval']['approvalId'])
            self._decide_approval_locked(repository, approval_id, True)
        except HTTPException:
            logger.warning('自动标准化未通过校验，回退人工映射确认', exc_info=True)
            return None

        refreshed = repository.get_run(run['runId'])
        if refreshed is not None:
            run.clear()
            run.update(refreshed)
        channels = list(refined.mapping.get('channels') or [{}])
        channel = channels[0]
        # 逐节点风荷载的每个通道共用同一个输入单位，取第一个即可代表全表。
        standard_unit = 'N' if channel.get('quantity') == 'FORCE' else 'm/s²'
        if channel.get('applicationType') == 'NODAL_FORCE_MATRIX':
            # 车流只有一个通道，但它描述的是一整张稠密矩阵。说成"单通道"会让用户
            # 以为只装了一个节点，这里按矩阵的节点列数报。
            scope = f'{channel.get("matrixColumnCount")} 节点逐节点矩阵'
        elif len(channels) > 1:
            scope = f'{len(channels)} 列逐节点通道'
        else:
            scope = '单通道'
        evidence = '；'.join(refined.reasons[:3])
        return (
            f'文件已按自身声明自动转换为标准荷载：{scope}，输入单位 {channel.get("sourceUnit")}，'
            f'统一输出 {standard_unit}。依据：{evidence}。'
            '如与预期不符，请在下一步审批前告知，我会重做映射。'
        )

    def set_mapping(self, import_id: str, mapping: dict[str, Any]) -> dict[str, Any]:
        with run_state_lock(str(mapping.get('runId') or '')):
            return self._set_mapping_locked(import_id, mapping)

    def _set_mapping_locked(self, import_id: str, mapping: dict[str, Any]) -> dict[str, Any]:
        repository = self.repository()
        load_import = self._required(repository.get_import(import_id), 'LOAD_IMPORT_NOT_FOUND', f'荷载导入 {import_id} 不存在')
        run = self._required(repository.get_run(mapping['runId']), 'RUN_NOT_FOUND', f'运行 {mapping["runId"]} 不存在')
        if run.get('importId') != import_id:
            raise HTTPException(status_code=422, detail={'code': 'IMPORT_RUN_MISMATCH', 'message': '荷载导入不属于该运行'})
        if run.get('pendingApprovalId'):
            previous = repository.get_approval(run['pendingApprovalId'])
            if previous and previous['status'] == 'PENDING':
                previous.update({'status': 'SUPERSEDED', 'updatedAt': utc_now()})
                repository.save_approval(previous)
        raw = platform_store.get_artifact(load_import['fileArtifactId']).content
        frozen_mapping = {key: value for key, value in mapping.items() if key != 'runId'}
        preview = load_import_service.standardize(file_name=load_import['fileName'], content=raw, mapping=frozen_mapping)
        approval = self._create_approval(
            run_id=run['runId'],
            action='STANDARDIZE_LOAD',
            frozen_action={
                'importId': import_id,
                'mapping': frozen_mapping,
                'expectedSha256': preview.digest,
            },
            summary=f'生成 {preview.report["sampleCount"]} 行标准荷载，SHA256 {preview.digest[:12]}…',
        )
        load_import.update({'status': 'WAITING_APPROVAL', 'mapping': frozen_mapping, 'updatedAt': utc_now()})
        repository.save_import(load_import)
        run.update({
            'status': 'WAITING_APPROVAL',
            'currentStage': 'LOAD_STANDARDIZATION',
            'pendingApprovalId': approval['approvalId'],
            'updatedAt': utc_now(),
        })
        repository.save_run(run)
        self._record_approval_message(repository, run, approval)
        return {'run': self._decorate_run(run), 'approval': approval, 'preview': preview.report}

    def decide_approval(self, approval_id: str, approved: bool, owner: str | None = None) -> dict[str, Any]:
        repository = self.repository()
        located = self._required(repository.get_approval(approval_id), 'APPROVAL_NOT_FOUND', f'审批 {approval_id} 不存在')
        if owner is not None:
            run = repository.get_run(str(located.get('runId') or ''))
            if run is not None:
                self._ensure_owner(run, owner, code='APPROVAL_NOT_FOUND', message=f'审批 {approval_id} 不存在')
        with run_state_lock(str(located.get('runId') or '')):
            return self._decide_approval_locked(repository, approval_id, approved)

    def _decide_approval_locked(
        self,
        repository: AgentRepository,
        approval_id: str,
        approved: bool,
    ) -> dict[str, Any]:
        # 锁内重读，避免使用取锁前的陈旧审批状态。
        approval = self._required(repository.get_approval(approval_id), 'APPROVAL_NOT_FOUND', f'审批 {approval_id} 不存在')
        if approval['status'] != 'PENDING':
            return {'approval': approval, 'run': self.get_run(approval['runId'])}
        run = self._required(repository.get_run(approval['runId']), 'RUN_NOT_FOUND', f'运行 {approval["runId"]} 不存在')
        action = str(approval.get('action') or '')
        if not approved:
            approval.update({'status': 'REJECTED', 'decidedAt': utc_now(), 'updatedAt': utc_now()})
            repository.save_approval(approval)
            if run.get('runtimeMode') == 'WORKFLOW_HARNESS' and run.get('workflowSnapshot'):
                self._cancel_workflow_cursor(repository, run)
                return {'approval': approval, 'run': self._decorate_run(run)}
            if action in {'RUN_FULL_OPTIMIZATION', 'RUN_ENGINEERING_WORKFLOW', 'RUN_DAMPER_COMPARISON', 'RUN_DAMPER_PARAMETER_SWEEP'}:
                fallback = 'CANCELLED'
            else:
                fallback = 'WAITING_MAPPING' if action == 'STANDARDIZE_LOAD' else 'READY_FOR_SOLVER'
            run.update({'status': fallback, 'pendingApprovalId': None, 'updatedAt': utc_now()})
            repository.save_run(run)
            return {'approval': approval, 'run': self._decorate_run(run)}
        if action == 'STANDARDIZE_LOAD':
            self._execute_standardization(repository, run, approval)
        elif action in {
            'RUN_SOLVER',
            'RUN_ENGINEERING_WORKFLOW',
            'RUN_FULL_OPTIMIZATION',
            'RUN_DAMPER_COMPARISON',
            'RUN_DAMPER_PARAMETER_SWEEP',
        }:
            try:
                self._execute_approved_value(repository, run, approval)
            except ToolExecutionError as exc:
                # Guard 失败不能冒泡成 HTTP 500；保留原审批待处理状态，交给上层显示结构化原因。
                raise HTTPException(
                    status_code=422,
                    detail={
                        'code': exc.code,
                        'message': exc.message,
                        'details': exc.details,
                    },
                ) from exc
        else:
            raise HTTPException(status_code=422, detail={'code': 'UNKNOWN_APPROVAL_ACTION', 'message': action})
        approval.update({'status': 'APPROVED', 'decidedAt': utc_now(), 'updatedAt': utc_now()})
        repository.save_approval(approval)
        return {'approval': approval, 'run': self.get_run(run['runId'])}

    def update_approval(self, run_id: str, changes: dict[str, Any], owner: str | None = None) -> dict[str, Any]:
        """按白名单重建待审批方案；原冻结动作永不原地修改。"""
        with run_state_lock(run_id):
            self._ensure_run_owner(run_id, owner)
            return self._update_approval_locked(run_id, changes)

    def _update_approval_locked(self, run_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        repository = self.repository()
        run = self._required(repository.get_run(run_id), 'RUN_NOT_FOUND', f'运行 {run_id} 不存在')
        task_type = str(run.get('taskType') or '')
        if task_type not in {*ENGINEERING_TASK_TYPES, 'FULL_OPTIMIZATION'}:
            raise HTTPException(status_code=422, detail={
                'code': 'APPROVAL_UPDATE_UNSUPPORTED',
                'message': '当前任务类型不支持审批前修改。',
            })
        pending_id = run.get('pendingApprovalId')
        approval = self._required(
            repository.get_approval(pending_id) if pending_id else None,
            'APPROVAL_NOT_FOUND',
            f'运行 {run_id} 没有待审批动作',
        )
        if approval.get('status') != 'PENDING' or run.get('status') != 'WAITING_APPROVAL':
            raise HTTPException(status_code=409, detail={
                'code': 'APPROVAL_NOT_PENDING',
                'message': '只有等待审批的运行才能修改。',
            })

        frozen = dict(approval.get('frozenAction') or {})
        previous_contract = dict(run.get('workflowContract') or {})
        current_intent = dict(run.get('intent') or {})
        solver = str(frozen.get('solver') or previous_contract.get('solver') or 'ANSYS')
        response_ids = list(
            changes.get('responseIds')
            or frozen.get('responseIds')
            or previous_contract.get('responseIds')
            or current_intent.get('responseIds')
            or []
        )
        load_artifact_id = frozen.get('loadDatasetArtifactId') or previous_contract.get('loadArtifactId')
        load_sha256 = frozen.get('loadDatasetSha256') or previous_contract.get('loadSha256')

        damper_type = (
            changes.get('damperType')
            or changes.get('damperKind')
        )
        if damper_type is None:
            damper_type = (frozen.get('damper') or {}).get('type') or previous_contract.get('damperType')
        damper_types = list(changes.get('damperTypes') or [])
        if not damper_types:
            damper_types = [
                case.get('damperType')
                for case in (frozen.get('cases') or previous_contract.get('cases') or [])
                if case.get('damperType')
            ]

        selected_layout_id = (
            changes.get('selectedLayoutId')
            or frozen.get('selectedLayoutId')
            or previous_contract.get('selectedLayoutId')
        )
        # 布置校验必须在重建契约之前：各 rebuild_contract 会把布置转交契约构造，
        # 未登记的布置在那里是 ValueError（HTTP 500）。先在此拦成结构化 422。
        # 候选表以受控常量为准，不取 contract['layoutCandidates']：只有
        # build_engineering_contract 会内联该字段，对比与批量契约不带它。
        if selected_layout_id and selected_layout_id not in STBRIDGE_LAYOUTS:
            raise HTTPException(status_code=422, detail={
                'code': 'INVALID_DAMPER_LAYOUT',
                'message': f'阻尼器布置 {selected_layout_id} 不在受控候选表中。',
            })

        task_handler = orchestration_handler_or_generic(task_type)
        approval_update = ApprovalUpdateContext(
            changes=changes,
            frozen=frozen,
            previous_contract=previous_contract,
            solver=solver,
            response_ids=response_ids,
            damper_type=damper_type,
            damper_types=damper_types,
            load_artifact_id=load_artifact_id,
            load_sha256=load_sha256,
        )
        contract = task_handler.rebuild_contract(approval_update)
        if selected_layout_id:
            contract['selectedLayoutId'] = selected_layout_id
            contract['selectedLayout'] = deepcopy(STBRIDGE_LAYOUTS[selected_layout_id])

        previous_budget = dict(frozen.get('budget') or previous_contract.get('budget') or {})
        budget_update = dict(changes.get('budget') or {})
        doe_design_count = budget_update.get('doeDesignCount')
        if doe_design_count is not None:
            if task_type not in {'DAMPER_OPTIMIZATION', 'FULL_OPTIMIZATION'}:
                raise HTTPException(status_code=422, detail={
                    'code': 'BUDGET_UPDATE_UNSUPPORTED',
                    'message': '只有参数优化任务支持修改 DOE 设计数。',
                })
            if (
                isinstance(doe_design_count, bool)
                or not isinstance(doe_design_count, int)
                or not DOE_INITIAL_MIN <= doe_design_count <= DOE_INITIAL_MAX
            ):
                raise HTTPException(status_code=422, detail={
                    'code': 'INVALID_DOE_DESIGN_COUNT',
                    'message': f'DOE 初始设计数必须在 {DOE_INITIAL_MIN}–{DOE_INITIAL_MAX} 之间。',
                })
            previous_budget['doeDesignCount'] = doe_design_count
        if previous_budget:
            contract['budget'] = previous_budget
        contract['loadKind'] = frozen.get('loadKind') or previous_contract.get('loadKind') or contract.get('loadKind')
        if 'scenario' in contract:
            # 对比与批量契约用 scenario 承载同一工况（执行侧的 _is_real_*_request
            # 谓词按它选模板）。上一行回灌 loadKind 时必须同步，否则会留下
            # loadKind=WIND / scenario=EARTHQUAKE 的分裂契约。
            contract['scenario'] = contract['loadKind']
        field_sources = dict(contract.get('fieldSources') or {})
        if damper_type is not None or damper_types:
            field_sources['damperType' if task_type != 'DAMPER_COMPARISON' else 'damperTypes'] = 'USER_SPECIFIED'
        if selected_layout_id and (
            changes.get('selectedLayoutId') is not None
        ):
            field_sources['selectedLayoutId'] = 'USER_SPECIFIED'
        if changes.get('responseIds') is not None:
            field_sources['responseIds'] = 'USER_SPECIFIED'
        if doe_design_count is not None:
            field_sources['budget'] = 'USER_SPECIFIED'
        contract['fieldSources'] = field_sources

        current_intent.update({
            'solver': solver,
            'responseIds': response_ids,
        })
        task_handler.apply_intent_updates(current_intent, approval_update, contract=contract)
        if selected_layout_id:
            current_intent['selectedLayoutId'] = selected_layout_id
        run['intent'] = current_intent
        run['workflowContract'] = contract

        mapping = dict(frozen.get('loadMapping') or {
            'loadKind': contract.get('loadKind') or 'EARTHQUAKE',
            'channels': [],
        })
        self._prepare_agent_approval(
            repository,
            run,
            self._agent_for(task_type),
            mapping=mapping,
            standard_artifact_id=load_artifact_id,
            standard_sha256=load_sha256,
        )
        repository.save_run(run)
        approval.update({
            'status': 'SUPERSEDED',
            'supersededAt': utc_now(),
            'updatedAt': utc_now(),
        })
        repository.save_approval(approval)
        new_approval = repository.get_approval(run.get('pendingApprovalId'))
        if new_approval:
            self._record_approval_message(repository, run, new_approval)
        return {
            'run': self._decorate_run(run),
            'approval': new_approval,
        }

    def get_run(self, run_id: str, owner: str | None = None) -> dict[str, Any]:
        # 读接口会触发验收/报告注册等写入；同一 run 的读-改-写必须串行化，
        # 否则并发轮询会相互覆盖（run 是整体 JSON 落库）。
        with run_state_lock(run_id):
            self._ensure_run_owner(run_id, owner)
            return self._get_run_locked(run_id)

    def reverify_damper_parameter_sweep(self, run_id: str) -> dict[str, Any]:
        """重建指定历史参数批量的派生证据与 Agent 报告，不重新求解。"""
        with run_state_lock(run_id):
            repository = self.repository()
            run = self._required(repository.get_run(run_id), 'RUN_NOT_FOUND', f'运行 {run_id} 不存在')
            if run.get('taskType') != 'DAMPER_PARAMETER_SWEEP' or not run.get('jobId'):
                raise HTTPException(status_code=422, detail={
                    'code': 'SWEEP_REVERIFICATION_UNSUPPORTED',
                    'message': '该运行不是可回写证据的阻尼器参数批量。',
                })

            job = platform_store.reverify_real_damper_parameter_sweep(str(run['jobId']))
            payload = job.model_dump(by_alias=True, mode='json')
            agent = self._agent_for('DAMPER_PARAMETER_SWEEP')
            outcome = agent.review(payload, workflow_contract=run.get('workflowContract') or {})
            if not outcome.accepted:
                raise HTTPException(status_code=422, detail={
                    'code': 'SWEEP_REVERIFICATION_REVIEW_FAILED',
                    'message': '历史输出已复核，但仍未满足全部真实证据门槛。',
                    'checks': outcome.checks,
                })

            report = _coerce_floats(agent.build_report(run, payload, outcome))
            report.update({
                'narrativeSummary': outcome.message,
                'narrativeMode': 'DETERMINISTIC',
                'evidenceReverification': (payload.get('result') or {}).get('evidenceReverification'),
            })
            safe_report, report_content = _json_artifact_payload(report)
            artifact_name = self._required_report_file_name('DAMPER_PARAMETER_SWEEP')
            report_artifact = platform_store.register_artifact(
                kind='JSON_SUMMARY',
                name=f'{run_id}_{artifact_name}',
                path=f'output/platform_store/agent_runs/{run_id}/{artifact_name}',
                mime_type='application/json',
                preview=safe_report,
                content=report_content,
                run_id=run_id,
            )
            verification = (payload.get('result') or {}).get('evidenceReverification') or {}
            superseded_artifact_ids = {
                str(artifact_id)
                for artifact_id in verification.get('supersededArtifactIds') or []
            }
            prior_report_artifact_id = str(run.get('reportArtifactId') or '')
            job_artifact_ids = [artifact['artifactId'] for artifact in payload.get('artifacts', [])]
            result = self._display_result_values(payload)
            run.update({
                'status': outcome.run_status,
                'currentStage': outcome.run_status,
                'artifactIds': list(dict.fromkeys([
                    artifact_id
                    for artifact_id in run.get('artifactIds') or []
                    if artifact_id not in superseded_artifact_ids and artifact_id != prior_report_artifact_id
                ] + job_artifact_ids + [report_artifact.artifact_id])),
                'reportArtifactId': report_artifact.artifact_id,
                'resultSummary': {
                    'accepted': outcome.accepted,
                    'runStatus': outcome.run_status,
                    'evidenceMode': outcome.evidence_mode,
                    'checks': outcome.checks,
                    'message': outcome.message,
                    'narrativeSummary': outcome.message,
                    'narrativeMode': 'DETERMINISTIC',
                    'caseResults': result.get('caseResults') or [],
                    'objectives': result.get('objectives'),
                    'responseComparison': result.get('responseComparison') or {},
                },
                'outputManifestArtifactId': (payload.get('result') or {}).get('outputManifestArtifactId'),
                'evidenceReverification': verification,
                'updatedAt': utc_now(),
            })
            run.pop('pendingReviewOutcome', None)
            run.pop('workflowGateError', None)
            repository.save_run(run)
            return self._decorate_run(run)

    def _get_run_locked(self, run_id: str) -> dict[str, Any]:
        repository = self.repository()
        run = self._required(repository.get_run(run_id), 'RUN_NOT_FOUND', f'运行 {run_id} 不存在')
        job_id = run.get('jobId')
        if job_id and run.get('taskType') in {
            'FULL_OPTIMIZATION',
            'DAMPER_OPTIMIZATION',
            'DAMPER_COMPARISON',
            'DAMPER_PARAMETER_SWEEP',
            'ANALYSIS',
        }:
            run = self._refresh_agent_run(repository, run)
        elif job_id and run['status'] == 'WAITING_JOB':
            platform_store.refresh()
            job = platform_store.get_job(job_id)
            if job.status in {'SUCCEEDED', 'FAILED', 'CANCELLED'}:
                run['status'] = job.status
                run['currentStage'] = 'REPORT_GENERATION'
                run['updatedAt'] = utc_now()
                if job.status == 'SUCCEEDED':
                    report = self._register_run_report(run, job.model_dump(by_alias=True, mode='json'))
                    run['artifactIds'] = [*run.get('artifactIds', []), report.artifact_id]
                    run['reportArtifactId'] = report.artifact_id
                    run['currentStage'] = 'COMPLETED'
                repository.save_run(run)
        return self._decorate_run(run)

    @staticmethod
    def _provision_bundled_earthquake(
        repository: AgentRepository,
        run: dict[str, Any],
    ) -> dict[str, Any]:
        bundled = load_artifact_service.provision_bundled_earthquake(run['runId'])
        artifact_ids = list(bundled['artifactIds'])
        run['artifactIds'] = list(dict.fromkeys([*run.get('artifactIds', []), *artifact_ids]))
        repository.save_step({
            'stepId': gen_id('step'),
            'runId': run['runId'],
            'idempotencyKey': f'{run["runId"]}:BUNDLED_EARTHQUAKE:{bundled["standardSha256"]}',
            'title': '登记项目内置地震荷载',
            'status': 'SUCCEEDED',
            'artifactIds': artifact_ids,
            'createdAt': utc_now(),
        })
        return bundled

    @staticmethod
    def _provision_bundled_wind(
        repository: AgentRepository,
        run: dict[str, Any],
    ) -> dict[str, Any]:
        bundled = load_artifact_service.provision_bundled_wind(run['runId'])
        artifact_ids = list(bundled['artifactIds'])
        run['artifactIds'] = list(dict.fromkeys([*run.get('artifactIds', []), *artifact_ids]))
        repository.save_step({
            'stepId': gen_id('step'),
            'runId': run['runId'],
            'idempotencyKey': f'{run["runId"]}:BUNDLED_WIND:{bundled["standardSha256"]}',
            'title': '登记项目内置风荷载',
            'status': 'SUCCEEDED',
            'artifactIds': artifact_ids,
            'createdAt': utc_now(),
        })
        return bundled

    @staticmethod
    def _provision_bundled_traffic(
        repository: AgentRepository,
        run: dict[str, Any],
    ) -> dict[str, Any]:
        bundled = load_artifact_service.provision_bundled_traffic(run['runId'])
        artifact_ids = list(bundled['artifactIds'])
        run['artifactIds'] = list(dict.fromkeys([*run.get('artifactIds', []), *artifact_ids]))
        repository.save_step({
            'stepId': gen_id('step'),
            'runId': run['runId'],
            # 幂等键同时纳入矩阵与 mapping 两个 SHA：任一制品变化都必须重新登记，
            # 只钉矩阵会让 mapping 换了列序却复用旧步骤。
            'idempotencyKey': (
                f'{run["runId"]}:BUNDLED_TRAFFIC:'
                f'{bundled["standardSha256"]}:{bundled["pointMappingSha256"]}'
            ),
            'title': '登记项目内置车流荷载',
            'status': 'SUCCEEDED',
            'artifactIds': artifact_ids,
            'createdAt': utc_now(),
        })
        return bundled

    def get_run_timeseries(
        self,
        run_id: str,
        *,
        columns: list[str] | None = None,
        max_points: int = 1000,
        owner: str | None = None,
    ) -> dict[str, Any]:
        """读取已登记的 timeseries.csv，并按请求列返回峰值保真的曲线。"""
        repository = self.repository()
        run = self._required(repository.get_run(run_id), 'RUN_NOT_FOUND', f'运行 {run_id} 不存在')
        self._ensure_owner(run, owner, code='RUN_NOT_FOUND', message=f'运行 {run_id} 不存在')
        try:
            artifacts = self._run_csv_artifacts(run)
        except ResultInquiryError as exc:
            # 结果目录校验失败属于该运行的数据问题（例如旧运行的目录声明了未登记的
            # 来源 CSV），不是服务器内部故障；返回可识别的 422 而不是 500。
            raise HTTPException(
                status_code=422,
                detail={'code': 'RESULT_CATALOG_INVALID', 'message': str(exc)},
            ) from exc
        artifact_id = artifacts.get('timeseries.csv') or next(
            (artifact_id for name, artifact_id in artifacts.items() if name.endswith('/timeseries.csv')),
            None,
        )
        if not artifact_id:
            raise HTTPException(
                status_code=404,
                detail={
                    'code': 'TIMESERIES_NOT_AVAILABLE',
                    'message': '该运行没有已登记的 timeseries.csv 结果。',
                },
            )
        service = ResultInquiryService(platform_store)
        try:
            available = service.columns(artifact_id)
            requested = [name for name in (columns or available) if name in available]
            selected = list(dict.fromkeys(requested)) or available
            series = service.downsample(
                artifact_id,
                columns=selected,
                max_points=max_points,
            )
            peaks = {
                name: service.peak(artifact_id, column=name)
                for name in selected
                if name != 'time'
            }
        except ResultInquiryError as exc:
            raise HTTPException(
                status_code=422,
                detail={'code': 'TIMESERIES_INVALID', 'message': str(exc)},
            ) from exc
        return {
            'runId': run_id,
            'availableColumns': available,
            'columns': selected,
            'series': series,
            'peaks': peaks,
        }

    @staticmethod
    def _sweep_case_label(parameters: dict[str, Any]) -> str:
        """把参数组合格式化为时程图例，不把参数误当作时程数据。"""
        labels = {'alpha': 'α'}
        ordered_names = ['c', 'alpha', 'vfloor']
        names = [
            name for name in ordered_names
            if name in parameters
            and not (
                name == 'vfloor'
                and isinstance(parameters[name], (int, float))
                and math.isclose(float(parameters[name]), 0.001, rel_tol=0.0, abs_tol=1e-12)
            )
        ]
        names.extend(sorted(name for name in parameters if name not in ordered_names))
        parts = []
        for name in names:
            value = parameters[name]
            display = format(value, 'g') if isinstance(value, (int, float)) else str(value)
            parts.append(f'{labels.get(name, name)}={display}')
        return '，'.join(parts) or '未记录参数'

    def get_run_timeseries_comparison(
        self,
        run_id: str,
        *,
        columns: list[str] | None = None,
        case_ids: list[str] | None = None,
        max_points: int = 1000,
        owner: str | None = None,
    ) -> dict[str, Any]:
        """读取同一参数批量的多案例时程，不重新求解或读取未登记路径。"""
        repository = self.repository()
        run = self._required(repository.get_run(run_id), 'RUN_NOT_FOUND', f'运行 {run_id} 不存在')
        self._ensure_owner(run, owner, code='RUN_NOT_FOUND', message=f'运行 {run_id} 不存在')
        if run.get('taskType') != 'DAMPER_PARAMETER_SWEEP':
            raise HTTPException(status_code=422, detail={
                'code': 'TIMESERIES_COMPARISON_NOT_AVAILABLE',
                'message': '多工况时程对比只适用于阻尼器参数批量结果。',
            })
        case_results = list((run.get('resultSummary') or {}).get('caseResults') or [])
        if not case_results and run.get('jobId'):
            try:
                case_results = list((platform_store.get_job(str(run['jobId'])).result or {}).get('caseResults') or [])
            except (AttributeError, KeyError, HTTPException):
                case_results = []
        verified_cases = [
            case for case in case_results
            if isinstance(case, dict) and case.get('isVerifiedSolverOutput') is True
        ]
        if not verified_cases:
            raise HTTPException(status_code=404, detail={
                'code': 'TIMESERIES_COMPARISON_NOT_AVAILABLE',
                'message': '该批量没有可用于对比的已验证参数工况。',
            })
        requested_case_ids = list(dict.fromkeys(str(case_id) for case_id in case_ids or [] if str(case_id)))
        verified_by_id = {str(case.get('caseId')): case for case in verified_cases}
        unknown_case_ids = [case_id for case_id in requested_case_ids if case_id not in verified_by_id]
        if unknown_case_ids:
            raise HTTPException(status_code=422, detail={
                'code': 'TIMESERIES_CASE_NOT_AVAILABLE',
                'message': f'请求的工况未通过验证或不存在: {", ".join(unknown_case_ids)}',
            })
        selected_cases = (
            [verified_by_id[case_id] for case_id in requested_case_ids]
            if requested_case_ids
            else verified_cases
        )
        try:
            result_catalog = self._load_result_catalog(run)
        except ResultInquiryError as exc:
            raise HTTPException(
                status_code=422,
                detail={'code': 'TIMESERIES_INVALID', 'message': str(exc)},
            ) from exc
        if result_catalog is None:
            raise HTTPException(status_code=404, detail={
                'code': 'TIMESERIES_COMPARISON_NOT_AVAILABLE',
                'message': '该批量缺少已登记的统一结果目录。',
            })

        case_sources: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for case in selected_cases:
            case_id = str(case.get('caseId') or '')
            candidates = [
                entry for entry in result_catalog['entries']
                if str(entry.get('path') or '').replace('\\', '/').endswith('/timeseries.csv')
                and f'/{case_id}/' in str(entry.get('path') or '').replace('\\', '/')
            ]
            if len(candidates) != 1:
                raise HTTPException(status_code=422, detail={
                    'code': 'TIMESERIES_CASE_SOURCE_INVALID',
                    'message': f'工况 {case_id} 没有唯一的已登记时程 CSV。',
                })
            case_sources.append((case, candidates[0]))

        shared_columns = set(case_sources[0][1]['columns'])
        for _case, source in case_sources[1:]:
            shared_columns &= set(source['columns'])
        if 'time' not in shared_columns:
            raise HTTPException(status_code=422, detail={
                'code': 'TIMESERIES_COMMON_COLUMN_MISSING',
                'message': '所选工况不存在共同的时间列，不能叠加时程曲线。',
            })
        available_columns = [
            column for column in case_sources[0][1]['columns']
            if column in shared_columns
        ]
        requested_columns = [name for name in columns or available_columns if name in shared_columns and name != 'time']
        selected_columns = ['time', *list(dict.fromkeys(requested_columns))]
        if len(selected_columns) == 1:
            raise HTTPException(status_code=422, detail={
                'code': 'TIMESERIES_COMMON_COLUMN_MISSING',
                'message': '所选工况没有共同的响应列，不能叠加时程曲线。',
            })

        inquiry_service = ResultInquiryService(platform_store)
        payload_cases = []
        try:
            for case, source in case_sources:
                artifact_id = str(source['artifactId'])
                payload_cases.append({
                    'caseId': str(case.get('caseId')),
                    'parameters': dict(case.get('parameters') or {}),
                    'label': self._sweep_case_label(dict(case.get('parameters') or {})),
                    'availableColumns': list(source['columns']),
                    'columns': selected_columns,
                    'series': inquiry_service.downsample(
                        artifact_id,
                        columns=selected_columns,
                        max_points=max_points,
                    ),
                    'peaks': {
                        column: inquiry_service.peak(artifact_id, column=column)
                        for column in selected_columns
                        if column != 'time'
                    },
                })
        except ResultInquiryError as exc:
            raise HTTPException(
                status_code=422,
                detail={'code': 'TIMESERIES_INVALID', 'message': str(exc)},
            ) from exc
        return {
            'runId': run_id,
            'availableColumns': available_columns,
            'columns': selected_columns,
            'cases': payload_cases,
        }

    def _run_csv_artifacts(self, run: dict[str, Any]) -> dict[str, str]:
        """返回运行关联的 CSV 制品；兼容旧 run 的 Job artifact。"""
        return self._inquiry_artifacts(run)

    def _refresh_agent_run(
        self,
        repository: AgentRepository,
        run: dict[str, Any],
    ) -> dict[str, Any]:
        gate_error = run.get('workflowGateError')
        if isinstance(gate_error, dict) and gate_error.get('code') == 'REPORT_GENERATION_ERROR':
            transaction = getattr(platform_store, 'state_transaction', None)
            try:
                with transaction() if callable(transaction) else nullcontext():
                    platform_store.refresh()
                    return self._recover_persisted_agent_report(repository, run) or run
            except Exception:
                logger.exception(
                    'agent report recovery failed: %s',
                    run.get('runId'),
                    extra={
                        'event': 'agent_report_recovery_failed',
                        'job_id': run.get('jobId'),
                        'error_code': 'REPORT_RECOVERY_ERROR',
                    },
                )
                return run
        transaction = getattr(platform_store, 'state_transaction', None)
        try:
            with transaction() if callable(transaction) else nullcontext():
                return self._refresh_agent_run_once(repository, run)
        except Exception as exc:
            logger.exception(
                'agent run refresh failed: %s',
                run.get('runId'),
                extra={
                    'event': 'agent_run_refresh_failed',
                    'job_id': run.get('jobId'),
                    'error_code': 'REPORT_GENERATION_ERROR',
                },
            )
            run.update({
                'status': 'FAILED',
                'currentStage': 'FAILED',
                'currentStep': 'FAILED',
                'workflowGateError': {
                    'code': 'REPORT_GENERATION_ERROR',
                    'message': '结果验收或报告生成失败，运行已安全终止。',
                    'details': {
                        'stage': 'REPORT_GENERATION',
                        'exceptionType': type(exc).__name__,
                    },
                },
                'updatedAt': utc_now(),
            })
            repository.save_run(run)
            return run

    def _recover_persisted_agent_report(
        self,
        repository: AgentRepository,
        run: dict[str, Any],
    ) -> dict[str, Any] | None:
        """把已落库但尚未挂载的报告恢复到原 run，不重播验收或报告生成。"""

        task_type = str(run.get('taskType') or '')
        artifact_name = report_file_name(task_type)
        run_id = str(run.get('runId') or '')
        job_id = str(run.get('jobId') or '')
        find_report = getattr(platform_store, 'find_artifact_for_run', None)
        if not artifact_name or not run_id or not job_id or not callable(find_report):
            return None
        record = find_report(run_id, f'{run_id}_{artifact_name}')
        if record is None or not isinstance(record.preview, dict):
            return None
        report = record.preview
        accepted = report.get('isFinalResult')
        checks = report.get('checks')
        if (
            report.get('agentRunId') != run_id
            or report.get('taskType') != task_type
            or report.get('jobId') != job_id
            or report.get('jobStatus') not in {'SUCCEEDED', 'FAILED', 'CANCELLED'}
            or not isinstance(accepted, bool)
            or not isinstance(checks, dict)
        ):
            return None

        job = platform_store.get_job(job_id)
        if job.status != report.get('jobStatus'):
            return None
        payload = job.model_dump(by_alias=True, mode='json')
        job_artifact_ids = [
            str(artifact.get('artifactId'))
            for artifact in payload.get('artifacts', [])
            if artifact.get('artifactId')
        ]
        run_status = (
            str(job.status)
            if job.status in {'FAILED', 'CANCELLED'}
            else 'SUCCEEDED' if accepted else 'COMPLETED_DIAGNOSTIC'
        )
        result = self._display_result_values(payload)
        report_artifact_id = record.artifact.artifact_id
        run.update({
            'status': run_status,
            'currentStage': run_status,
            'artifactIds': list(dict.fromkeys([
                *run.get('artifactIds', []),
                *job_artifact_ids,
                report_artifact_id,
            ])),
            'reportArtifactId': report_artifact_id,
            'resultSummary': {
                'accepted': accepted,
                'runStatus': run_status,
                'evidenceMode': report.get('evidenceMode'),
                'checks': checks,
                'message': report.get('conclusion'),
                'objectives': result.get('objectives'),
                'baselineObjectives': result.get('baselineObjectives'),
                'recommendedObjectives': result.get('recommendedObjectives'),
                'recommendedParameters': result.get('recommendedParameters'),
                'caseResults': result.get('caseResults') or [],
                'responseComparison': result.get('responseComparison') or {},
                'sampleResponses': result.get('sampleResponses') or [],
                **(
                    {'narrativeSummary': report.get('narrativeSummary')}
                    if report.get('narrativeSummary')
                    else {}
                ),
            },
            'outputManifestArtifactId': (
                report.get('outputManifestArtifactId')
                or (payload.get('result') or {}).get('outputManifestArtifactId')
            ),
            'updatedAt': utc_now(),
        })
        run.pop('workflowGateError', None)
        run.pop('pendingReviewOutcome', None)

        snapshot = run.get('workflowSnapshot') or {}
        completed = set(run.get('completedSteps') or [])
        terminal_steps = set(snapshot.get('terminalSteps') or [])
        next_step = next(
            (
                str(step.get('stepId'))
                for step in snapshot.get('steps', [])
                if step.get('stepId') not in completed and step.get('stepId') not in terminal_steps
            ),
            None,
        )
        if next_step:
            run['currentStep'] = next_step
        self._advance_workflow_terminal(
            repository,
            run,
            evidence_accepted=accepted,
            report_persisted=True,
        )
        repository.save_run(run)
        return run

    def _refresh_agent_run_once(
        self,
        repository: AgentRepository,
        run: dict[str, Any],
    ) -> dict[str, Any]:
        """统一处理三个工程 Agent 的 Job 刷新、验收和报告注册。"""
        platform_store.refresh()
        job = platform_store.get_job(run['jobId'])
        if job.status not in {'SUCCEEDED', 'FAILED', 'CANCELLED'}:
            return run
        payload = job.model_dump(by_alias=True, mode='json')
        job_artifact_ids = [artifact['artifactId'] for artifact in payload.get('artifacts', [])]
        self._complete_job_tool_calls(
            repository,
            run,
            job_status=str(job.status),
            artifact_ids=job_artifact_ids,
        )
        if (
            self._run_persistent_loop(run)
            and job.status == 'SUCCEEDED'
            and str(run.get('currentStep') or '') not in {
                'REPORT', 'COMPLETED', 'FAILED', 'CANCELLED',
            }
        ):
            # 每次刷新只允许模型推进一个内部步骤；下一次请求从持久化游标继续。
            self._resume_persistent_job_stage(
                repository,
                run,
                artifact_ids=job_artifact_ids,
                job_payload=payload,
            )
            return run
        if run.get('reportArtifactId'):
            missing_artifacts = [
                artifact_id
                for artifact_id in job_artifact_ids
                if artifact_id not in (run.get('artifactIds') or [])
            ]
            if missing_artifacts:
                run['artifactIds'] = list(dict.fromkeys([
                    *run.get('artifactIds', []),
                    *missing_artifacts,
                ]))
                run['updatedAt'] = utc_now()
                repository.save_run(run)
            return run
        if (
            run.get('taskType') in {'FULL_OPTIMIZATION', 'DAMPER_OPTIMIZATION'}
            and run.get('status') not in {'SUCCEEDED', 'COMPLETED_DIAGNOSTIC', 'FAILED', 'CANCELLED'}
        ):
            self._validate_run_transition(run, 'REVIEWING')
            run.update({'status': 'REVIEWING', 'currentStage': 'REVIEWING', 'updatedAt': utc_now()})
            repository.save_run(run)
        agent = self._agent_for(str(run.get('taskType')))
        pending_review = run.get('pendingReviewOutcome')
        if self._run_persistent_loop(run) and isinstance(pending_review, dict):
            outcome = ReviewOutcome(
                accepted=bool(pending_review.get('accepted')),
                run_status=str(pending_review.get('runStatus') or 'COMPLETED_DIAGNOSTIC'),
                evidence_mode=str(pending_review.get('evidenceMode') or 'DIAGNOSTIC'),
                checks=dict(pending_review.get('checks') or {}),
                message=str(pending_review.get('message') or '证据审查已完成。'),
                extra=dict(pending_review.get('extra') or {}),
            )
        else:
            outcome = agent.review(
                payload,
                workflow_contract={
                    'taskType': run.get('taskType'),
                    **(run.get('workflowContract') or {}),
                },
            )
        report = _coerce_floats(agent.build_report(run, payload, outcome))
        narrative = self._safe_narrate_result(
            task_type=str(run.get('taskType')),
            accepted=outcome.accepted,
            evidence_mode=outcome.evidence_mode,
            template_message=outcome.message,
            facts=agent.narrative_facts(report, payload),
        )
        report.update(self._narrative_fields(narrative))
        safe_report, report_content = _json_artifact_payload(report)
        artifact_name = self._required_report_file_name(str(run.get('taskType')))
        report_artifact = platform_store.register_artifact(
            kind='JSON_SUMMARY',
            name=f'{run["runId"]}_{artifact_name}',
            path=f'output/platform_store/agent_runs/{run["runId"]}/{artifact_name}',
            mime_type='application/json',
            preview=safe_report,
            content=report_content,
            run_id=run['runId'],
        )
        report_preview = platform_store.get_artifact(report_artifact.artifact_id).preview
        narrative_summary = report_preview.get('narrativeSummary') if isinstance(report_preview, dict) else None
        result = self._display_result_values(payload)
        reflection = {
            'accepted': outcome.accepted,
            'runStatus': outcome.run_status,
            'evidenceMode': outcome.evidence_mode,
            'checks': outcome.checks,
            'message': outcome.message,
            **outcome.extra,
            'objectives': result.get('objectives'),
            'baselineObjectives': result.get('baselineObjectives'),
            'recommendedObjectives': result.get('recommendedObjectives'),
            'recommendedParameters': result.get('recommendedParameters'),
            'caseResults': result.get('caseResults') or [],
            'responseComparison': result.get('responseComparison') or {},
            'sampleResponses': result.get('sampleResponses') or [],
        }
        self._validate_run_transition(run, str(outcome.run_status))
        run.update({
            'status': outcome.run_status,
            'currentStage': outcome.run_status,
            'artifactIds': list(dict.fromkeys([
                *run.get('artifactIds', []),
                *job_artifact_ids,
                report_artifact.artifact_id,
            ])),
            'reportArtifactId': report_artifact.artifact_id,
            'resultSummary': {
                **reflection,
                **({'narrativeSummary': narrative_summary} if narrative_summary else {}),
            },
            'outputManifestArtifactId': (payload.get('result') or {}).get('outputManifestArtifactId'),
            'updatedAt': utc_now(),
        })
        run.pop('pendingReviewOutcome', None)
        self._advance_workflow_terminal(
            repository,
            run,
            evidence_accepted=bool(outcome.accepted),
            report_persisted=(
                report_artifact.artifact_id in run['artifactIds']
                and platform_store.get_artifact(report_artifact.artifact_id).artifact.artifact_id
                == report_artifact.artifact_id
                and platform_store.get_artifact(report_artifact.artifact_id).artifact.run_id
                == run['runId']
            ),
        )
        repository.save_run(run)
        return run

    @staticmethod
    def _display_result_values(payload: dict[str, Any]) -> dict[str, Any]:
        """从 Job result 或优化摘要制品提取前端需要的标量，不携带时程数组。"""
        result = dict(payload.get('result') or {})
        artifacts = payload.get('artifacts') or []
        previews: dict[str, dict[str, Any]] = {}
        for item in artifacts:
            artifact_id = item.get('artifactId')
            name = item.get('name')
            if not artifact_id or not name:
                continue
            try:
                preview = platform_store.get_artifact(artifact_id).preview
            except (AttributeError, KeyError, HTTPException):
                continue
            if isinstance(preview, dict):
                previews[str(name)] = preview
        optimization = previews.get('real_optimization_summary.json', {}).get('optimization') or {}
        baseline = previews.get('real_baseline_summary.json', {}).get('objectives') or {}
        comparison_summary = previews.get('real_damper_comparison_summary.json', {})
        # 概览制品按荷载类型分名（地震/风），这里取实际存在的那一个。
        workflow_overview = next(
            (
                previews[name]
                for name in OPTIMIZATION_OVERVIEW_ARTIFACT_NAMES.values()
                if name in previews
            ),
            {},
        )
        scenario_prefix = str(workflow_overview.get('scenario') or 'EARTHQUAKE').lower()
        result.setdefault('caseResults', comparison_summary.get('caseResults') or [])
        result.setdefault('responseComparison', comparison_summary.get('responseComparison') or {})
        result.setdefault('sampleResponses', workflow_overview.get('sampleResponses') or [])
        objective_names = list(optimization.get('objective_names') or [])
        best_objectives = list(optimization.get('best_objectives') or [])
        recommended = {
            str(name).split(':', 1)[-1]: value
            for name, value in zip(objective_names, best_objectives)
        }
        if recommended:
            result.setdefault('recommendedObjectives', recommended)
            result.setdefault('baselineObjectives', {
                key: baseline.get(key, baseline.get(f'{scenario_prefix}:{key}'))
                for key in recommended
                if key in baseline or f'{scenario_prefix}:{key}' in baseline
            })
        if optimization.get('parameter_names') and optimization.get('best_design'):
            result.setdefault('recommendedParameters', dict(zip(
                optimization['parameter_names'], optimization['best_design'],
            )))
        return result

    def _register_agent_report(
        self,
        run: dict[str, Any],
        job: dict[str, Any],
        outcome: ReviewOutcome,
        agent: EngineeringAgent,
    ):
        report = _coerce_floats(agent.build_report(run, job, outcome))
        narrative = self._safe_narrate_result(
            task_type=str(run.get('taskType')),
            accepted=outcome.accepted,
            evidence_mode=outcome.evidence_mode,
            template_message=outcome.message,
            facts=agent.narrative_facts(report, job),
        )
        report.update(self._narrative_fields(narrative))
        safe_report, report_content = _json_artifact_payload(report)
        artifact_name = self._required_report_file_name(str(run.get('taskType')))
        return platform_store.register_artifact(
            kind='JSON_SUMMARY',
            name=f'{run["runId"]}_{artifact_name}',
            path=f'output/platform_store/agent_runs/{run["runId"]}/{artifact_name}',
            mime_type='application/json',
            preview=safe_report,
            content=report_content,
        )

    def _safe_narrate_result(
        self,
        *,
        task_type: str,
        accepted: bool,
        evidence_mode: str,
        template_message: str,
        facts: dict[str, Any],
    ) -> NarrativeResult:
        fallback_text = str(template_message or '').strip()[:2000] or '结果已生成。'
        try:
            result = self.planner.narrate_result(
                task_type=task_type,
                accepted=accepted,
                evidence_mode=evidence_mode,
                template_message=fallback_text,
                facts=facts,
            )
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
                text=fallback_text,
                fallbackReason='LLM_NARRATIVE_EXCEPTION',
            )

    @staticmethod
    def _approval_facts(
        frozen_action: dict[str, Any],
        contract: dict[str, Any],
    ) -> dict[str, Any]:
        """只提取给审批说明用的事实，不暴露哈希或节点对。"""
        layout = frozen_action.get('selectedLayout') or contract.get('selectedLayout') or {}
        resources = frozen_action.get('resources') or {}
        cases = frozen_action.get('cases') or []
        damper_type = (frozen_action.get('damper') or {}).get('type') or contract.get('damperType')
        if damper_type is None and cases:
            damper_type = [case.get('damperType') for case in cases if case.get('damperType')]
        task_type = str(frozen_action.get('taskType') or contract.get('taskType') or '')
        budget = frozen_action.get('budget') or contract.get('budget') or {}
        optimization_defaults = (
            FULL_OPTIMIZATION_CONTRACT
            if task_type in {'FULL_OPTIMIZATION', 'DAMPER_OPTIMIZATION'}
            else {}
        )
        task_handler = orchestration_handler(task_type)
        estimated_solves_min: int | None
        estimated_solves_max: int | None
        if task_handler is None:
            estimated_solves_min = estimated_solves_max = None
        else:
            estimated_solves_min, estimated_solves_max = task_handler.estimated_solves(
                budget=budget,
                contract=contract,
                cases=cases,
            )
        selected_layout = {
            'id': frozen_action.get('selectedLayoutId') or contract.get('selectedLayoutId'),
            'direction': layout.get('direction'),
            'physicalCountPerTower': layout.get('physicalCountPerTower'),
        }
        input_sources = {
            str(item.get('field')): str(item.get('source'))
            for item in (frozen_action.get('inputProvenance') or [])
            if isinstance(item, dict) and item.get('field') and item.get('source')
            and str(item.get('field')) in {
                'solver', 'loadCase', 'damper.type', 'damper.cases',
                'selectedLayoutId', 'responseIds', 'budget', 'executionTimeoutS',
            }
        }
        load_artifact_id = frozen_action.get('loadDatasetArtifactId') or contract.get('loadArtifactId')
        load_mapping = frozen_action.get('loadMapping') or {}
        load_source = (
            load_mapping.get('source')
            or ('UPLOADED_FILE' if load_artifact_id else 'TEMPLATE')
        )
        return {
            'solver': frozen_action.get('solver') or contract.get('solver'),
            'loadKind': frozen_action.get('loadKind') or contract.get('loadKind'),
            'runMode': frozen_action.get('runMode'),
            'responseIds': frozen_action.get('responseIds') or contract.get('responseIds') or [],
            'damperType': damper_type,
            'hasDamper': bool(damper_type),
            'layoutId': frozen_action.get('selectedLayoutId') or contract.get('selectedLayoutId'),
            'damperCountPerTower': layout.get('physicalCountPerTower'),
            'direction': layout.get('direction'),
            'executionTimeoutS': frozen_action.get('executionTimeoutS') or resources.get('executionTimeoutS'),
            'budget': budget or {
                key: contract[key]
                for key in ('doeDesignCount', 'candidateCount', 'surrogateCv', 'maxActiveLearningIterations')
                if key in contract
            },
            'fieldSources': frozen_action.get('fieldSources') or contract.get('fieldSources') or {},
            'estimatedRealSolves': estimated_solves_max,
            'estimatedRealSolvesMin': estimated_solves_min,
            'estimatedRealSolvesMax': estimated_solves_max,
            'selectedLayout': selected_layout,
            'dampingCoefficient': (
                frozen_action.get('dampingCoefficient')
                or contract.get('dampingCoefficient')
                or optimization_defaults.get('dampingCoefficient')
            ),
            'velocityExponent': (
                frozen_action.get('velocityExponent')
                or contract.get('velocityExponent')
                or optimization_defaults.get('velocityExponent')
            ),
            'loadCases': (
                frozen_action.get('loadCases')
                or contract.get('loadCases')
                or optimization_defaults.get('loadCases')
            ),
            'caseCount': len(cases) or None,
            'loadSource': load_source,
            'usesUploadedLoad': bool(load_artifact_id) and load_source == 'UPLOADED_FILE',
            'inputSources': input_sources,
        }

    def _safe_describe_pending_action(
        self,
        *,
        task_type: str,
        approval_action: str,
        facts: dict[str, Any],
        template_message: str,
    ) -> NarrativeResult:
        fallback_text = str(template_message or '').strip()[:2000] or '批准后执行冻结的真实工程 Job。'
        try:
            describe = getattr(self.planner, 'describe_pending_action')
            result = describe(
                task_type=task_type,
                approval_action=approval_action,
                facts=facts,
            )
            if isinstance(result, NarrativeResult):
                return result if result.narrative_mode == 'LLM' else NarrativeResult(
                    narrativeMode='TEMPLATE_FALLBACK',
                    text=fallback_text,
                    fallbackReason=result.fallback_reason,
                )
            narrative = NarrativeResult(
                narrativeMode=str(result.narrative_mode),
                text=str(result.text),
                fallbackReason=result.fallback_reason,
            )
            return narrative if narrative.narrative_mode == 'LLM' else NarrativeResult(
                narrativeMode='TEMPLATE_FALLBACK',
                text=fallback_text,
                fallbackReason=narrative.fallback_reason,
            )
        except Exception:
            return NarrativeResult(
                narrativeMode='TEMPLATE_FALLBACK',
                text=fallback_text,
                fallbackReason='LLM_APPROVAL_NARRATIVE_EXCEPTION',
            )

    @staticmethod
    def _narrative_fields(result: NarrativeResult) -> dict[str, Any]:
        return {
            'narrativeSummary': result.text,
            'narrativeMode': result.narrative_mode,
            'narrativeFallbackReason': result.fallback_reason,
        }

    def cancel_run(self, run_id: str, owner: str | None = None) -> dict[str, Any]:
        with run_state_lock(run_id):
            self._ensure_run_owner(run_id, owner)
            return self._cancel_run_locked(run_id)

    def _cancel_run_locked(self, run_id: str) -> dict[str, Any]:
        repository = self.repository()
        run = self._required(repository.get_run(run_id), 'RUN_NOT_FOUND', f'运行 {run_id} 不存在')
        if run.get('jobId'):
            try:
                platform_dispatcher.cancel_job(run['jobId'])
            except HTTPException as exc:
                if exc.status_code != 404:
                    raise
                logger.warning(
                    'cancel stale run without job: %s',
                    run_id,
                    extra={'event': 'agent_cancel_stale_job', 'job_id': run['jobId']},
                )
        self._cancel_workflow_cursor(repository, run)
        run.update({'status': 'CANCELLED', 'currentStage': 'CANCELLED', 'updatedAt': utc_now()})
        repository.save_run(run)
        return (
            self.get_run(run_id)
            if run.get('taskType') in {'FULL_OPTIMIZATION', 'DAMPER_OPTIMIZATION', 'DAMPER_COMPARISON', 'ANALYSIS'}
            else self._decorate_run(run)
        )

    def _execute_standardization(
        self,
        repository: AgentRepository,
        run: dict[str, Any],
        approval: dict[str, Any],
    ) -> None:
        frozen = approval['frozenAction']
        load_import = self._required(repository.get_import(frozen['importId']), 'LOAD_IMPORT_NOT_FOUND', '荷载导入不存在')
        mapping = frozen['mapping']
        raw = platform_store.get_artifact(load_import['fileArtifactId']).content
        standardized = load_import_service.standardize(file_name=load_import['fileName'], content=raw, mapping=mapping)
        if standardized.digest != frozen['expectedSha256']:
            raise HTTPException(status_code=409, detail={'code': 'SOURCE_CHANGED', 'message': '审批后标准荷载内容发生变化'})

        if load_import.get('standardSha256') == standardized.digest:
            standard_artifact_id = load_import['standardArtifactId']
            report_artifact_id = load_import['reportArtifactId']
            point_mapping_artifact_id = load_import.get('pointMappingArtifactId')
            point_mapping_sha256 = load_import.get('pointMappingSha256')
        else:
            # 源字节变了就整套重登记：mapping 是同一份字节推导出来的，复用旧
            # mapping 会让它与新矩阵的列序对不上，而错位在量纲上看不出来。
            point_mapping_artifact_id = None
            point_mapping_sha256 = None
            safe_report, report_content = _json_artifact_payload(standardized.report)
            standard_artifact = platform_store.register_artifact(
                kind='CSV_TIMESERIES',
                name=f'{Path(load_import["fileName"]).stem}_momo_standard.csv',
                path=f'output/platform_store/load_imports/{load_import["importId"]}/momo_standard.csv',
                mime_type='text/csv; charset=utf-8',
                preview=_coerce_floats({
                    'rows': standardized.content.decode('utf-8').splitlines()[:9],
                    **standardized.report,
                }),
                content=standardized.content,
                run_id=run['runId'],
            )
            report_artifact = platform_store.register_artifact(
                kind='JSON_SUMMARY',
                name='load_conversion_report.json',
                path=f'output/platform_store/load_imports/{load_import["importId"]}/conversion_report.json',
                mime_type='application/json',
                preview=safe_report,
                content=report_content,
                run_id=run['runId'],
            )
            standard_artifact_id = standard_artifact.artifact_id
            report_artifact_id = report_artifact.artifact_id

        artifact_ids = [standard_artifact_id, report_artifact_id]
        if standardized.point_mapping is not None:
            # 车流的稠密矩阵单独存在是不可解释的：哪一列对应哪个节点要靠这份 mapping
            # 声明。它由 standardize() 从同一份源字节推导，所以与矩阵同生共死，按标准
            # 制品的同一条口径复用；两个 SHA 都会被 ANALYSIS 审批冻结
            # （analysis._load_kind_gate 缺任一则失败关闭）。
            if not point_mapping_artifact_id:
                point_mapping_artifact = platform_store.register_artifact(
                    kind='JSON_SUMMARY',
                    name=f'{Path(load_import["fileName"]).stem}_node_mappings.json',
                    path=f'output/platform_store/load_imports/{load_import["importId"]}/node_mappings.json',
                    mime_type='application/json',
                    preview={
                        'nodeCount': standardized.report.get('nodeCount'),
                        'mappings': json.loads(standardized.point_mapping.decode('utf-8'))[:8],
                    },
                    content=standardized.point_mapping,
                    run_id=run['runId'],
                )
                point_mapping_artifact_id = point_mapping_artifact.artifact_id
                point_mapping_sha256 = point_mapping_artifact.sha256
            # mapping 制品的 ID/SHA 跟着 mapping 字典走：审批门只收 mapping 与标准
            # 制品两个入参，挂在这里就能一路流到 frozen_action，与内置车流同一条路径
            # （见 load_artifact_service._provision_bundled_traffic）。
            mapping = {
                **mapping,
                'pointMappingArtifactId': point_mapping_artifact_id,
                'pointMappingSha256': point_mapping_sha256,
            }
            artifact_ids.append(point_mapping_artifact_id)

        load_import.update({
            'status': 'STANDARDIZED',
            'standardSha256': standardized.digest,
            'standardArtifactId': standard_artifact_id,
            'reportArtifactId': report_artifact_id,
            'pointMappingArtifactId': point_mapping_artifact_id,
            'pointMappingSha256': point_mapping_sha256,
            'updatedAt': utc_now(),
        })
        repository.save_import(load_import)
        step = repository.save_step({
            'stepId': gen_id('step'),
            'runId': run['runId'],
            'idempotencyKey': f'{run["runId"]}:STANDARDIZE:{standardized.digest}',
            'title': '标准化荷载文件',
            'status': 'SUCCEEDED',
            'artifactIds': artifact_ids,
            'createdAt': utc_now(),
        })
        if run.get('taskType') in {'DAMPER_OPTIMIZATION', 'DAMPER_COMPARISON', 'DAMPER_PARAMETER_SWEEP', 'ANALYSIS'}:
            self._prepare_agent_approval(
                repository,
                run,
                self._agent_for(str(run['taskType'])),
                mapping=mapping,
                standard_artifact_id=standard_artifact_id,
                standard_sha256=standardized.digest,
            )
            run['artifactIds'] = list(dict.fromkeys([*run.get('artifactIds', []), *step['artifactIds']]))
            repository.save_run(run)
            approval = repository.get_approval(run.get('pendingApprovalId')) if run.get('pendingApprovalId') else None
            if approval:
                self._record_approval_message(repository, run, approval)
            return

        solver_approval = self._create_approval(
            run_id=run['runId'],
            action='RUN_SOLVER',
            frozen_action={
                'solver': mapping['solver'],
                'caseSetId': load_import['importId'],
                'loadDatasetArtifactId': standard_artifact_id,
                'resources': {'processCount': 1, 'coresPerProcess': 1, 'executionTimeoutS': 7200},
            },
            summary=f'使用 {mapping["solver"]} 创建单次求解 Job；结果模式以 Job 证据为准。',
        )
        run.update({
            'status': 'WAITING_APPROVAL',
            'currentStage': 'SOLVER_APPROVAL',
            'pendingApprovalId': solver_approval['approvalId'],
            'artifactIds': list(dict.fromkeys([*run.get('artifactIds', []), *step['artifactIds']])),
            'updatedAt': utc_now(),
        })
        repository.save_run(run)
        self._record_approval_message(repository, run, solver_approval)

    def _prepare_engineering_analysis_approval(
        self,
        repository: AgentRepository,
        run: dict[str, Any],
        *,
        mapping: dict[str, Any],
        standard_artifact_id: str | None,
        standard_sha256: str | None,
    ) -> None:
        self._prepare_agent_approval(
            repository,
            run,
            self._agent_for('ANALYSIS'),
            mapping=mapping,
            standard_artifact_id=standard_artifact_id,
            standard_sha256=standard_sha256,
        )
    def _prepare_engineering_optimization_approval(
        self,
        repository: AgentRepository,
        run: dict[str, Any],
        *,
        mapping: dict[str, Any],
        standard_artifact_id: str | None,
        standard_sha256: str | None,
    ) -> None:
        self._prepare_agent_approval(
            repository,
            run,
            self._agent_for('DAMPER_OPTIMIZATION'),
            mapping=mapping,
            standard_artifact_id=standard_artifact_id,
            standard_sha256=standard_sha256,
        )
    def _prepare_damper_comparison_approval(
        self,
        repository: AgentRepository,
        run: dict[str, Any],
        *,
        mapping: dict[str, Any],
        standard_artifact_id: str | None,
        standard_sha256: str | None,
    ) -> None:
        self._prepare_agent_approval(
            repository,
            run,
            self._agent_for('DAMPER_COMPARISON'),
            mapping=mapping,
            standard_artifact_id=standard_artifact_id,
            standard_sha256=standard_sha256,
        )

    def _prepare_damper_parameter_sweep_approval(
        self,
        repository: AgentRepository,
        run: dict[str, Any],
        *,
        mapping: dict[str, Any],
        standard_artifact_id: str | None,
        standard_sha256: str | None,
    ) -> None:
        self._prepare_agent_approval(
            repository,
            run,
            self._agent_for('DAMPER_PARAMETER_SWEEP'),
            mapping=mapping,
            standard_artifact_id=standard_artifact_id,
            standard_sha256=standard_sha256,
        )
    def _execute_approved_value(
        self,
        repository: AgentRepository,
        run: dict[str, Any],
        approval: dict[str, Any],
    ) -> None:
        """按冻结审批动作创建唯一 Job；具体求解器仍由运行时工具执行。"""
        action = str(approval.get('action') or '')
        frozen = approval['frozenAction']
        frozen_hash = self._payload_sha256(frozen)
        if action == 'RUN_SOLVER':
            idempotency_key = f'{run["runId"]}:SOLVER:{sha256(json.dumps(frozen, sort_keys=True).encode()).hexdigest()}'
            tool_call_id = self._authorize_approved_execution(
                repository,
                run,
                idempotency_key=idempotency_key,
                effective_arguments={'runId': run['runId'], 'frozenAction': frozen},
            )
            existing = next(
                (step for step in repository.list_steps(run['runId']) if step['idempotencyKey'] == idempotency_key),
                None,
            )
            if existing and existing.get('jobId'):
                job_id = existing['jobId']
            elif run.get('taskType') == 'ANALYSIS':
                dispatch = self._analysis_runtime_tools().dispatch(
                    {'run_id': run['runId'], 'frozen_action': frozen},
                    approved=True,
                    idempotency_key=idempotency_key,
                )
                job_id = dispatch.job_id
            else:
                job = platform_store.create_job('SOLVER_BATCH', {**frozen, 'agentRunId': run['runId']})
                job_id = job.job_id
            if not existing or not existing.get('jobId'):
                repository.save_step({
                    'stepId': gen_id('step'),
                    'runId': run['runId'],
                    'idempotencyKey': idempotency_key,
                    'title': '创建单次求解任务',
                    'status': 'WAITING_JOB',
                    'jobId': job_id,
                    'createdAt': utc_now(),
                })
            self._finish_execution_tool_call(
                repository,
                run,
                call_id=tool_call_id,
                job_id=job_id,
            )
            self._validate_run_transition(run, 'WAITING_JOB')
            run.update({
                'status': 'WAITING_JOB',
                'currentStage': 'SOLVER_EXECUTION',
                'pendingApprovalId': None,
                'jobId': job_id,
                'updatedAt': utc_now(),
            })
        elif action in {'RUN_ENGINEERING_WORKFLOW', 'RUN_FULL_OPTIMIZATION', 'RUN_DAMPER_COMPARISON', 'RUN_DAMPER_PARAMETER_SWEEP'}:
            prefix, title = {
                'RUN_ENGINEERING_WORKFLOW': ('FULL_OPTIMIZATION', '创建工程优化任务'),
                'RUN_FULL_OPTIMIZATION': ('FULL_OPTIMIZATION', '创建完整优化任务'),
                'RUN_DAMPER_COMPARISON': ('DAMPER_COMPARISON', '创建双工况阻尼器对比任务'),
                'RUN_DAMPER_PARAMETER_SWEEP': ('DAMPER_PARAMETER_SWEEP', '创建阻尼器参数批量任务'),
            }[action]
            idempotency_key = f'{run["runId"]}:{prefix}:{frozen_hash}'
            tool_call_id = self._authorize_approved_execution(
                repository,
                run,
                idempotency_key=idempotency_key,
                effective_arguments={'runId': run['runId'], 'frozenAction': frozen},
            )
            platform_store.refresh()
            existing_job = next(
                (job for job in platform_store.jobs if job.request.get('agentIdempotencyKey') == idempotency_key),
                None,
            )
            if existing_job is None:
                params = {key: value for key, value in frozen.items() if key != 'jobType'}
                job = platform_store.create_job(
                    frozen['jobType'],
                    {
                        **params,
                        'agentRunId': run['runId'],
                        'agentIdempotencyKey': idempotency_key,
                        'frozenActionSha256': frozen_hash,
                    },
                )
                job_id = job.job_id
            else:
                job_id = existing_job.job_id
            if not any(step['idempotencyKey'] == idempotency_key for step in repository.list_steps(run['runId'])):
                repository.save_step({
                    'stepId': gen_id('step'),
                    'runId': run['runId'],
                    'idempotencyKey': idempotency_key,
                    'title': title,
                    'status': 'WAITING_JOB',
                    'jobId': job_id,
                    'frozenActionSha256': frozen_hash,
                    'createdAt': utc_now(),
                })
            self._finish_execution_tool_call(
                repository,
                run,
                call_id=tool_call_id,
                job_id=job_id,
            )
            self._validate_run_transition(run, 'WAITING_JOB')
            run.update({
                'status': 'WAITING_JOB',
                'currentStage': 'WAITING_JOB',
                'pendingApprovalId': None,
                'jobId': job_id,
                'frozenActionSha256': frozen_hash,
                'updatedAt': utc_now(),
            })
        else:
            raise HTTPException(status_code=422, detail={'code': 'UNKNOWN_APPROVAL_ACTION', 'message': action})
        repository.save_run(run)

    def _reflect_damper_comparison(self, job: dict[str, Any]) -> dict[str, Any]:
        return orchestration_handler_or_generic('DAMPER_COMPARISON').reflect(
            self._agent_for('DAMPER_COMPARISON'),
            job,
        )
    def _reflect_full_optimization(
        self,
        job: dict[str, Any],
        *,
        run: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return orchestration_handler_or_generic('FULL_OPTIMIZATION').reflect(
            self._agent_for('FULL_OPTIMIZATION'),
            job,
            run=run,
        )
    def _register_full_optimization_report(
        self,
        run: dict[str, Any],
        job: dict[str, Any],
        reflection: dict[str, Any],
    ):
        outcome = ReviewOutcome(
            accepted=bool(reflection['accepted']),
            run_status=str(reflection.get('runStatus') or ('SUCCEEDED' if reflection['accepted'] else reflection['evidenceMode'])),
            evidence_mode=str(reflection['evidenceMode']),
            checks=dict(reflection['checks']),
            message=str(reflection['message']),
            extra={key: reflection[key] for key in ('validationStatus', 'reviewStatus', 'finalRecommendationStatus') if key in reflection},
        )
        return self._register_agent_report(run, job, outcome, self._agent_for('FULL_OPTIMIZATION'))
    @staticmethod
    def _payload_sha256(payload: dict[str, Any]) -> str:
        return sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
        ).hexdigest()

    def _create_approval(
        self,
        *,
        run_id: str,
        action: str,
        frozen_action: dict[str, Any],
        summary: str,
        narrative_mode: str | None = None,
        narrative_fallback_reason: str | None = None,
    ) -> dict[str, Any]:
        now = utc_now()
        approval = {
            'approvalId': gen_id('approval'),
            'runId': run_id,
            'action': action,
            'status': 'PENDING',
            'summary': summary,
            'narrativeMode': narrative_mode,
            'narrativeFallbackReason': narrative_fallback_reason,
            'frozenAction': frozen_action,
            'frozenActionSha256': self._payload_sha256(frozen_action),
            'createdAt': now,
            'updatedAt': now,
        }
        self.repository().save_approval(approval)
        return approval

    def _record_approval_message(
        self,
        repository: AgentRepository,
        run: dict[str, Any],
        approval: dict[str, Any],
    ) -> None:
        """把每一版待审批方案作为独立助手消息落库，保留完整审批历史。"""
        approval_id = approval.get('approvalId')
        session_id = run.get('sessionId')
        if not approval_id or not session_id:
            return
        list_messages = getattr(repository, 'list_messages', None)
        if callable(list_messages) and any(
            message.get('approvalId') == approval_id
            for message in list_messages(session_id)
        ):
            return
        repository.add_message({
            'messageId': gen_id('msg'),
            'sessionId': session_id,
            'role': 'ASSISTANT',
            'messageType': 'APPROVAL',
            'approvalId': approval_id,
            'content': str(approval.get('summary') or '请确认是否执行该工程计划。'),
            'runId': run.get('runId'),
            'createdAt': utc_now(),
        })
        get_session = getattr(repository, 'get_session', None)
        session = get_session(session_id) if callable(get_session) else None
        if session is not None:
            session['updatedAt'] = utc_now()
            repository.save_session(session)

    def _decorate_run(self, run: dict[str, Any]) -> dict[str, Any]:
        repository = self.repository()
        self._sync_workflow_runtime(repository, run)
        approval = repository.get_approval(run['pendingApprovalId']) if run.get('pendingApprovalId') else None
        list_tool_calls = getattr(repository, 'list_tool_calls', None)
        tool_calls = list_tool_calls(run['runId']) if callable(list_tool_calls) else []
        return {
            **self._with_inquiry_projection(self._with_result_metadata(run)),
            'pendingApproval': approval,
            'steps': repository.list_steps(run['runId']),
            'toolCalls': tool_calls,
            'jobProgress': self._job_progress(run.get('jobId')),
        }

    def _reflect_damper_parameter_sweep(self, job: dict[str, Any], *, run: dict[str, Any] | None = None) -> dict[str, Any]:
        return orchestration_handler_or_generic('DAMPER_PARAMETER_SWEEP').reflect(
            self._agent_for('DAMPER_PARAMETER_SWEEP'),
            job,
            run=run,
        )

    def _with_result_metadata(self, run: dict[str, Any]) -> dict[str, Any]:
        """给已完成工程结果补充可读的结果标记和下载文件元数据。"""
        if (
            run.get('taskType') not in ENGINEERING_TASK_TYPES
            or run.get('status') not in {'SUCCEEDED', 'COMPLETED_DIAGNOSTIC'}
        ):
            return run
        result_artifacts = []
        for artifact_id in run.get('artifactIds') or []:
            try:
                artifact = platform_store.get_artifact(str(artifact_id)).artifact
            except (HTTPException, KeyError):
                continue
            result_artifacts.append({
                'artifactId': artifact.artifact_id,
                'name': artifact.name,
                'kind': artifact.kind,
                'sizeBytes': artifact.size_bytes,
            })
        if run.get('taskType') in {'FULL_OPTIMIZATION', 'DAMPER_OPTIMIZATION'}:
            # 旧任务可能在合并制品功能上线前完成；首次打开结果时补齐合并工作簿。
            source_records = []
            for artifact_id in run.get('artifactIds') or []:
                try:
                    source_records.append(platform_store.get_artifact(str(artifact_id)))
                except (HTTPException, KeyError):
                    continue
            optimization_record = next(
                (
                    record for record in source_records
                    if record.artifact.name in {'real_optimization_summary.json', 'optimization_summary.json'}
                ),
                None,
            )
            source_run_id = (
                getattr(optimization_record.artifact, 'run_id', None) if optimization_record else None
            ) or next(
                (
                    getattr(record.artifact, 'run_id', None)
                    for record in source_records
                    if getattr(record.artifact, 'run_id', None)
                ),
                None,
            )
            if source_run_id:
                workbooks = platform_store.ensure_optimization_metric_workbooks(
                    run_id=str(source_run_id),
                    artifact_ids=[str(artifact_id) for artifact_id in run.get('artifactIds') or []],
                )
                if workbooks:
                    result_artifacts = [
                        {
                            'artifactId': artifact.artifact_id,
                            'name': artifact.name,
                            'kind': artifact.kind,
                            'sizeBytes': artifact.size_bytes,
                        }
                        for artifact in workbooks
                    ]
        return {
            **run,
            'resultMetadata': self._result_metadata(run),
            'resultArtifacts': result_artifacts,
        }

    def _with_inquiry_projection(self, run: dict[str, Any]) -> dict[str, Any]:
        """把旧查询轨迹投影成结构化展示，不改写历史审计记录。"""
        if run.get('taskType') != 'INQUIRY':
            return run
        summary = run.get('resultSummary')
        if not isinstance(summary, dict):
            return run
        if summary.get('inquiryMetrics') and not summary.get('inquiryTopsis'):
            return run
        if summary.get('inquiryTopsis') and summary.get('inquiryTopsisWeights'):
            return run
        facts = run.get('inquiryFacts')
        queries = facts.get('queries') if isinstance(facts, dict) else None
        if not isinstance(queries, list):
            return run
        metrics = self._structured_inquiry_metrics(queries)
        topsis = [
            dict(row)
            for item in queries
            if isinstance(item, dict) and item.get('tool') == 'result.topsis'
            for row in ((item.get('output') or {}).get('rows') or [])
            if isinstance(row, dict)
        ]
        topsis_weights = None
        for item in queries:
            if not isinstance(item, dict) or item.get('tool') != 'result.topsis':
                continue
            output = item.get('output') or {}
            names = output.get('objectiveNames') or output.get('objective_names') or []
            weights = output.get('weights') or []
            if isinstance(names, list) and isinstance(weights, list) and names and weights:
                topsis_weights = {
                    'objectiveNames': [str(name).split(':', 1)[-1] for name in names],
                    'weights': [float(weight) for weight in weights],
                }
                break
        if not metrics and not topsis:
            return run
        return {
            **run,
            'resultSummary': {
                **summary,
                'message': (
                    f'已读取 TOPSIS 前 {len(topsis)} 项候选，排名和数值均来自已登记的优化摘要。'
                    if topsis else self._deterministic_inquiry_answer(queries)
                ),
                'inquiryMetrics': metrics,
                'inquiryTopsis': topsis,
                **({'inquiryTopsisWeights': topsis_weights} if topsis_weights else {}),
                'queryProgress': {
                    'completed': len(metrics) + len(topsis),
                    'message': f'已读取 {len(metrics) + len(topsis)} 项结果指标',
                },
            },
        }

    @staticmethod
    def _job_progress(job_id: str | None) -> dict[str, Any] | None:
        """把 Job 的求解进度透出给前端。

        进度是观测信号，读不到不能影响运行状态查询，但也不能静默：
        Job 不存在只记一条 warning（该 run 本就没有求解通道），Schema、
        SQLite 或序列化出错则记完整堆栈并回一个可见的错误标记，让前端能区分
        "这条路径不上报进度" 和 "进度通道坏了"。
        """

        if not job_id:
            return None
        try:
            job = platform_store.get_job(job_id)
        except HTTPException:
            logger.warning(
                'job progress unavailable: job %s not found',
                job_id,
                extra={
                    'event': 'job_progress_job_missing',
                    'job_id': job_id,
                    'error_code': 'JOB_PROGRESS_JOB_MISSING',
                },
            )
            return None
        except Exception:
            logger.exception(
                'job progress lookup failed: %s',
                job_id,
                extra={
                    'event': 'job_progress_read_failed',
                    'job_id': job_id,
                    'error_code': 'JOB_PROGRESS_READ_ERROR',
                },
            )
            return {'phase': '进度不可用', 'message': '读取求解进度失败，求解本身不受影响；详见服务端日志。'}
        progress = getattr(job, 'progress', None)
        if progress is None:
            return None
        try:
            return progress.model_dump(by_alias=True, mode='json')
        except Exception:
            logger.exception(
                'job progress serialisation failed: %s',
                job_id,
                extra={
                    'event': 'job_progress_serialise_failed',
                    'job_id': job_id,
                    'error_code': 'JOB_PROGRESS_SERIALISE_ERROR',
                },
            )
            return {'phase': '进度不可用', 'message': '求解进度数据无法序列化，求解本身不受影响；详见服务端日志。'}

    def _register_run_report(self, run: dict[str, Any], job: dict[str, Any]):
        report = {
            'agentRunId': run['runId'],
            'goal': run['goal'],
            'jobId': job['jobId'],
            'jobStatus': job['status'],
            'executionMode': 'PLATFORM_JOB',
            'isVerifiedRealFem': bool((job.get('result') or {}).get('isVerifiedSolverOutput', False)),
            'artifactIds': [artifact['artifactId'] for artifact in job.get('artifacts', [])],
            'limitations': '只有 Job 明确提供 isVerifiedSolverOutput=true 时，结果才可作为真实 FEM 证据。',
        }
        safe_report, report_content = _json_artifact_payload(report)
        return platform_store.register_artifact(
            kind='JSON_SUMMARY',
            name=f'{run["runId"]}_evidence_report.json',
            path=f'output/platform_store/agent_runs/{run["runId"]}/evidence_report.json',
            mime_type='application/json',
            preview=safe_report,
            content=report_content,
        )

    @staticmethod
    def _required(value: Any, code: str, message: str) -> Any:
        if value is None:
            raise HTTPException(status_code=404, detail={'code': code, 'message': message})
        return value

agent_service = AgentService()
