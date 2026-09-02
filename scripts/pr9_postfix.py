from pathlib import Path

HARNESS = Path('momo_agent/backend/app/services/agent_harness.py')
TEST = Path('momo_agent/backend/tests/test_agent_harness.py')


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f'{label}: expected 1 match, got {count}')
    return text.replace(old, new, 1)


text = HARNESS.read_text(encoding='utf-8')
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
