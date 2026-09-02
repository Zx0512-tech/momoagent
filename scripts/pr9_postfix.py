from pathlib import Path

HARNESS = Path('momo_agent/backend/app/services/agent_harness.py')
TEST = Path('momo_agent/backend/tests/test_agent_harness.py')


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f'{label}: expected 1 match, got {count}')
    return text.replace(old, new, 1)


text = HARNESS.read_text(encoding='utf-8')

# Preserve the pre-PR9 bootstrap failure semantics: WorkflowGuard owns stage violations;
# only an allowed model-selected tool proceeds into Capability discovery/type validation.
old_bootstrap = '''            call = turn.tool_calls[0]
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
new_bootstrap = '''            call = turn.tool_calls[0]
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
                capability = _CAPABILITY_REGISTRY.require(call.name)
                start = _CAPABILITY_DISPATCHER.authorize_and_validate(
                    call.name,
                    call.arguments,
                    allowed_capabilities=workflow_state['allowedTools'],
                    idempotency_key=f'{session["sessionId"]}:{call.tool_call_id}',
                )
'''
text = replace_once(text, old_bootstrap, new_bootstrap, 'bootstrap workflow semantics')

# For server-owned solver launch, deterministic engineering budget checks must retain their
# established error codes before the generic Capability input validation. Dispatcher still runs
# before WorkflowGuard and before any external execution/service call.
early_dispatch = '''        arguments = {'runId': run['runId']}
        arguments = _CAPABILITY_DISPATCHER.authorize_and_validate(
            tool_name,
            arguments,
            allowed_capabilities=[tool_name],
            approved=True,
            idempotency_key=idempotency_key,
        ).model_dump(by_alias=True, mode='json')
        executed_arguments = effective_arguments or arguments
'''
text = replace_once(
    text,
    early_dispatch,
    "        arguments = {'runId': run['runId']}\n        executed_arguments = effective_arguments or arguments\n",
    'defer approved solver capability validation',
)
text = replace_once(
    text,
    "            usage['doeDesignCount'] = requested_doe_count\n        guard.authorize(\n",
    "            usage['doeDesignCount'] = requested_doe_count\n        arguments = _CAPABILITY_DISPATCHER.authorize_and_validate(\n            tool_name,\n            arguments,\n            allowed_capabilities=[tool_name],\n            approved=True,\n            idempotency_key=idempotency_key,\n        ).model_dump(by_alias=True, mode='json')\n        guard.authorize(\n",
    'approved solver dispatcher after deterministic validation',
)

text = replace_once(
    text,
    "            'approved': spec.requires_approval,\n            'authorized': True,\n            'idempotencyKey': idempotency_key,\n            'idempotencyKeySource': spec.idempotency_key_source,\n",
    "            'approved': capability.requires_approval,\n            'authorized': True,\n            'idempotencyKey': idempotency_key,\n            'idempotencyKeySource': capability.idempotency_key_source,\n",
    'approved execution audit fields',
)
text = replace_once(
    text,
    "            'risk': spec.risk.value,\n            'approved': runtime_approval if capability.requires_approval else False,\n",
    "            'risk': capability.risk.value,\n            'approved': runtime_approval if capability.requires_approval else False,\n",
    'persistent loop audit risk',
)
HARNESS.write_text(text, encoding='utf-8')

test = TEST.read_text(encoding='utf-8')
test = replace_once(
    test,
    "        tools=harness_tool_catalog(),\n",
    "        tools=harness_step_tool_catalog(['result.peak']),\n",
    'native message payload stage tools',
)
old_cache = '''def test_tool_catalogs_are_cached_and_mutation_safe() -> None:
    first = harness_tool_catalog()
    second = harness_tool_catalog()
    assert first == second
    # 出口深拷贝：调用方原地改写不得污染缓存。
    first[0]['description'] = '污染'
    assert harness_tool_catalog() == second

    step_first = harness_step_tool_catalog(['workflow.observe', 'result.peak'])
    step_first[0]['inputSchema']['properties']['hacked'] = True
    step_second = harness_step_tool_catalog(['result.peak', 'workflow.observe'])
    assert all('hacked' not in item['inputSchema'].get('properties', {}) for item in step_second)
    assert [item['name'] for item in step_second] == ['result.peak', 'workflow.observe']
'''
new_cache = '''def test_stage_tool_catalog_is_cached_and_mutation_safe() -> None:
    first = harness_step_tool_catalog(['workflow.observe', 'result.peak'])
    second = harness_step_tool_catalog(['result.peak', 'workflow.observe'])
    assert first == second
    # 出口深拷贝：调用方原地改写不得污染阶段缓存。
    first[0]['inputSchema']['properties']['hacked'] = True
    again = harness_step_tool_catalog(['workflow.observe', 'result.peak'])
    assert all('hacked' not in item['inputSchema'].get('properties', {}) for item in again)
    assert [item['name'] for item in again] == ['result.peak', 'workflow.observe']
'''
test = replace_once(test, old_cache, new_cache, 'remove global union cache test')
test = replace_once(
    test,
    "        validated = _HARNESS_TOOL_SPECS[name].input_model.model_validate(trace['arguments'])\n",
    "        validated = harness_capability_registry().require(name).input_model.model_validate(\n            trace['arguments'],\n        )\n",
    'trace contract registry truth',
)
TEST.write_text(test, encoding='utf-8')

for path in (HARNESS, TEST):
    content = path.read_text(encoding='utf-8')
    for forbidden in ('_HARNESS_TOOL_SPECS', 'harness_tool_catalog', 'spec.requires_approval', 'spec.risk'):
        if forbidden in content:
            raise RuntimeError(f'{path}: legacy reference remains: {forbidden}')

print('PR9 generated references fixed')
