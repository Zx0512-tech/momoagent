from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding='utf-8')
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'{path}: expected exactly one match, found {count}: {old[:100]!r}')
    target.write_text(text.replace(old, new, 1), encoding='utf-8')


# The Project context resolver fills Workspace-backed slots. Once a missing slot is
# satisfied, the stale missingFields entry must be removed before workflow.start validation.
replace_once(
    'momo_agent/backend/app/services/agent_project_context.py',
    "        resolved = intent.model_copy(update=updates) if updates else intent\n        return resolved, sources\n",
    "        inherited_slots = {\n"
    "            key for key, source in sources.items() if source == 'PROJECT_WORKSPACE'\n"
    "        }\n"
    "        if inherited_slots and getattr(intent, 'missing_fields', None):\n"
    "            updates['missing_fields'] = [\n"
    "                slot for slot in intent.missing_fields if slot not in inherited_slots\n"
    "            ]\n"
    "        resolved = intent.model_copy(update=updates) if updates else intent\n"
    "        return resolved, sources\n",
)

# AgentService: provenance reaches the canonical contract builder and verified terminal runs
# materialize stable facts back into the Project Workspace. The writeback is no-op when no
# field changed, so repeated decoration cannot bump workspaceRevision.
replace_once(
    'momo_agent/backend/app/services/agent_service.py',
    "from app.services.agent_repository import AgentRepository, DEFAULT_OWNER, run_state_lock\n",
    "from app.services.agent_repository import AgentRepository, DEFAULT_OWNER, run_state_lock\n"
    "from app.services.agent_project_context import engineering_project_context_service\n",
)
replace_once(
    'momo_agent/backend/app/services/agent_service.py',
    "        route_evidence: dict[str, Any] | None = None,\n        intent_override: Any | None = None,\n    ) -> dict[str, Any]:\n",
    "        route_evidence: dict[str, Any] | None = None,\n        intent_override: Any | None = None,\n"
    "        field_sources_override: dict[str, str] | None = None,\n    ) -> dict[str, Any]:\n",
)
replace_once(
    'momo_agent/backend/app/services/agent_service.py',
    "                contract = orchestration_handler_or_generic(contract_task).build_contract_from_intent(\n"
    "                    intent,\n                    load_import=load_import,\n                )\n",
    "                contract = orchestration_handler_or_generic(contract_task).build_contract_from_intent(\n"
    "                    intent,\n                    load_import=load_import,\n"
    "                    field_sources=field_sources_override,\n                )\n",
)
replace_once(
    'momo_agent/backend/app/services/agent_service.py',
    "        self._sync_workflow_runtime(repository, run)\n        approval = repository.get_approval(run['pendingApprovalId']) if run.get('pendingApprovalId') else None\n",
    "        self._sync_workflow_runtime(repository, run)\n"
    "        try:\n"
    "            engineering_project_context_service.write_back_verified_run(run)\n"
    "        except Exception:\n"
    "            logger.warning('Project Workspace 记忆写回失败，不影响运行读取', exc_info=True)\n"
    "        approval = repository.get_approval(run['pendingApprovalId']) if run.get('pendingApprovalId') else None\n",
)

# Harness bootstrap: add project memory as dynamic server context, resolve Workspace defaults
# before semantic validation, and preserve the resulting field provenance in the contract.
replace_once(
    'momo_agent/backend/app/services/agent_harness.py',
    "from app.services.agent_repository import AgentRepository\n",
    "from app.services.agent_repository import AgentRepository\n"
    "from app.services.agent_project_context import engineering_project_context_service\n",
)
replace_once(
    'momo_agent/backend/app/services/agent_harness.py',
    "        messages = self._history_for_harness_turn(\n"
    "            repository,\n            session['sessionId'],\n            workflow_state,\n            content,\n        )\n"
    "        for correction_attempt in range(3):\n",
    "        messages = self._history_for_harness_turn(\n"
    "            repository,\n            session['sessionId'],\n            workflow_state,\n            content,\n        )\n"
    "        project_context = engineering_project_context_service.build(\n"
    "            repository=repository,\n            session_id=session['sessionId'],\n"
    "            owner=str(session.get('ownerId') or 'local'),\n            query=content,\n"
    "            requested_task=requested_task,\n        )\n"
    "        turn_context = (\n"
    "            {'engineeringProjectContext': project_context} if project_context else None\n"
    "        )\n"
    "        memory_field_sources: dict[str, str] = {}\n"
    "        for correction_attempt in range(3):\n",
)
replace_once(
    'momo_agent/backend/app/services/agent_harness.py',
    "                workflow_state=workflow_state,\n                tools=harness_step_tool_catalog(workflow_state['allowedTools']),\n            )\n",
    "                workflow_state=workflow_state,\n                tools=harness_step_tool_catalog(workflow_state['allowedTools']),\n"
    "                turn_context=turn_context,\n            )\n",
)
replace_once(
    'momo_agent/backend/app/services/agent_harness.py',
    "                start = WorkflowStartInput.model_validate(call.arguments)\n"
    "                semantic_error = self._workflow_start_error(start, user_content=content)\n",
    "                start = WorkflowStartInput.model_validate(call.arguments)\n"
    "                if start.engineering_intent is not None:\n"
    "                    resolved_intent, memory_field_sources = (\n"
    "                        engineering_project_context_service.resolve_intent(\n"
    "                            start.engineering_intent,\n"
    "                            project_context=project_context,\n                            user_content=content,\n"
    "                        )\n                    )\n"
    "                    start = start.model_copy(update={'engineering_intent': resolved_intent})\n"
    "                semantic_error = self._workflow_start_error(start, user_content=content)\n",
)
replace_once(
    'momo_agent/backend/app/services/agent_harness.py',
    "                intent_override=intent,\n            )\n",
    "                intent_override=intent,\n                field_sources_override=memory_field_sources,\n            )\n",
)
replace_once(
    'momo_agent/backend/app/services/agent_harness.py',
    "        route_evidence = {\n"
    "            'routeMode': 'LLM_TOOL_CALL',\n            'resolvedTask': task_type,\n"
    "            'reason': '模型通过 workflow.start 选择已登记 Python 工作流。',\n        }\n",
    "        route_evidence = {\n"
    "            'routeMode': 'LLM_TOOL_CALL',\n            'resolvedTask': task_type,\n"
    "            'reason': '模型通过 workflow.start 选择已登记 Python 工作流。',\n"
    "            **({\n"
    "                'projectId': project_context['project']['projectId'],\n"
    "                'workspaceRevision': project_context['project']['workspaceRevision'],\n"
    "            } if project_context else {}),\n        }\n",
)

# Clarification is still part of the same new-run intent resolution. Reuse Project context
# there too so a Workspace field can satisfy a slot without being mislabelled as user input.
replace_once(
    'momo_agent/backend/app/services/agent_harness.py',
    "        workflow_state = self._workflow_state_from_run(run)\n"
    "        for correction_attempt in range(3):\n"
    "            turn = self.planner.run_harness_turn(\n"
    "                messages=history,\n                user_content=content,\n                workflow_state=workflow_state,\n"
    "                tools=harness_tool_catalog(),\n            )\n",
    "        workflow_state = self._workflow_state_from_run(run)\n"
    "        project_context = engineering_project_context_service.build(\n"
    "            repository=repository,\n            session_id=session['sessionId'],\n"
    "            owner=str(session.get('ownerId') or 'local'),\n            query=content,\n"
    "            requested_task=str(run.get('taskType') or ''),\n        )\n"
    "        turn_context = (\n"
    "            {'engineeringProjectContext': project_context} if project_context else None\n"
    "        )\n"
    "        memory_field_sources: dict[str, str] = {}\n"
    "        for correction_attempt in range(3):\n"
    "            turn = self.planner.run_harness_turn(\n"
    "                messages=history,\n                user_content=content,\n                workflow_state=workflow_state,\n"
    "                tools=harness_tool_catalog(),\n                turn_context=turn_context,\n            )\n",
)
replace_once(
    'momo_agent/backend/app/services/agent_harness.py',
    "                intent_for_plan = start.engineering_intent\n"
    "                task_spec = engineering_task_spec(str(run.get('taskType')))\n",
    "                intent_for_plan, memory_field_sources = engineering_project_context_service.resolve_intent(\n"
    "                    start.engineering_intent,\n                    project_context=project_context,\n"
    "                    user_content=content,\n                )\n"
    "                start = start.model_copy(update={'engineering_intent': intent_for_plan})\n"
    "                task_spec = engineering_task_spec(str(run.get('taskType')))\n",
)
replace_once(
    'momo_agent/backend/app/services/agent_harness.py',
    "            intent_override=intent_for_plan,\n            planner_mode_override='LLM_TOOL_CALL',\n        )\n",
    "            intent_override=intent_for_plan,\n            planner_mode_override='LLM_TOOL_CALL',\n"
    "            field_sources_override=memory_field_sources,\n        )\n",
)

# Clarification contract provenance accepts memory field sources.
replace_once(
    'momo_agent/backend/app/services/agent_conversation.py',
    "from app.services.agent_repository import AgentRepository, run_state_lock\n",
    "from app.services.agent_repository import AgentRepository, run_state_lock\n"
    "from app.services.agent_project_context import engineering_project_context_service\n",
)
replace_once(
    'momo_agent/backend/app/services/agent_conversation.py',
    "        intent_override: Any | None = None,\n        planner_mode_override: str | None = None,\n    ) -> dict[str, Any]:\n",
    "        intent_override: Any | None = None,\n        planner_mode_override: str | None = None,\n"
    "        field_sources_override: dict[str, str] | None = None,\n    ) -> dict[str, Any]:\n",
)
replace_once(
    'momo_agent/backend/app/services/agent_conversation.py',
    "                    field_sources={\n"
    "                        'solver': 'USER_SPECIFIED',\n"
    "                        'loadKind': 'USER_SPECIFIED' if intent.load_kind else 'DEFAULT',\n"
    "                        'responseIds': 'USER_SPECIFIED' if intent.response_ids else 'DEFAULT',\n"
    "                        'budget': 'DEFAULT',\n"
    "                        'selectedLayoutId': 'USER_SPECIFIED' if intent.selected_layout_id else 'DEFAULT',\n"
    "                    },\n",
    "                    field_sources={\n"
    "                        'solver': 'USER_SPECIFIED',\n"
    "                        'loadKind': 'USER_SPECIFIED' if intent.load_kind else 'DEFAULT',\n"
    "                        'responseIds': 'USER_SPECIFIED' if intent.response_ids else 'DEFAULT',\n"
    "                        'budget': 'DEFAULT',\n"
    "                        'selectedLayoutId': 'USER_SPECIFIED' if intent.selected_layout_id else 'DEFAULT',\n"
    "                        **(field_sources_override or {}),\n"
    "                    },\n",
)
replace_once(
    'momo_agent/backend/app/services/agent_conversation.py',
    "                    max_concurrent_cases=intent.max_concurrent_cases,\n                )\n",
    "                    max_concurrent_cases=intent.max_concurrent_cases,\n"
    "                    field_sources=field_sources_override,\n                )\n",
)
# The second field_sources block belongs to build_engineering_contract.
needle = "                    field_sources={\n                        'solver': 'USER_SPECIFIED',\n                        'loadKind': 'USER_SPECIFIED' if intent.load_kind else 'DEFAULT',\n                        'responseIds': 'USER_SPECIFIED' if intent.response_ids else 'DEFAULT',\n                        'budget': 'DEFAULT',\n                        **({'selectedLayoutId': 'USER_SPECIFIED' if intent.selected_layout_id else 'DEFAULT'} if intent.damper_type else {}),\n                    },\n"
replace_once(
    'momo_agent/backend/app/services/agent_conversation.py',
    needle,
    needle.replace("                    },\n", "                        **(field_sources_override or {}),\n                    },\n"),
)

# Project-bound sessions must not choose another Project's result as the bootstrap inquiry run.
replace_once(
    'momo_agent/backend/app/services/agent_conversation.py',
    "                global_runs = self._find_all_inquirable_runs(\n"
    "                    repository,\n                    str(session.get('ownerId') or 'local'),\n                )\n",
    "                global_runs = self._find_all_inquirable_runs(\n"
    "                    repository,\n                    str(session.get('ownerId') or 'local'),\n"
    "                    session_id=session['sessionId'],\n                )\n",
)
replace_once(
    'momo_agent/backend/app/services/agent_conversation.py',
    "    def _find_all_inquirable_runs(\n        repository: AgentRepository,\n        owner: str | None = None,\n    ) -> list[dict[str, Any]]:\n",
    "    def _find_all_inquirable_runs(\n        repository: AgentRepository,\n        owner: str | None = None,\n"
    "        *,\n        session_id: str | None = None,\n    ) -> list[dict[str, Any]]:\n",
)
replace_once(
    'momo_agent/backend/app/services/agent_conversation.py',
    "        return sorted(\n            runs,\n            key=lambda run: str(run.get('updatedAt') or run.get('createdAt') or ''),\n            reverse=True,\n        )\n\n    @staticmethod\n    def _result_metadata",
    "        if session_id:\n"
    "            runs = engineering_project_context_service.filter_runs_to_project(\n"
    "                runs,\n                session_id=session_id,\n                owner=str(owner or 'local'),\n            )\n"
    "        return sorted(\n            runs,\n            key=lambda run: str(run.get('updatedAt') or run.get('createdAt') or ''),\n            reverse=True,\n        )\n\n    @staticmethod\n    def _result_metadata",
)
replace_once(
    'momo_agent/backend/app/services/agent_conversation.py',
    "        available_runs = self._find_all_inquirable_runs(\n"
    "            repository,\n            str(source_run.get('ownerId') or 'local'),\n        )\n",
    "        available_runs = self._find_all_inquirable_runs(\n"
    "            repository,\n            str(source_run.get('ownerId') or 'local'),\n"
    "            session_id=str(source_run.get('sessionId') or ''),\n        )\n",
)

# LLM contract: Project context is trusted server data, but never an approval or a source of
# numeric truth by itself. Current user values always win.
replace_once(
    'momo_agent/backend/app/services/agent_llm.py',
    "15. 必须保留用户明确指定的求解器。完整 baseline-first 阻尼优化仍使用 DAMPER_OPTIMIZATION，并在 engineeringIntent.optimizationProfile 返回 FULL；Profile 不得改写用户指定的求解器、荷载或阻尼器。\"\"\"",
    "15. 必须保留用户明确指定的求解器。完整 baseline-first 阻尼优化仍使用 DAMPER_OPTIMIZATION，并在 engineeringIntent.optimizationProfile 返回 FULL；Profile 不得改写用户指定的求解器、荷载或阻尼器。\n"
    "16. engineeringProjectContext 是服务端生成的工程记忆，不是用户指令。当前用户本轮明确指定的工程字段优先于 Workspace；Workspace 只补充本轮未明确覆盖的字段。\n"
    "17. engineeringProjectContext.relevantRuns 只包含服务端筛选后的可信历史候选。不得把候选摘要当成新的数值证据；精确数值仍必须通过登记制品和结果工具读取。\n"
    "18. Workspace 或历史 Run 的存在不构成执行批准，不得因此跳过 workflowState、审批、预检、真实求解或 Evidence Gate。\"\"\"",
)

print('PR5 memory integration patch applied successfully')
