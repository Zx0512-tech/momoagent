from pathlib import Path
import re


def read(path: str) -> str:
    return Path(path).read_text(encoding='utf-8')


def write(path: str, text: str) -> None:
    Path(path).write_text(text, encoding='utf-8')


def exact(text: str, old: str, new: str, label: str, count: int = 1) -> str:
    actual = text.count(old)
    if actual != count:
        raise SystemExit(f'{label}: expected {count} exact matches, got {actual}')
    return text.replace(old, new)


def regex(text: str, pattern: str, repl: str, label: str, count: int = 1) -> str:
    updated, actual = re.subn(pattern, repl, text, count=count, flags=re.S)
    if actual != count:
        raise SystemExit(f'{label}: expected {count} regex matches, got {actual}')
    return updated


# agent_service: remove the historical execution constructor and its special imports.
path = 'momo_agent/backend/app/services/agent_service.py'
text = read(path)
text = exact(
    text,
    "from app.agents.damper_optimization import (\n    DamperOptimizationAgent,\n    FULL_OPTIMIZATION_CONTRACT,\n    FULL_OPTIMIZATION_PLAN,\n)\n",
    "from app.agents.damper_optimization import DamperOptimizationAgent\n",
    'agent_service imports',
)
text = regex(
    text,
    r"\n    def _create_full_optimization_run\([\s\S]*?(?=\n    def _try_auto_standardize\()",
    "\n",
    'agent_service legacy full constructor',
)
if 'FULL_OPTIMIZATION' in text:
    raise SystemExit('agent_service still contains FULL_OPTIMIZATION after migration')
write(path, text)

# conversation dispatcher: only canonical optimization may start a new run.
path = 'momo_agent/backend/app/services/agent_conversation.py'
text = read(path)
text = exact(
    text,
    "'AUTO', 'ANALYSIS', 'DAMPER_OPTIMIZATION', 'DAMPER_COMPARISON', 'DAMPER_PARAMETER_SWEEP', 'FULL_OPTIMIZATION',",
    "'AUTO', 'ANALYSIS', 'DAMPER_OPTIMIZATION', 'DAMPER_COMPARISON', 'DAMPER_PARAMETER_SWEEP',",
    'conversation harness task set',
)
text = regex(
    text,
    r"\n        if resolved_task == 'FULL_OPTIMIZATION':\n            return self\._create_full_optimization_run\([\s\S]*?\n            \)\n(?=        if resolved_task == 'CLARIFICATION':)",
    "\n",
    'conversation legacy full dispatch',
)
if 'FULL_OPTIMIZATION' in text:
    raise SystemExit('agent_conversation still contains FULL_OPTIMIZATION after migration')
write(path, text)

# Harness workflow.start schema and execution are canonical-only.
path = 'momo_agent/backend/app/services/agent_harness.py'
text = read(path)
text = regex(
    text,
    r"\nclass FullOptimizationStartIntent\(BaseModel\):[\s\S]*?(?=\nclass WorkflowStartInput\(BaseModel\):)",
    "\n",
    'harness full start model',
)
old = """    task_type: Literal[
        'ANALYSIS',
        'DAMPER_COMPARISON',
        'DAMPER_PARAMETER_SWEEP',
        'DAMPER_OPTIMIZATION',
        'FULL_OPTIMIZATION',
        'RESULT_INQUIRY',
    ] = Field(
        alias='taskType',
        description=(
            'FULL_OPTIMIZATION 只用于用户明确指定 ANSYS 的地震联合完整优化；'
            '显式 OpenSeesPy baseline-first 优化使用 DAMPER_OPTIMIZATION，不能为适配 Schema 改写求解器。'
        ),
    )
    engineering_intent: EngineeringIntent | None = Field(default=None, alias='engineeringIntent')
    full_optimization_intent: FullOptimizationStartIntent | None = Field(
        default=None,
        alias='fullOptimizationIntent',
    )
"""
new = """    task_type: Literal[
        'ANALYSIS',
        'DAMPER_COMPARISON',
        'DAMPER_PARAMETER_SWEEP',
        'DAMPER_OPTIMIZATION',
        'RESULT_INQUIRY',
    ] = Field(alias='taskType')
    engineering_intent: EngineeringIntent | None = Field(default=None, alias='engineeringIntent')
"""
text = exact(text, old, new, 'harness workflow start schema')
text = exact(
    text,
    "        'DAMPER_OPTIMIZATION',\n        'FULL_OPTIMIZATION',\n        'RESULT_INQUIRY',",
    "        'DAMPER_OPTIMIZATION',\n        'RESULT_INQUIRY',",
    'harness workflow tool registry',
)
old = """                if start.task_type == 'FULL_OPTIMIZATION':
                    if start.full_optimization_intent is None:
                        raise ValueError('FULL_OPTIMIZATION 澄清回复缺少 fullOptimizationIntent')
                    intent_for_plan = start.full_optimization_intent
                else:
                    if start.engineering_intent is None:
                        raise ValueError('澄清回复缺少 engineeringIntent')
                    intent_for_plan = start.engineering_intent
"""
new = """                if start.engineering_intent is None:
                    raise ValueError('澄清回复缺少 engineeringIntent')
                intent_for_plan = start.engineering_intent
"""
text = exact(text, old, new, 'harness clarification intent selection')
text = regex(
    text,
    r"\n        if start\.task_type == 'FULL_OPTIMIZATION':\n            # 完整优化使用专用 intent；[\s\S]*?\n            return self\._decorate_run\(stored_result\)\n(?=        resolved = self\._resolve_clarification\()",
    "\n",
    'harness clarification full restart branch',
)
old = """        elif task_type == 'FULL_OPTIMIZATION':
            result = self._create_full_optimization_run(
                repository,
                session,
                content,
                now,
                route_evidence=route_evidence,
                intent_override=start.full_optimization_intent,
            )
        else:
            intent = start.engineering_intent
            result = self._create_engineering_run(
"""
new = """        else:
            intent = start.engineering_intent
            result = self._create_engineering_run(
"""
text = exact(text, old, new, 'harness new full execution branch')
old = """        selected_solver = None
        if start.task_type == 'FULL_OPTIMIZATION' and start.full_optimization_intent is not None:
            selected_solver = start.full_optimization_intent.solver
        elif start.engineering_intent is not None:
            selected_solver = start.engineering_intent.solver
"""
new = """        selected_solver = (
            start.engineering_intent.solver if start.engineering_intent is not None else None
        )
"""
text = exact(text, old, new, 'harness solver preservation')
text = exact(
    text,
    "        if start.task_type == 'FULL_OPTIMIZATION':\n            return None if start.full_optimization_intent is not None else '缺少 fullOptimizationIntent'\n",
    "",
    'harness full semantic validation',
)
# Old frozen FULL snapshots can still be read; this is history projection only, not a start path.
text = text.replace(
    "normalized_task in {'DAMPER_OPTIMIZATION', 'FULL_OPTIMIZATION'}",
    "normalized_task in {'DAMPER_OPTIMIZATION', 'FULL_OPTIMIZATION'}  # historical snapshot only",
)
write(path, text)

# LLM routing/planning: FULL is a profile value, never a task type.
path = 'momo_agent/backend/app/services/agent_llm.py'
text = read(path)
text = exact(text, "    'FULL_OPTIMIZATION',\n", "", 'llm route tuple')
text = exact(
    text,
    "        'FULL_OPTIMIZATION', 'LOAD_IMPORT', 'CLARIFICATION',\n",
    "        'LOAD_IMPORT', 'CLARIFICATION',\n",
    'llm TaskRoute literal',
)
text = exact(
    text,
    "15. 必须保留用户明确指定的求解器。FULL_OPTIMIZATION 只用于用户明确要求 ANSYS 的地震联合完整优化；用户指定 OpenSeesPy 做 baseline-first 阻尼优化时选择 DAMPER_OPTIMIZATION，并在 engineeringIntent 中返回 OPENSEESPY_INPROC，绝不能为了适配 FULL_OPTIMIZATION Schema 把求解器改成 ANSYS。",
    "15. 必须保留用户明确指定的求解器。完整 baseline-first 阻尼优化仍使用 DAMPER_OPTIMIZATION，并在 engineeringIntent.optimizationProfile 返回 FULL；Profile 不得改写用户指定的求解器、荷载或阻尼器。",
    'harness system prompt profile rule',
)
old = """                        'taskType 仅允许 ANALYSIS, DAMPER_OPTIMIZATION, DAMPER_COMPARISON, '
                        'DAMPER_PARAMETER_SWEEP, '
                        'FULL_OPTIMIZATION, LOAD_IMPORT, CLARIFICATION, SMALL_TALK, '
"""
new = """                        'taskType 仅允许 ANALYSIS, DAMPER_OPTIMIZATION, DAMPER_COMPARISON, '
                        'DAMPER_PARAMETER_SWEEP, LOAD_IMPORT, CLARIFICATION, SMALL_TALK, '
"""
text = exact(text, old, new, 'llm router allowed types')
text = exact(
    text,
    "                        'DAMPER_OPTIMIZATION=想为某一种阻尼器找最优参数；'\n",
    "                        'DAMPER_OPTIMIZATION=想为某一种阻尼器找最优参数，包括完整 baseline-first 全流程；'\n",
    'llm router optimization definition',
)
text = exact(
    text,
    "                        'FULL_OPTIMIZATION=明确要求走完整 baseline-first 全流程优化；'\n",
    "",
    'llm router full definition',
)
text = regex(
    text,
    r"\n    def plan\(self, goal: str\) -> PlannerResult:[\s\S]*?(?=\n    def [a-zA-Z_])",
    "\n",
    'llm special full plan method',
)
text = regex(
    text,
    r"\n    def _payload\(self, goal: str\) -> dict\[str, Any\]:[\s\S]*?(?=\n    def [a-zA-Z_])",
    "\n",
    'llm special full payload method',
)
text = regex(
    text,
    r"\nclass AgentIntent\(BaseModel\):[\s\S]*?(?=\nclass EngineeringPlannerResult\(BaseModel\):)",
    "\n",
    'llm retired AgentIntent models',
)
text = exact(
    text,
    "        'selectedLayoutId', 'responseIds', 'budgetProfile', 'requiresRealFem',\n",
    "        'selectedLayoutId', 'responseIds', 'budgetProfile', 'optimizationProfile', 'requiresRealFem',\n",
    'engineering parser optimization profile',
)
text = text.replace(
    "'budgetProfile, requiresRealFem, missingFields, summary, '",
    "'budgetProfile, optimizationProfile, requiresRealFem, missingFields, summary, '",
)
text = text.replace(
    "'budgetProfile 必须为 STANDARD，requiresRealFem 必须为 true。",
    "'budgetProfile 必须为 STANDARD；optimizationProfile 仅允许 STANDARD, FULL, CUSTOM。用户明确要求完整/全流程 baseline-first 优化时返回 FULL，未明确时返回 STANDARD；requiresRealFem 必须为 true。",
)
if 'FULL_OPTIMIZATION' in text:
    raise SystemExit('agent_llm still contains FULL_OPTIMIZATION after migration')
write(path, text)

# Capability catalog advertises only the canonical controlled optimization task.
path = 'momo_agent/backend/app/services/real_execution/registry.py'
text = read(path)
text = regex(
    text,
    r"\n            CapabilityDescriptor\(\n                jobType='FULL_OPTIMIZATION',[\s\S]*?\n            \),",
    "",
    'real execution full capability descriptor',
)
text = exact(text, "            'FULL_OPTIMIZATION',\n", "", 'real execution resolver full task')
if 'FULL_OPTIMIZATION' in text:
    raise SystemExit('real_execution registry still contains FULL_OPTIMIZATION')
write(path, text)

# Retire tests whose sole purpose was executing the removed task branch.
Path('momo_agent/backend/tests/test_agent_full_optimization_api.py').unlink()
Path('momo_agent/backend/tests/test_full_optimization_profile_compat.py').unlink()
path = 'momo_agent/backend/tests/conftest.py'
text = read(path)
text = exact(text, "        'test_agent_full_optimization_api.py',\n", "", 'conftest retired full module')
write(path, text)

# Native PR3 invariants.
test_path = Path('momo_agent/backend/tests/test_optimization_execution_unified.py')
test_path.write_text('''from __future__ import annotations\n\nimport pytest\nfrom pydantic import ValidationError\n\nfrom app.agents.core import AgentContext\nfrom app.agents.damper_optimization import DamperOptimizationAgent\nfrom app.agents.task_registry import engineering_task_spec\nfrom app.agents.tools import ToolExecutionError\nfrom app.agents.workflows import workflow_definition\nfrom app.api.v1.agent_schemas import AgentMessageCreateRequest\nfrom app.services.agent_engineering import EngineeringIntent, resolve_optimization_profile\nfrom app.services.agent_harness import WorkflowStartInput\nfrom app.services.agent_task_handlers import DamperOptimizationTaskHandler, orchestration_handler\n\n\ndef _intent(profile: str = "FULL", solver: str = "OPENSEESPY_INPROC", load_kind: str = "WIND") -> EngineeringIntent:\n    return EngineeringIntent.model_validate({\n        "taskType": "DAMPER_OPTIMIZATION",\n        "solver": solver,\n        "damperType": "VISCOUS",\n        "loadKind": load_kind,\n        "selectedLayoutId": "TWO_PER_TOWER",\n        "responseIds": ["cumulative_displacement"],\n        "optimizationProfile": profile,\n        "requiresRealFem": True,\n        "missingFields": [],\n        "summary": "统一优化执行",\n    })\n\n\ndef test_optimization_agent_has_one_prepare_branch() -> None:\n    assert hasattr(DamperOptimizationAgent, "_prepare_optimization")\n    assert not hasattr(DamperOptimizationAgent, "_prepare_full_optimization")\n    assert not hasattr(DamperOptimizationAgent, "_prepare_engineering_optimization")\n\n\ndef test_full_profile_uses_canonical_task_and_preserves_solver_and_load() -> None:\n    intent = _intent()\n    contract = DamperOptimizationTaskHandler().build_contract_from_intent(intent, load_import=None)\n    policy = resolve_optimization_profile("FULL")\n    assert contract["taskType"] == "DAMPER_OPTIMIZATION"\n    assert contract["optimizationProfile"] == "FULL"\n    assert contract["optimizationPolicy"] == policy.model_dump(by_alias=True)\n    assert contract["solver"] == "OPENSEESPY_INPROC"\n    assert contract["loadKind"] == "WIND"\n\n\ndef test_full_task_is_not_registered_in_new_system() -> None:\n    assert engineering_task_spec("FULL_OPTIMIZATION") is None\n    assert orchestration_handler("FULL_OPTIMIZATION") is None\n    with pytest.raises(ToolExecutionError):\n        workflow_definition("FULL_OPTIMIZATION")\n\n\ndef test_new_api_and_harness_reject_retired_full_task_type() -> None:\n    with pytest.raises(ValidationError):\n        AgentMessageCreateRequest(content="完整优化", taskType="FULL_OPTIMIZATION")\n    with pytest.raises(ValidationError):\n        WorkflowStartInput.model_validate({"taskType": "FULL_OPTIMIZATION"})\n\n\ndef test_agent_context_only_accepts_canonical_optimization_task() -> None:\n    context = AgentContext(session_id="s1", goal="完整优化", requested_task="DAMPER_OPTIMIZATION")\n    assert context.requested_task == "DAMPER_OPTIMIZATION"\n    with pytest.raises(ValidationError):\n        AgentContext(session_id="s1", goal="完整优化", requested_task="FULL_OPTIMIZATION")\n''', encoding='utf-8')

# Remove temporary migration machinery from the final code tree.
for temporary in (
    '.github/workflows/pr3-native-migration.yml',
    '.github/workflows/pr3-native-migration-run.yml',
    'tools/pr3_native_migration.py',
):
    p = Path(temporary)
    if p.exists():
        p.unlink()
