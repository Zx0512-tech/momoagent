from __future__ import annotations

from pathlib import Path
import re


def replace_exact(path: str, old: str, new: str, label: str, count: int = 1) -> None:
    file_path = Path(path)
    text = file_path.read_text(encoding='utf-8')
    actual = text.count(old)
    if actual != count:
        raise SystemExit(f'{label}: expected {count} matches, got {actual}')
    file_path.write_text(text.replace(old, new), encoding='utf-8')


def replace_regex(path: str, pattern: str, new: str, label: str, count: int = 1) -> None:
    file_path = Path(path)
    text = file_path.read_text(encoding='utf-8')
    updated, actual = re.subn(pattern, new, text, count=count, flags=re.S)
    if actual != count:
        raise SystemExit(f'{label}: expected {count} matches, got {actual}')
    file_path.write_text(updated, encoding='utf-8')


# 1) Harness catalog: assert the canonical enum surface rather than a retired schema description.
replace_exact(
    'momo_agent/backend/tests/test_agent_harness.py',
    "    assert 'OpenSeesPy' in start_properties['taskType']['description']\n"
    "    assert 'DAMPER_OPTIMIZATION' in start_properties['taskType']['description']\n",
    "    assert 'DAMPER_OPTIMIZATION' in str(start_properties['taskType'])\n"
    "    assert 'FULL_OPTIMIZATION' not in str(start_properties['taskType'])\n",
    'harness canonical task schema assertion',
)

# 2) Solver-rewrite guard: replace only the first-turn branch inside the targeted test.
# The payload stays canonical DAMPER_OPTIMIZATION+FULL while intentionally trying ANSYS,
# so the anti-rewrite gate—not retired-schema validation—must reject it.
replace_regex(
    'momo_agent/backend/tests/test_agent_harness.py',
    r"(def test_harness_rejects_solver_rewrite_and_repairs_to_opensees_optimization\(monkeypatch\)[\s\S]*?"
    r"        if len\(calls\) == 1:\n)[\s\S]*?(\n        else:)",
    """\1                arguments = {
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
                }\2""",
    'harness solver rewrite canonical mock',
)

# 3) LLM prompt test: native engineering payload requires explicit attachment summary.
replace_exact(
    'momo_agent/backend/tests/test_agent_llm.py',
    "        '执行完整阻尼优化', requested_task='DAMPER_OPTIMIZATION', has_file=False,\n    )['messages'][0]['content']\n",
    "        '执行完整阻尼优化', requested_task='DAMPER_OPTIMIZATION', has_file=False,\n"
    "        attachment_summary=None,\n"
    "    )['messages'][0]['content']\n",
    'llm engineering payload attachment summary',
)

# 4) Reflection tests: both old helper call sites now use the canonical helper name.
replace_exact(
    'momo_agent/backend/tests/test_agent_load_api.py',
    'agent_service._reflect_full_optimization(',
    'agent_service._reflect_optimization(',
    'canonical optimization reflection helper',
    count=2,
)

# One-shot machinery must not remain in the PR tree.
for temporary in ('tools/pr3_last_ci_fix.py', '.github/workflows/pr3-last-ci-fix.yml'):
    candidate = Path(temporary)
    if candidate.exists():
        candidate.unlink()
