from __future__ import annotations

import importlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / 'momo_agent' / 'backend'
sys.path.insert(0, str(BACKEND))

HARNESS = BACKEND / 'app' / 'services' / 'agent_harness.py'
RUNTIME = BACKEND / 'app' / 'capabilities' / 'runtime.py'
TEST_HARNESS = BACKEND / 'tests' / 'test_agent_harness.py'
TEST_RUNTIME = BACKEND / 'tests' / 'test_engineering_capability_runtime.py'


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f'{label}: expected exactly one match, got {count}')
    return text.replace(old, new, 1)


def replace_regex_once(text: str, pattern: str, replacement: str, label: str) -> str:
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
    if count != 1:
        raise RuntimeError(f'{label}: expected exactly one regex match, got {count}')
    return updated


POLICIES: dict[str, tuple[tuple[str, ...], str]] = {
    'workflow.start': (('SESSION_ACTIVE', 'INTENT_RESOLVED'), 'NONE'),
    'workflow.observe': (('ACTIVE_RUN',), 'NONE'),
    'workflow.complete': (('WORKFLOW_GATES_PASSED', 'REPORT_READY'), 'REGISTERED_ARTIFACT_ONLY'),
    'approval.request': (('FROZEN_CONTRACT', 'PREFLIGHT_PASSED'), 'NONE'),
    'approval.decide': (('PENDING_APPROVAL',), 'NONE'),
    'evidence.verify': (('REGISTERED_RESULT',), 'REGISTERED_ARTIFACT_ONLY'),
    'analysis.plan': (('INTENT_RESOLVED',), 'NONE'),
    'analysis.prepare': (('MODEL_READY', 'LOAD_READY'), 'NONE'),
    'analysis.run': (('FROZEN_CONTRACT', 'PREFLIGHT_PASSED', 'APPROVAL_GRANTED'), 'REAL_FEM_REQUIRED'),
    'analysis.review': (('REAL_FEM_RESULT', 'REGISTERED_RESULT'), 'REAL_FEM_REQUIRED'),
    'analysis.visualize': (('EVIDENCE_REVIEW_PASSED', 'REGISTERED_RESULT'), 'REGISTERED_ARTIFACT_ONLY'),
    'result.extract': (('REAL_FEM_JOB_SUCCEEDED',), 'REAL_FEM_REQUIRED'),
    'result.find_recent': (('TERMINAL_RUN_AVAILABLE',), 'NONE'),
    'load.inspect': (('REGISTERED_LOAD_ARTIFACT',), 'REGISTERED_ARTIFACT_ONLY'),
    'load.map_targets': (('REGISTERED_LOAD_ARTIFACT', 'MODEL_TARGETS_REGISTERED'), 'REGISTERED_ARTIFACT_ONLY'),
    'comparison.plan': (('INTENT_RESOLVED',), 'NONE'),
    'comparison.calibrate': (('COMPARISON_CASES_FROZEN',), 'NONE'),
    'comparison.prepare': (('CALIBRATION_READY', 'MODEL_READY', 'LOAD_READY'), 'NONE'),
    'comparison.run': (('FROZEN_CONTRACT', 'PREFLIGHT_PASSED', 'APPROVAL_GRANTED'), 'REAL_FEM_REQUIRED'),
    'comparison.compare': (('REAL_FEM_CASES_SUCCEEDED', 'REGISTERED_RESULT'), 'REAL_FEM_REQUIRED'),
    'comparison.review': (('COMPARISON_RESULT_REGISTERED',), 'REAL_FEM_REQUIRED'),
    'sweep.plan': (('INTENT_RESOLVED',), 'NONE'),
    'sweep.prepare': (('SWEEP_CASES_FROZEN', 'MODEL_READY', 'LOAD_READY'), 'NONE'),
    'sweep.run': (('FROZEN_CONTRACT', 'PREFLIGHT_PASSED', 'APPROVAL_GRANTED'), 'REAL_FEM_REQUIRED'),
    'sweep.review': (('REAL_FEM_CASES_SUCCEEDED', 'REGISTERED_RESULT'), 'REAL_FEM_REQUIRED'),
    'optimization.prepare_plan': (('INTENT_RESOLVED',), 'NONE'),
    'optimization.preflight': (('OPTIMIZATION_PLAN_FROZEN', 'MODEL_READY', 'LOAD_READY'), 'NONE'),
    'optimization.run_baseline': (('FROZEN_CONTRACT', 'PREFLIGHT_PASSED', 'APPROVAL_GRANTED'), 'REAL_FEM_REQUIRED'),
    'optimization.run_doe': (('FROZEN_CONTRACT', 'PREFLIGHT_PASSED', 'APPROVAL_GRANTED'), 'REAL_FEM_REQUIRED'),
    'optimization.fit_surrogate': (('DOE_RESULTS_REGISTERED',), 'REGISTERED_ARTIFACT_ONLY'),
    'optimization.active_learning': (('SURROGATE_VALIDATED', 'PREFLIGHT_PASSED', 'APPROVAL_GRANTED'), 'REAL_FEM_REQUIRED'),
    'optimization.rank_candidates': (('SURROGATE_VALIDATED', 'CANDIDATE_SPACE_FROZEN'), 'REGISTERED_ARTIFACT_ONLY'),
    'optimization.recommend': (('CANDIDATE_RANKING_REGISTERED',), 'REGISTERED_ARTIFACT_ONLY'),
    'optimization.validate_candidates': (('RECOMMENDATION_REGISTERED', 'PREFLIGHT_PASSED', 'APPROVAL_GRANTED'), 'REAL_FEM_REQUIRED'),
    'optimization.review': (('CANDIDATE_VALIDATION_REGISTERED',), 'REAL_FEM_REQUIRED'),
    'result.columns': (('REGISTERED_RESULT',), 'REGISTERED_ARTIFACT_ONLY'),
    'result.peak': (('REGISTERED_RESULT',), 'REGISTERED_ARTIFACT_ONLY'),
    'result.at_time': (('REGISTERED_RESULT',), 'REGISTERED_ARTIFACT_ONLY'),
    'result.correlate': (('REGISTERED_RESULT',), 'REGISTERED_ARTIFACT_ONLY'),
    'result.compare': (('REGISTERED_RESULT',), 'REGISTERED_ARTIFACT_ONLY'),
    'result.compare_runs': (('PROJECT_BOUND', 'VERIFIED_RESULT_AVAILABLE'), 'REGISTERED_ARTIFACT_ONLY'),
    'result.topsis': (('REGISTERED_RESULT',), 'REGISTERED_ARTIFACT_ONLY'),
    'result.sweep_cases': (('REGISTERED_RESULT',), 'REGISTERED_ARTIFACT_ONLY'),
}


# Import the PR8 source once, before rewriting it, so descriptions/input models/risk metadata
# are migrated mechanically rather than copied into a second hand-maintained table.
harness_module = importlib.import_module('app.services.agent_harness')
legacy_specs = getattr(harness_module, '_HARNESS_TOOL_SPECS')
if set(legacy_specs) != set(POLICIES):
    missing = sorted(set(legacy_specs) - set(POLICIES))
    extra = sorted(set(POLICIES) - set(legacy_specs))
    raise RuntimeError(f'policy map mismatch: missing={missing}, extra={extra}')

capability_lines: list[str] = []
for capability_id, spec in sorted(legacy_specs.items()):
    prerequisites, evidence_policy = POLICIES[capability_id]
    capability_lines.extend([
        '    EngineeringCapability(',
        f'        capability_id={capability_id!r},',
        f'        description={spec.description!r},',
        f'        input_model={spec.input_model.__name__},',
        f'        risk=ToolRisk.{spec.risk.name},',
        f'        requires_approval={spec.requires_approval!r},',
        f'        prerequisites={prerequisites!r},',
        f'        evidence_policy=EvidencePolicy.{evidence_policy},',
        f'        idempotency_key_source={spec.idempotency_key_source!r},',
        '    ),',
    ])

capability_block = '''_WORKFLOW_OBSERVATION_TOOLS: frozenset[str] = frozenset({'workflow.observe'})


_CAPABILITY_REGISTRY = CapabilityRegistry()
for _capability in (
''' + '\n'.join(capability_lines) + '''
):
    _CAPABILITY_REGISTRY.register(_capability)

_CAPABILITY_DISPATCHER = CapabilityDispatcher(_CAPABILITY_REGISTRY)


def harness_capability_registry() -> CapabilityRegistry:
    return _CAPABILITY_REGISTRY


def _workflow_capability_ids() -> set[str]:
    expected = {'workflow.start'} | set(_WORKFLOW_OBSERVATION_TOOLS)
    for task_type in (
        'ANALYSIS',
        'DAMPER_COMPARISON',
        'DAMPER_PARAMETER_SWEEP',
        'DAMPER_OPTIMIZATION',
        'RESULT_INQUIRY',
    ):
        for step in workflow_definition(task_type).steps:
            expected.update(step.allowed_tools)
    return expected


def _validate_capability_registry() -> None:
    expected = _workflow_capability_ids()
    registered = set(_CAPABILITY_REGISTRY.list_ids())
    missing = sorted(expected - registered)
    extra = sorted(registered - expected)
    if missing or extra:
        raise RuntimeError(
            f'Capability Registry 与冻结 Workflow 定义不一致: missing={missing}, extra={extra}',
        )


_validate_capability_registry()


# Tool Schema 只是 Capability 面向模型 API 的阶段投影。缓存只保存当前阶段投影，
# 不再维护任何全局 tool union 或第二份 schema 真源。
_TOOL_CATALOG_CACHE: dict[Any, list[dict[str, Any]]] = {}
_TOOL_CATALOG_LOCK = Lock()
'''

harness_text = HARNESS.read_text(encoding='utf-8')
harness_text = replace_regex_once(
    harness_text,
    r'@dataclass\(frozen=True\)\nclass HarnessToolSpec:.*?_CAPABILITY_DISPATCHER = CapabilityDispatcher\(_CAPABILITY_REGISTRY\)\n',
    capability_block,
    'replace legacy specs with direct capabilities',
)

harness_text = replace_regex_once(
    harness_text,
    r'\ndef harness_capability_registry\(\) -> CapabilityRegistry:.*?\ndef harness_step_tool_catalog\(',
    '\ndef harness_step_tool_catalog(',
    'remove duplicate registry/global tool union block',
)
# The previous regex intentionally removes the duplicate PR8 registry accessor plus union.
# Reinsert the single accessor/validator/cache block immediately before the step catalog.
insert_marker = '\ndef harness_step_tool_catalog('
if capability_block.strip() not in harness_text:
    raise RuntimeError('capability block unexpectedly absent after replacement')

harness_text = harness_text.replace(
    "    unknown = sorted(set(allowed_tools) - set(_HARNESS_TOOL_SPECS))",
    "    unknown = sorted(set(allowed_tools) - set(_CAPABILITY_REGISTRY.list_ids()))",
)

# PR8 persisted a second accessor/cache block after the dispatcher. The replacement above may
# have consumed our newly generated accessor if the regex was too broad; rebuild the region safely.
if 'def harness_capability_registry()' not in harness_text:
    anchor = '_CAPABILITY_DISPATCHER = CapabilityDispatcher(_CAPABILITY_REGISTRY)\n'
    tail = capability_block.split(anchor, 1)[1]
    harness_text = replace_once(
        harness_text,
        anchor,
        anchor + tail,
        'restore capability accessor and stage cache',
    )

# Bootstrap workflow.start: Dispatcher becomes the typed/allowed capability gate before Guard.
old_bootstrap_guard = '''            call = turn.tool_calls[0]
            try:
                WorkflowGuard().authorize(
                    workflow_snapshot=freeze_workflow(_bootstrap_definition())['workflowSnapshot'],
                    current_step='ROUTING',
                    tool_call=WorkflowToolCall(
                        name=call.name,
                        arguments=call.arguments,
                        risk=ToolRisk.MUTATING,
                        idempotencyKey=f'{session["sessionId"]}:{call.tool_call_id}',
                    ),
                )
'''
new_bootstrap_guard = '''            call = turn.tool_calls[0]
            try:
                capability = _CAPABILITY_REGISTRY.require(call.name)
                start = _CAPABILITY_DISPATCHER.authorize_and_validate(
                    call.name,
                    call.arguments,
                    allowed_capabilities=workflow_state['allowedTools'],
                    idempotency_key=f'{session["sessionId"]}:{call.tool_call_id}',
                )
                WorkflowGuard().authorize(
                    workflow_snapshot=freeze_workflow(_bootstrap_definition())['workflowSnapshot'],
                    current_step='ROUTING',
                    tool_call=WorkflowToolCall(
                        name=call.name,
                        arguments=start.model_dump(by_alias=True, mode='json'),
                        risk=capability.risk,
                        requiresApproval=capability.requires_approval,
                        idempotencyKey=f'{session["sessionId"]}:{call.tool_call_id}',
                    ),
                )
'''
harness_text = replace_once(harness_text, old_bootstrap_guard, new_bootstrap_guard, 'bootstrap dispatcher')
harness_text = replace_once(
    harness_text,
    "            try:\n                start = WorkflowStartInput.model_validate(call.arguments)\n                if start.engineering_intent is not None:\n",
    "            try:\n                if start.engineering_intent is not None:\n",
    'bootstrap remove direct model validation',
)

# Clarification workflow.start also goes through Dispatcher.
harness_text = replace_once(
    harness_text,
    "            try:\n                start = WorkflowStartInput.model_validate(call.arguments)\n                if start.task_type != run.get('taskType'):\n",
    "            try:\n                start = _CAPABILITY_DISPATCHER.authorize_and_validate(\n                    'workflow.start',\n                    call.arguments,\n                    allowed_capabilities=['workflow.start'],\n                    idempotency_key=f'{run[\"runId\"]}:clarify:{call.tool_call_id}',\n                )\n                if start.task_type != run.get('taskType'):\n",
    'clarification dispatcher',
)

# approval.decide invalid input is now a ToolExecutionError from Dispatcher, not ValidationError.
old_approval_error = '''        except ValidationError as exc:
            return self._create_harness_failure_run(
                repository,
                session,
                content,
                now,
                code='INPUT_VALIDATION_ERROR',
                message='approval.decide 参数未通过类型校验。',
                details={'message': str(exc)},
            )
'''
new_approval_error = '''        except ToolExecutionError as exc:
            return self._create_harness_failure_run(
                repository,
                session,
                content,
                now,
                code=exc.code,
                message=exc.message,
                details=exc.details,
            )
'''
harness_text = replace_once(harness_text, old_approval_error, new_approval_error, 'approval dispatcher error')

# Deterministic TOPSIS fallback uses the same Capability validation path as model calls.
harness_text = replace_once(
    harness_text,
    "            effective_arguments = _HARNESS_TOOL_SPECS['result.topsis'].input_model.model_validate(\n                arguments,\n            ).model_dump(by_alias=True, mode='json')\n",
    "            effective_arguments = _CAPABILITY_DISPATCHER.authorize_and_validate(\n                'result.topsis',\n                arguments,\n                allowed_capabilities=self._workflow_state_from_run(run)['allowedTools'],\n            ).model_dump(by_alias=True, mode='json')\n",
    'topsis fallback dispatcher',
)

# Native inquiry loop: every model-selected query capability is authorized/typed by Dispatcher.
old_inquiry_prefix = '''                try:
                    WorkflowGuard().authorize(
                        workflow_snapshot=run['workflowSnapshot'],
                        current_step='QUERY',
                        completed_steps=run['completedSteps'],
                        repeated_no_progress=repeated_no_progress,
                        tool_call=WorkflowToolCall(name=call.name, arguments=call.arguments),
                    )
                    if call.name == 'result.compare_runs':
                        try:
                            validated = ResultCompareRunsInput.model_validate(call.arguments)
                            effective_arguments = validated.model_dump(by_alias=True, mode='json')
'''
new_inquiry_prefix = '''                try:
                    capability = _CAPABILITY_REGISTRY.require(call.name)
                    validated = _CAPABILITY_DISPATCHER.authorize_and_validate(
                        call.name,
                        call.arguments,
                        allowed_capabilities=self._workflow_state_from_run(run)['allowedTools'],
                    )
                    effective_arguments = validated.model_dump(by_alias=True, mode='json')
                    WorkflowGuard().authorize(
                        workflow_snapshot=run['workflowSnapshot'],
                        current_step='QUERY',
                        completed_steps=run['completedSteps'],
                        repeated_no_progress=repeated_no_progress,
                        tool_call=WorkflowToolCall(
                            name=call.name,
                            arguments=effective_arguments,
                            risk=capability.risk,
                        ),
                    )
                    if call.name == 'result.compare_runs':
                        try:
'''
harness_text = replace_once(harness_text, old_inquiry_prefix, new_inquiry_prefix, 'inquiry dispatcher prefix')
harness_text = replace_once(
    harness_text,
    "                        output = tools.call(call.name, call.arguments).model_dump(by_alias=True, mode='json')\n                        effective_arguments = _HARNESS_TOOL_SPECS[call.name].input_model.model_validate(\n                            call.arguments,\n                        ).model_dump(by_alias=True, mode='json')\n",
    "                        output = tools.call(call.name, effective_arguments).model_dump(by_alias=True, mode='json')\n",
    'inquiry remove legacy validation',
)

# Approved real solver execution: Capability is the source for policy and Dispatcher validates it.
harness_text = harness_text.replace(
    '        spec = _HARNESS_TOOL_SPECS[tool_name]\n',
    '        capability = _CAPABILITY_REGISTRY.require(tool_name)\n',
    1,
)
harness_text = replace_once(
    harness_text,
    "        arguments = {'runId': run['runId']}\n        executed_arguments = effective_arguments or arguments\n",
    "        arguments = {'runId': run['runId']}\n        arguments = _CAPABILITY_DISPATCHER.authorize_and_validate(\n            tool_name,\n            arguments,\n            allowed_capabilities=[tool_name],\n            approved=True,\n            idempotency_key=idempotency_key,\n        ).model_dump(by_alias=True, mode='json')\n        executed_arguments = effective_arguments or arguments\n",
    'approved solver dispatcher',
)
harness_text = harness_text.replace('                risk=spec.risk,\n', '                risk=capability.risk,\n', 1)
harness_text = harness_text.replace('                requiresApproval=spec.requires_approval,\n', '                requiresApproval=capability.requires_approval,\n', 1)
harness_text = harness_text.replace('                approved=spec.requires_approval,\n', '                approved=capability.requires_approval,\n', 1)

# Python-owned post-job stages use Capability InputModel for shape and Dispatcher for validation.
old_python_args = '''    @staticmethod
    def _python_stage_arguments(name: str, run: dict[str, Any]) -> dict[str, Any]:
        """只按登记输入模型构造服务端阶段参数，不裁剪失败输入。"""
        spec = _HARNESS_TOOL_SPECS[name]
        fields = spec.input_model.model_fields
        arguments: dict[str, Any] = {}
        if 'run_id' in fields:
            arguments['runId'] = run['runId']
        if 'job_id' in fields:
            arguments['jobId'] = run['jobId']
        try:
            validated = spec.input_model.model_validate(arguments)
        except ValidationError as exc:
            raise ToolExecutionError(
                'INPUT_VALIDATION_ERROR',
                f'Python Job 阶段工具 {name} 的服务端参数不符合登记契约。',
                details={'toolName': name, 'message': str(exc)},
            ) from exc
        return validated.model_dump(by_alias=True, mode='json')
'''
new_python_args = '''    @staticmethod
    def _python_stage_arguments(name: str, run: dict[str, Any]) -> dict[str, Any]:
        """只按 Capability InputModel 构造服务端阶段参数；最终校验统一交给 Dispatcher。"""
        capability = _CAPABILITY_REGISTRY.require(name)
        fields = capability.input_model.model_fields
        arguments: dict[str, Any] = {}
        if 'run_id' in fields:
            arguments['runId'] = run['runId']
        if 'job_id' in fields:
            arguments['jobId'] = run['jobId']
        return arguments
'''
harness_text = replace_once(harness_text, old_python_args, new_python_args, 'python stage args')

harness_text = harness_text.replace(
    '        spec = _HARNESS_TOOL_SPECS[name]\n        arguments = WorkflowHarnessMixin._python_stage_arguments(name, run)\n        idempotency_key = call_id if spec.idempotency_key_source == \'SERVER_DERIVED\' else None\n',
    "        capability = _CAPABILITY_REGISTRY.require(name)\n        arguments = WorkflowHarnessMixin._python_stage_arguments(name, run)\n        idempotency_key = (\n            call_id if capability.idempotency_key_source == 'SERVER_DERIVED' else None\n        )\n",
    1,
)
harness_text = replace_once(
    harness_text,
    "        approved = runtime_approval if spec.requires_approval else False\n        WorkflowGuard().authorize(\n",
    "        approved = runtime_approval if capability.requires_approval else False\n        arguments = _CAPABILITY_DISPATCHER.authorize_and_validate(\n            name,\n            arguments,\n            allowed_capabilities=[name],\n            approved=approved,\n            idempotency_key=idempotency_key,\n        ).model_dump(by_alias=True, mode='json')\n        WorkflowGuard().authorize(\n",
    'python stage dispatcher',
)
harness_text = harness_text.replace('                risk=spec.risk,\n', '                risk=capability.risk,\n', 1)
harness_text = harness_text.replace('                requiresApproval=spec.requires_approval,\n', '                requiresApproval=capability.requires_approval,\n', 1)
harness_text = harness_text.replace("            'risk': spec.risk.value,\n", "            'risk': capability.risk.value,\n", 1)
harness_text = harness_text.replace(
    "                {'idempotencyKeySource': spec.idempotency_key_source}\n                if spec.idempotency_key_source\n",
    "                {'idempotencyKeySource': capability.idempotency_key_source}\n                if capability.idempotency_key_source\n",
    1,
)

# Persistent model loop: no TypedToolRegistry/legacy spec. Dispatcher validates the model call,
# then the existing deterministic stage handler runs and its output is Pydantic-validated.
old_persistent = '''            spec = _HARNESS_TOOL_SPECS[call.name]
            validated = spec.input_model.model_validate(call.arguments)
            effective_arguments = validated.model_dump(by_alias=True, mode='json')
            runtime_approval = (
                'WAITING_APPROVAL' in (run.get('completedSteps') or [])
                or any(
                    item.get('approved') is True and item.get('authorized') is True
                    for item in repository.list_tool_calls(run['runId'])
                )
            )
            idempotency_key = call_id if spec.idempotency_key_source == 'SERVER_DERIVED' else None
            WorkflowGuard().authorize(
                workflow_snapshot=snapshot,
                current_step=current,
                completed_steps=run.get('completedSteps') or [],
                step_attempt=int(run.get('stepAttempt') or 1),
                tool_call=WorkflowToolCall(
                    name=call.name,
                    arguments=call.arguments,
                    risk=spec.risk,
                    requiresApproval=spec.requires_approval,
                    approved=runtime_approval if spec.requires_approval else False,
                    idempotencyKey=idempotency_key,
                ),
            )
            registry = TypedToolRegistry()
            is_review_step = current in {'EVIDENCE_REVIEW', 'REVIEW'}

            def stage_handler(_payload: BaseModel) -> dict[str, Any]:
                if is_review_step:
                    if job_payload is None:
                        raise ToolExecutionError(
                            'JOB_RESULT_REQUIRED',
                            '证据审查需要真实 Job 结果。',
                        )
                    outcome = self._agent_for(str(run.get('taskType'))).review(
                        job_payload,
                        workflow_contract={
                            'taskType': run.get('taskType'),
                            **(run.get('workflowContract') or {}),
                        },
                    )
                    return {
                        'accepted': outcome.accepted,
                        'runStatus': outcome.run_status,
                        'evidenceMode': outcome.evidence_mode,
                        'checks': outcome.checks,
                        'message': outcome.message,
                        'extra': outcome.extra,
                    }
                return {
                    'source': 'platform_job',
                    'stage': current,
                    'artifactIds': artifact_ids,
                }

            registry.register(TypedAgentTool(
                name=call.name,
                description=spec.description,
                input_model=spec.input_model,
                output_model=HarnessReviewOutput if is_review_step else HarnessStageEvidenceOutput,
                risk=spec.risk,
                requires_approval=spec.requires_approval,
                handler=stage_handler,
            ))
            stage_output = registry.execute(
                call.name,
                call.arguments,
                approved=runtime_approval if spec.requires_approval else False,
                idempotency_key=idempotency_key,
            ).model_dump(by_alias=True, mode='json')
'''
new_persistent = '''            capability = _CAPABILITY_REGISTRY.require(call.name)
            runtime_approval = (
                'WAITING_APPROVAL' in (run.get('completedSteps') or [])
                or any(
                    item.get('approved') is True and item.get('authorized') is True
                    for item in repository.list_tool_calls(run['runId'])
                )
            )
            idempotency_key = (
                call_id if capability.idempotency_key_source == 'SERVER_DERIVED' else None
            )
            validated = _CAPABILITY_DISPATCHER.authorize_and_validate(
                call.name,
                call.arguments,
                allowed_capabilities=allowed_tools,
                approved=runtime_approval if capability.requires_approval else False,
                idempotency_key=idempotency_key,
            )
            effective_arguments = validated.model_dump(by_alias=True, mode='json')
            WorkflowGuard().authorize(
                workflow_snapshot=snapshot,
                current_step=current,
                completed_steps=run.get('completedSteps') or [],
                step_attempt=int(run.get('stepAttempt') or 1),
                tool_call=WorkflowToolCall(
                    name=call.name,
                    arguments=effective_arguments,
                    risk=capability.risk,
                    requiresApproval=capability.requires_approval,
                    approved=runtime_approval if capability.requires_approval else False,
                    idempotencyKey=idempotency_key,
                ),
            )
            is_review_step = current in {'EVIDENCE_REVIEW', 'REVIEW'}

            def stage_handler(_payload: BaseModel) -> dict[str, Any]:
                if is_review_step:
                    if job_payload is None:
                        raise ToolExecutionError(
                            'JOB_RESULT_REQUIRED',
                            '证据审查需要真实 Job 结果。',
                        )
                    outcome = self._agent_for(str(run.get('taskType'))).review(
                        job_payload,
                        workflow_contract={
                            'taskType': run.get('taskType'),
                            **(run.get('workflowContract') or {}),
                        },
                    )
                    return {
                        'accepted': outcome.accepted,
                        'runStatus': outcome.run_status,
                        'evidenceMode': outcome.evidence_mode,
                        'checks': outcome.checks,
                        'message': outcome.message,
                        'extra': outcome.extra,
                    }
                return {
                    'source': 'platform_job',
                    'stage': current,
                    'artifactIds': artifact_ids,
                }

            output_model = HarnessReviewOutput if is_review_step else HarnessStageEvidenceOutput
            stage_output = output_model.model_validate(stage_handler(validated)).model_dump(
                by_alias=True,
                mode='json',
            )
'''
harness_text = replace_once(harness_text, old_persistent, new_persistent, 'persistent loop dispatcher')
# Remaining persistent-loop audit record fields must also come from Capability.
harness_text = harness_text.replace("            'risk': spec.risk.value,\n", "            'risk': capability.risk.value,\n", 1)
harness_text = harness_text.replace(
    "            'approved': runtime_approval if spec.requires_approval else False,\n",
    "            'approved': runtime_approval if capability.requires_approval else False,\n",
    1,
)
harness_text = harness_text.replace(
    "                {'idempotencyKeySource': spec.idempotency_key_source}\n                if spec.idempotency_key_source\n",
    "                {'idempotencyKeySource': capability.idempotency_key_source}\n                if capability.idempotency_key_source\n",
    1,
)

# No legacy Harness Tool registry surface may remain.
harness_text = harness_text.replace('from dataclasses import dataclass\n', '')
harness_text = harness_text.replace('    TypedAgentTool,\n    TypedToolRegistry,\n', '')
for forbidden in (
    '_HARNESS_TOOL_SPECS',
    'HarnessToolSpec',
    '_MODEL_INVOCABLE_TOOLS',
    '_CAPABILITY_POLICY_OVERRIDES',
    'harness_tool_catalog',
    'TypedToolRegistry',
    'TypedAgentTool',
):
    if forbidden in harness_text:
        raise RuntimeError(f'legacy harness surface remains: {forbidden}')

HARNESS.write_text(harness_text, encoding='utf-8')

# Make policy fields constructor-required so future capabilities cannot silently rely on defaults.
runtime_text = RUNTIME.read_text(encoding='utf-8')
runtime_text = replace_once(
    runtime_text,
    "    input_model: type[BaseModel]\n    version: str = '1.0.0'\n    risk: ToolRisk = ToolRisk.READ_ONLY\n    requires_approval: bool = False\n    prerequisites: tuple[str, ...] = ()\n    evidence_policy: EvidencePolicy = EvidencePolicy.NONE\n",
    "    input_model: type[BaseModel]\n    risk: ToolRisk\n    requires_approval: bool\n    prerequisites: tuple[str, ...]\n    evidence_policy: EvidencePolicy\n    version: str = '1.0.0'\n",
    'require explicit capability policies',
)
RUNTIME.write_text(runtime_text, encoding='utf-8')

# Update harness tests: registry is the only capability truth; global union/legacy spec no longer exist.
test_text = TEST_HARNESS.read_text(encoding='utf-8')
test_text = replace_once(
    test_text,
    "    HarnessJobInput,\n    _HARNESS_TOOL_SPECS,\n    bootstrap_workflow_state,\n    harness_tool_catalog,\n    harness_step_tool_catalog,\n",
    "    HarnessJobInput,\n    bootstrap_workflow_state,\n    harness_capability_registry,\n    harness_step_tool_catalog,\n",
    'test harness imports',
)
new_catalog_test = '''def test_capability_registry_is_single_truth_and_keeps_compare_contracts_distinct() -> None:
    registry = harness_capability_registry()
    names = list(registry.list_ids())

    assert names == sorted(names)
    assert 'workflow.start' in names
    assert 'comparison.compare' in names
    assert 'result.compare' in names
    assert 'result.compare_runs' in names
    assert 'result.peak' in names
    assert 'result.delta' not in names
    assert 'result.ratio' not in names
    assert 'solver.capabilities' not in names

    workflow_start = registry.tool_schemas(['workflow.start'])[0]
    inquiry_compare = registry.tool_schemas(['result.compare'])[0]
    cross_run_compare = registry.tool_schemas(['result.compare_runs'])[0]
    engineering_compare = registry.require('comparison.compare')

    assert workflow_start['inputSchema'].get('additionalProperties') is False
    assert workflow_start['idempotencyKeySource'] == 'SERVER_DERIVED'
    intent_properties = workflow_start['inputSchema']['$defs']['EngineeringIntent']['properties']
    assert 'OpenSees' in intent_properties['solver']['description']
    assert '塔底内力' in intent_properties['responseIds']['description']
    start_properties = workflow_start['inputSchema']['properties']
    assert 'FullOptimizationStartIntent' not in workflow_start['inputSchema']['$defs']
    assert 'optimizationProfile' in intent_properties
    assert 'FULL' in str(intent_properties['optimizationProfile'])
    assert 'DAMPER_OPTIMIZATION' in str(start_properties['taskType'])
    assert 'FULL_OPTIMIZATION' not in str(start_properties['taskType'])

    assert engineering_compare.idempotency_key_source == 'SERVER_DERIVED'
    assert set(inquiry_compare['inputSchema']['properties']) == {'artifactId', 'columns'}
    assert set(inquiry_compare['inputSchema']['required']) == {'artifactId', 'columns'}
    assert 'CSV' in inquiry_compare['description']
    assert set(cross_run_compare['inputSchema']['properties']) == {'targets', 'baselineRunId', 'metricIds'}
    assert cross_run_compare['inputSchema']['properties']['targets']['maxItems'] == 8
    assert 'Project' in cross_run_compare['description']
    engineering_schema = engineering_compare.input_model.model_json_schema(by_alias=True)
    assert set(engineering_schema['properties']) == {'runId', 'jobId'}
    assert set(engineering_schema['required']) == {'runId', 'jobId'}
    assert '阻尼器' in engineering_compare.description

    for capability_id in names:
        descriptor = registry.require(capability_id).runtime_descriptor()
        assert descriptor['capabilityId'] == capability_id
        assert descriptor['sideEffect'] in {'NONE', 'ARTIFACT_WRITE', 'EXTERNAL_COMPUTE', 'STATE_MUTATION'}
        assert descriptor['approvalPolicy'] in {'NONE', 'REQUIRED'}
        assert isinstance(descriptor['prerequisites'], list)
        assert descriptor['evidencePolicy'] in {'NONE', 'REGISTERED_ARTIFACT_ONLY', 'REAL_FEM_REQUIRED'}

    expected = {'workflow.start', 'workflow.observe'}
    for task_type in (
        'ANALYSIS', 'DAMPER_COMPARISON', 'DAMPER_PARAMETER_SWEEP',
        'DAMPER_OPTIMIZATION', 'RESULT_INQUIRY',
    ):
        for step in workflow_definition(task_type).steps:
            expected.update(step.allowed_tools)
    assert set(names) == expected


'''
test_text = replace_regex_once(
    test_text,
    r'def test_harness_catalog_is_fixed_sorted_and_keeps_compare_contracts_distinct\(\) -> None:.*?(?=def test_step_tool_catalog_exposes_only_current_frozen_step_tools)',
    new_catalog_test,
    'replace legacy catalog test',
)
TEST_HARNESS.write_text(test_text, encoding='utf-8')

# Capability unit tests must declare all policies explicitly, matching the production contract.
runtime_test_text = TEST_RUNTIME.read_text(encoding='utf-8')
helper = '''\n\ndef _demo_capability() -> EngineeringCapability:\n    return EngineeringCapability(\n        capability_id='result.demo',\n        description='demo',\n        input_model=DemoInput,\n        risk=ToolRisk.READ_ONLY,\n        requires_approval=False,\n        prerequisites=(),\n        evidence_policy=EvidencePolicy.NONE,\n    )\n'''
runtime_test_text = replace_once(
    runtime_test_text,
    '\n\ndef test_capability_registry_uses_input_model_as_tool_schema_truth() -> None:\n',
    helper + '\n\ndef test_capability_registry_uses_input_model_as_tool_schema_truth() -> None:\n',
    'insert demo capability helper',
)
runtime_test_text, demo_count = re.subn(
    r"EngineeringCapability\(\s*capability_id='result\.demo',\s*description='demo',\s*input_model=DemoInput,?\s*\)",
    '_demo_capability()',
    runtime_test_text,
    flags=re.S,
)
if demo_count != 3:
    raise RuntimeError(f'expected three demo capability constructions, got {demo_count}')
TEST_RUNTIME.write_text(runtime_test_text, encoding='utf-8')

print('PR9 migration applied successfully')
