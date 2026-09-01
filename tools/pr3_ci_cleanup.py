from __future__ import annotations

from pathlib import Path
import re


def read(path: str) -> str:
    return Path(path).read_text(encoding='utf-8')


def write(path: str, text: str) -> None:
    Path(path).write_text(text, encoding='utf-8')


def exact(text: str, old: str, new: str, label: str, count: int = 1) -> str:
    actual = text.count(old)
    if actual != count:
        raise SystemExit(f'{label}: expected {count} matches, got {actual}')
    return text.replace(old, new)


def function_span(text: str, name: str) -> tuple[int, int]:
    match = re.search(rf'(?m)^def {re.escape(name)}\b', text)
    if not match:
        raise SystemExit(f'function not found: {name}')
    start = match.start()
    following = re.search(r'(?m)^(?:def |class )', text[match.end():])
    end = match.end() + following.start() if following else len(text)
    return start, end


def replace_in_function(text: str, name: str, old: str, new: str, *, count: int | None = None) -> str:
    start, end = function_span(text, name)
    block = text[start:end]
    actual = block.count(old)
    expected = actual if count is None else count
    if count is not None and actual != count:
        raise SystemExit(f'{name}: expected {count} matches for {old!r}, got {actual}')
    if actual == 0:
        raise SystemExit(f'{name}: no matches for {old!r}')
    return text[:start] + block.replace(old, new) + text[end:]


def replace_function(text: str, name: str, new_block: str) -> str:
    start, end = function_span(text, name)
    return text[:start] + new_block.rstrip() + '\n\n' + text[end:].lstrip('\n')


# Production: map only persisted legacy FULL runs onto the canonical workflow snapshot.
path = 'momo_agent/backend/app/services/agent_harness.py'
text = read(path)
text = exact(
    text,
    "        workflow_type = 'RESULT_INQUIRY' if task_type == 'INQUIRY' else task_type\n",
    "        # Historical persisted FULL runs may predate workflow snapshots.  Project them\n"
    "        # onto the canonical optimization workflow at read/resume time only; FULL is\n"
    "        # intentionally absent from WorkflowStartInput and the workflow registry.\n"
    "        if task_type == 'INQUIRY':\n"
    "            workflow_type = 'RESULT_INQUIRY'\n"
    "        elif task_type == 'FULL_OPTIMIZATION':\n"
    "            workflow_type = 'DAMPER_OPTIMIZATION'\n"
    "        else:\n"
    "            workflow_type = task_type\n",
    'historical workflow projection',
)
write(path, text)

# Production: remove retired FULL-specific helper names; behavior is canonical optimization.
path = 'momo_agent/backend/app/services/agent_service.py'
text = read(path)
text = exact(text, '    def _reflect_full_optimization(\n', '    def _reflect_optimization(\n', 'reflect helper name')
text = exact(text, '    def _register_full_optimization_report(\n', '    def _register_optimization_report(\n', 'report helper name')
write(path, text)

# Harness tests: canonical tasks for new execution, legacy FULL only in persistence tests.
path = 'momo_agent/backend/tests/test_agent_harness.py'
text = read(path)
text = exact(
    text,
    "    full_intent = workflow_start['inputSchema']['$defs']['FullOptimizationStartIntent']['properties']\n",
    "    assert 'FullOptimizationStartIntent' not in workflow_start['inputSchema']['$defs']\n"
    "    assert 'optimizationProfile' in intent_properties\n"
    "    assert 'FULL' in str(intent_properties['optimizationProfile'])\n",
    'catalog retired full schema',
)
text = exact(
    text,
    "    assert 'OpenSeesPy' in full_intent['solver']['description']\n",
    "",
    'catalog retired full solver assertion',
)
for name in (
    'test_persistent_loop_job_completion_stops_after_real_execution_step',
    'test_persistent_loop_asks_model_for_one_current_step_tool_and_persists_progress',
    'test_persistent_loop_stops_after_three_model_turn_failures',
    'test_job_completion_does_not_fast_forward_without_artifact_evidence',
    'test_approved_optimization_rejects_invalid_frozen_doe_budget',
    'test_python_job_completion_records_internal_optimization_stage_traces',
    'test_python_job_stage_traces_follow_registered_contracts_and_risks',
):
    text = replace_in_function(text, name, 'FULL_OPTIMIZATION', 'DAMPER_OPTIMIZATION')
text = replace_in_function(
    text,
    'test_decorating_invalid_persisted_cursor_migrates_to_snapshot_initial_step',
    "workflow_definition('FULL_OPTIMIZATION')",
    "workflow_definition('DAMPER_OPTIMIZATION')",
    count=1,
)
text = replace_in_function(
    text,
    'test_approved_execution_gate_accepts_all_engineering_workflows',
    "        'FULL_OPTIMIZATION': ('BASELINE', ['REQUIREMENTS', 'PREFLIGHT']),\n",
    '',
    count=1,
)
text = exact(
    text,
    "    monkeypatch.setattr(\n        service,\n        '_create_full_optimization_run',\n        lambda *_args, **_kwargs: (_ for _ in ()).throw(\n            AssertionError('显式 OpenSeesPy 请求不得进入 ANSYS FULL_OPTIMIZATION')\n        ),\n    )\n",
    "",
    'remove deleted full constructor monkeypatch',
)
write(path, text)

# LLM tests: validate the native engineering intent interface, not the retired FULL-only API.
path = 'momo_agent/backend/tests/test_agent_llm.py'
text = read(path)
text = replace_function(text, 'test_llm_planner_returns_valid_structured_intent', '''def test_llm_planner_returns_valid_structured_intent(monkeypatch) -> None:
    planner = OpenAICompatiblePlanner(base_url='http://127.0.0.1:11434/v1', model='momo-planner')
    monkeypatch.setattr(
        planner,
        '_request',
        lambda _payload, *, stage=None: {
            'choices': [{
                'message': {
                    'content': json.dumps({
                        'taskType': 'DAMPER_OPTIMIZATION',
                        'solver': 'ANSYS',
                        'damperType': 'VISCOUS',
                        'loadKind': 'EARTHQUAKE',
                        'selectedLayoutId': 'TWO_PER_TOWER',
                        'responseIds': ['cumulative_displacement'],
                        'budgetProfile': 'STANDARD',
                        'optimizationProfile': 'FULL',
                        'requiresRealFem': True,
                        'missingFields': [],
                        'summary': '运行完整优化',
                    }, ensure_ascii=False),
                }
            }]
        },
    )

    result = planner.plan_engineering(
        '执行完整阻尼优化', requested_task='DAMPER_OPTIMIZATION', has_file=False,
    )

    assert result.planner_mode == 'LLM'
    assert result.intent.task_type == 'DAMPER_OPTIMIZATION'
    assert result.intent.optimization_profile == 'FULL'
    assert result.intent.solver == 'ANSYS'
    assert result.intent.load_kind == 'EARTHQUAKE'
''')
text = replace_function(text, 'test_llm_planner_requires_configuration', '''def test_llm_planner_requires_configuration() -> None:
    with pytest.raises(LLMUnavailableError) as error:
        OpenAICompatiblePlanner(base_url='', model='').plan_engineering(
            '执行完整阻尼优化', requested_task='DAMPER_OPTIMIZATION', has_file=False,
        )
    assert error.value.stage == 'INTENT'
    assert error.value.reason == 'LLM_NOT_CONFIGURED'
''')
text = replace_function(text, 'test_llm_planner_raises_on_request_failure_or_invalid_json', '''def test_llm_planner_raises_on_request_failure_or_invalid_json(monkeypatch) -> None:
    planner = OpenAICompatiblePlanner(base_url='http://127.0.0.1:11434/v1', model='momo-planner')
    monkeypatch.setattr(
        planner,
        '_request',
        lambda _payload, *, stage=None: (_ for _ in ()).throw(
            LLMUnavailableError(stage or 'INTENT', 'LLM_CONNECTION_FAILED')
        ),
    )
    with pytest.raises(LLMUnavailableError) as error:
        planner.plan_engineering(
            '执行完整阻尼优化', requested_task='DAMPER_OPTIMIZATION', has_file=False,
        )
    assert error.value.reason == 'LLM_CONNECTION_FAILED'

    monkeypatch.setattr(
        planner,
        '_request',
        lambda _payload, *, stage=None: {'choices': [{'message': {'content': 'not-json'}}]},
    )
    with pytest.raises(LLMUnavailableError) as error:
        planner.plan_engineering(
            '执行完整阻尼优化', requested_task='DAMPER_OPTIMIZATION', has_file=False,
        )
    assert error.value.reason == 'LLM_INVALID_RESPONSE'
''')
text = replace_function(text, 'test_llm_planner_prompt_freezes_supported_intent_values', '''def test_llm_planner_prompt_freezes_supported_intent_values() -> None:
    planner = OpenAICompatiblePlanner(base_url='http://127.0.0.1:11434/v1', model='momo-planner')

    system_prompt = planner._engineering_payload(
        '执行完整阻尼优化', requested_task='DAMPER_OPTIMIZATION', has_file=False,
    )['messages'][0]['content']

    assert 'DAMPER_OPTIMIZATION' in system_prompt
    assert 'optimizationProfile' in system_prompt
    assert 'FULL' in system_prompt
    assert 'FULL_OPTIMIZATION' not in system_prompt
''')
text = text.replace('_register_full_optimization_report(', '_register_optimization_report(')
text = text.replace("'taskType': 'FULL_OPTIMIZATION'", "'taskType': 'DAMPER_OPTIMIZATION'")
write(path, text)

# Service registry test: the retired task must now fail closed.
path = 'momo_agent/backend/tests/test_engineering_base.py'
text = read(path)
text = replace_function(text, 'test_engineering_agent_is_abstract_and_service_registry_aliases_full_optimization', '''def test_engineering_agent_is_abstract_and_service_registry_rejects_retired_full_task() -> None:
    assert EngineeringAgent.__abstractmethods__
    assert not _DummyAgent.__abstractmethods__

    service = AgentService()
    optimization = service._agent_for('DAMPER_OPTIMIZATION')

    assert optimization is not None
    assert optimization.task_type == 'DAMPER_OPTIMIZATION'
    for retired in ('FULL_OPTIMIZATION', 'NOT_A_TASK'):
        with pytest.raises(Exception) as exc_info:
            service._agent_for(retired)
        assert getattr(exc_info.value, 'status_code', None) == 422
''')
write(path, text)

# Capability registry tests: FULL is no longer an agent-controlled capability.
path = 'momo_agent/backend/tests/test_real_execution_capabilities.py'
text = read(path)
text = exact(
    text,
    "    for job_type in ('ANALYSIS', 'DAMPER_COMPARISON', 'DAMPER_OPTIMIZATION', 'FULL_OPTIMIZATION', 'RESULT_INQUIRY'):\n        capability = real_execution_registry.resolve(job_type, {'source': 'AGENT'})\n        assert capability.mode == 'CONTROLLED_AGENT'\n        assert capability.status == 'LIVE'\n",
    "    for job_type in ('ANALYSIS', 'DAMPER_COMPARISON', 'DAMPER_OPTIMIZATION', 'RESULT_INQUIRY'):\n        capability = real_execution_registry.resolve(job_type, {'source': 'AGENT'})\n        assert capability.mode == 'CONTROLLED_AGENT'\n        assert capability.status == 'LIVE'\n\n    retired = real_execution_registry.resolve('FULL_OPTIMIZATION', {'source': 'AGENT'})\n    assert retired.mode == 'PLATFORM_API'\n    assert retired.status == 'DISABLED'\n    assert retired.handler == 'unregistered'\n",
    'controlled capability list',
)
write(path, text)

# Replace the old ANSYS-only FULL capability assertion with the canonical matrix + retired gate.
path = 'momo_agent/backend/tests/test_wind_analysis_agent.py'
text = read(path)
text = replace_function(text, 'test_full_optimization_capability_matches_its_ansys_earthquake_only_gate', '''def test_canonical_optimization_capability_replaces_retired_full_gate() -> None:
    capability = real_execution_registry.resolve('DAMPER_OPTIMIZATION', {'source': 'AGENT'})

    assert capability.status == 'LIVE'
    assert capability.supports(solver='ANSYS', scenario='EARTHQUAKE')
    assert capability.supports(solver='OPENSEESPY_INPROC', scenario='WIND')
    assert capability.supports(solver='ANSYS', scenario='TRAFFIC')

    retired = real_execution_registry.resolve('FULL_OPTIMIZATION', {'source': 'AGENT'})
    assert retired.status == 'DISABLED'
    assert retired.handler == 'unregistered'
''')
write(path, text)

# Finalizer is one-shot; remove itself and its workflow from the resulting commit.
for temporary in ('tools/pr3_ci_cleanup.py', '.github/workflows/pr3-ci-cleanup.yml'):
    candidate = Path(temporary)
    if candidate.exists():
        candidate.unlink()
