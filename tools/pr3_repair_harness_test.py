from __future__ import annotations

from pathlib import Path
import re
import subprocess

PATH = Path('momo_agent/backend/tests/test_agent_harness.py')
FUNCTION = 'test_harness_rejects_solver_rewrite_and_repairs_to_opensees_optimization'


def extract_function(text: str, name: str) -> str:
    match = re.search(rf'(?m)^def {re.escape(name)}\b', text)
    if not match:
        raise SystemExit(f'missing source function: {name}')
    next_match = re.search(r'(?m)^def ', text[match.end():])
    end = match.end() + next_match.start() if next_match else len(text)
    return text[match.start():end].rstrip()


current = PATH.read_text(encoding='utf-8')
if '\x01' not in current:
    raise SystemExit('expected damaged U+0001 marker was not found')
if current.count('\x01') != 1 or current.count('\x02') != 1:
    raise SystemExit(f'unexpected control marker counts: U+0001={current.count(chr(1))}, U+0002={current.count(chr(2))}')

master = subprocess.check_output(
    ['git', 'show', f'origin/master:{PATH.as_posix()}'],
    text=True,
    encoding='utf-8',
)
block = extract_function(master, FUNCTION)

# PR2/master still contains a guard against calling the deleted FULL constructor.  PR3 no
# longer has that constructor, so remove this obsolete monkeypatch if present.
block, removed = re.subn(
    r"\n    monkeypatch\.setattr\(\n        service,\n        '_create_full_optimization_run',[\s\S]*?\n    \)\n",
    '\n',
    block,
    count=1,
)
if removed not in {0, 1}:
    raise SystemExit(f'unexpected deleted-constructor monkeypatch count: {removed}')

# Replace only the deliberately-invalid first model turn.  It is now a *valid canonical*
# DAMPER_OPTIMIZATION request with FULL profile but an invalid solver rewrite to ANSYS.
branch_pattern = re.compile(
    r"        if len\(calls\) == 1:\n[\s\S]*?\n        else:",
)
canonical_first_turn = """        if len(calls) == 1:
            arguments = {
                'taskType': 'DAMPER_OPTIMIZATION',
                'engineeringIntent': {
                    'taskType': 'DAMPER_OPTIMIZATION',
                    'solver': 'ANSYS',
                    'damperType': 'VISCOUS',
                    'loadKind': 'EARTHQUAKE',
                    'selectedLayoutId': 'TWO_PER_TOWER',
                    'responseIds': [
                        'max_girder_end_displacement',
                        'max_tower_base_shear',
                        'max_tower_base_moment',
                    ],
                    'optimizationProfile': 'FULL',
                    'missingFields': [],
                    'summary': '使用 ANSYS 执行完整优化。',
                },
            }
        else:"""
block, replaced = branch_pattern.subn(canonical_first_turn, block, count=1)
if replaced != 1:
    raise SystemExit(f'expected one first-turn branch, got {replaced}')
if 'FULL_OPTIMIZATION' in block:
    raise SystemExit('restored test still references retired FULL_OPTIMIZATION task')

bad_start = current.index('\x01')
next_def = current.find('\ndef ', bad_start)
if next_def < 0:
    raise SystemExit('could not find function boundary after damaged region')

prefix = current[:bad_start].rstrip() + '\n\n'
suffix = current[next_def:].lstrip('\n')
updated = prefix + block + '\n\n' + suffix
if '\x01' in updated or '\x02' in updated:
    raise SystemExit('control characters remain after repair')
PATH.write_text(updated, encoding='utf-8')

# One-shot repair machinery must not remain in the final PR.
for temporary in ('tools/pr3_repair_harness_test.py', '.github/workflows/pr3-repair-harness-test.yml'):
    candidate = Path(temporary)
    if candidate.exists():
        candidate.unlink()
