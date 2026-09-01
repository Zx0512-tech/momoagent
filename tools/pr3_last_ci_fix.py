from __future__ import annotations

from pathlib import Path


def replace_exact(path: str, old: str, new: str, label: str, count: int = 1) -> None:
    file_path = Path(path)
    text = file_path.read_text(encoding='utf-8')
    actual = text.count(old)
    if actual != count:
        raise SystemExit(f'{label}: expected {count} matches, got {actual}')
    file_path.write_text(text.replace(old, new), encoding='utf-8')


# 1) Harness catalog: assert the canonical enum surface rather than a retired schema description.
replace_exact(
    'momo_agent/backend/tests/test_agent_harness.py',
    "    assert 'OpenSeesPy' in start_properties['taskType']['description']\n",
    "    assert 'DAMPER_OPTIMIZATION' in str(start_properties['taskType'])\n"
    "    assert 'FULL_OPTIMIZATION' not in str(start_properties['taskType'])\n",
    'harness canonical task schema assertion',
)

# 2) Solver-rewrite guard: feed a valid canonical FULL-profile optimization whose solver
# intentionally conflicts with the user's explicit OpenSeesPy request.  This keeps the test
# focused on the anti-rewrite safety gate instead of the retired FULL task schema.
replace_exact(
    'momo_agent/backend/tests/test_agent_harness.py',
    """            if len(calls) == 1:
                arguments = {
                    'taskType': 'FULL_OPTIMIZATION',
                    'fullOptimizationIntent': {
                        'taskType': 'FULL_OPTIMIZATION',
                        'solver': 'ANSYS',
                        'scenario': 'EARTHQUAKE',
                        'useVerifiedTemplateLoads': True,
                        'requiresRealFem': True,
                        'summary': '使用 ANSYS 执行完整优化。',
                    },
                }
""",
    """            if len(calls) == 1:
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
""",
    'harness solver rewrite canonical mock',
)
# Make the repaired second turn stay on the same FULL profile as well.
replace_exact(
    'momo_agent/backend/tests/test_agent_harness.py',
    """                        'responseIds': [
                            'max_girder_end_displacement',
                            'max_tower_base_shear',
                            'max_tower_base_moment',
                        ],
                        'missingFields': [],
                        'summary': '使用 OpenSeesPy 执行黏滞阻尼器 baseline-first 真实优化。',
""",
    """                        'responseIds': [
                            'max_girder_end_displacement',
                            'max_tower_base_shear',
                            'max_tower_base_moment',
                        ],
                        'optimizationProfile': 'FULL',
                        'missingFields': [],
                        'summary': '使用 OpenSeesPy 执行黏滞阻尼器 baseline-first 真实优化。',
""",
    'harness repaired full profile',
    count=1,
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

# 4) Reflection test: the production helper is now canonically named.
replace_exact(
    'momo_agent/backend/tests/test_agent_load_api.py',
    'agent_service._reflect_full_optimization(',
    'agent_service._reflect_optimization(',
    'canonical optimization reflection helper',
)

# One-shot machinery must not remain in the PR tree.
for temporary in ('tools/pr3_last_ci_fix.py', '.github/workflows/pr3-last-ci-fix.yml'):
    candidate = Path(temporary)
    if candidate.exists():
        candidate.unlink()
