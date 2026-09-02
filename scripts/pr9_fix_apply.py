from pathlib import Path

path = Path('scripts/pr9_apply.py')
text = path.read_text(encoding='utf-8')
old = '''harness_text = replace_regex_once(
    harness_text,
    r'\\ndef harness_capability_registry\\(\\) -> CapabilityRegistry:.*?\\ndef harness_step_tool_catalog\\(',
    '\\ndef harness_step_tool_catalog(',
    'remove duplicate registry/global tool union block',
)
# The previous regex intentionally removes the duplicate PR8 registry accessor plus union.
# Reinsert the single accessor/validator/cache block immediately before the step catalog.
insert_marker = '\\ndef harness_step_tool_catalog('
if capability_block.strip() not in harness_text:
    raise RuntimeError('capability block unexpectedly absent after replacement')

harness_text = harness_text.replace(
    "    unknown = sorted(set(allowed_tools) - set(_HARNESS_TOOL_SPECS))",
    "    unknown = sorted(set(allowed_tools) - set(_CAPABILITY_REGISTRY.list_ids()))",
)

# PR8 persisted a second accessor/cache block after the dispatcher. The replacement above may
# have consumed our newly generated accessor if the regex was too broad; rebuild the region safely.
if 'def harness_capability_registry()' not in harness_text:
    anchor = '_CAPABILITY_DISPATCHER = CapabilityDispatcher(_CAPABILITY_REGISTRY)\\n'
    tail = capability_block.split(anchor, 1)[1]
    harness_text = replace_once(
        harness_text,
        anchor,
        anchor + tail,
        'restore capability accessor and stage cache',
    )
'''
new = '''registry_markers = [
    match.start() for match in re.finditer(r'^def harness_capability_registry\\(\\)', harness_text, flags=re.M)
]
if len(registry_markers) != 2:
    raise RuntimeError(f'expected generated + legacy registry accessors, got {len(registry_markers)}')
legacy_registry_start = registry_markers[1]
step_catalog_start = harness_text.index('def harness_step_tool_catalog(', legacy_registry_start)
harness_text = harness_text[:legacy_registry_start] + harness_text[step_catalog_start:]

harness_text = harness_text.replace(
    "    unknown = sorted(set(allowed_tools) - set(_HARNESS_TOOL_SPECS))",
    "    unknown = sorted(set(allowed_tools) - set(_CAPABILITY_REGISTRY.list_ids()))",
)
'''
if text.count(old) != 1:
    raise RuntimeError(f'fix target count={text.count(old)}')
path.write_text(text.replace(old, new, 1), encoding='utf-8')
print('PR9 helper hardened')
