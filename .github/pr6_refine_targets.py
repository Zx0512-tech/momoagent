from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding='utf-8')
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'{path}: expected one match, got {count}: {old[:120]!r}')
    target.write_text(text.replace(old, new, 1), encoding='utf-8')


replace_once(
    'momo_agent/backend/app/services/agent_run_comparison.py',
    "                comparisons.append({\n                    'baselineRunId': baseline['runId'],\n                    'runId': item['runId'],",
    "                comparisons.append({\n                    'baselineTargetKey': baseline['targetKey'],\n                    'targetKey': item['targetKey'],\n                    'baselineRunId': baseline['runId'],\n                    'runId': item['runId'],\n                    **({'caseId': item['caseId']} if item.get('caseId') else {}),\n                    **({'candidateRank': item['candidateRank']} if item.get('candidateRank') else {}),",
)
replace_once(
    'momo_agent/backend/app/services/agent_run_comparison.py',
    "        return {\n            'runId': str(run.get('runId') or ''),\n            'taskType': task_type,",
    "        run_id = str(run.get('runId') or '')\n        target_key = run_id\n        if selector_meta.get('caseId'):\n            target_key = f'{run_id}#case:{selector_meta[\"caseId\"]}'\n        elif selector_meta.get('candidateRank'):\n            target_key = f'{run_id}#rank:{selector_meta[\"candidateRank\"]}'\n        return {\n            'targetKey': target_key,\n            'runId': run_id,\n            'taskType': task_type,",
)
replace_once(
    'momo_agent/backend/app/services/agent_run_comparison.py',
    "                    {'rank': rank, 'runId': item['runId'], 'value': item['metrics'][metric_id]['value']}\n                    for rank, item in enumerate(ordered, start=1)",
    "                    {\n                        'rank': rank,\n                        'targetKey': item['targetKey'],\n                        'runId': item['runId'],\n                        **({'caseId': item['caseId']} if item.get('caseId') else {}),\n                        **({'candidateRank': item['candidateRank']} if item.get('candidateRank') else {}),\n                        'value': item['metrics'][metric_id]['value'],\n                    }\n                    for rank, item in enumerate(ordered, start=1)",
)

replace_once(
    'platform-ui/src/api/agentApi.ts',
    "export interface RunComparisonRun {\n  runId: string;",
    "export interface RunComparisonRun {\n  targetKey: string;\n  runId: string;",
)
replace_once(
    'platform-ui/src/api/agentApi.ts',
    "export interface RunComparisonPair {\n  baselineRunId: string;\n  runId: string;",
    "export interface RunComparisonPair {\n  baselineTargetKey: string;\n  targetKey: string;\n  baselineRunId: string;\n  runId: string;\n  caseId?: string;\n  candidateRank?: number;",
)
replace_once(
    'platform-ui/src/api/agentApi.ts',
    "    rows: Array<{ rank: number; runId: string; value: number }>;",
    "    rows: Array<{ rank: number; targetKey: string; runId: string; caseId?: string; candidateRank?: number; value: number }>;",
)

replace_once(
    'platform-ui/src/pages/agent/RunComparisonCard.tsx',
    "        `${item.runId}:${metricId}`,",
    "        `${item.targetKey}:${metricId}`,",
)
replace_once(
    'platform-ui/src/pages/agent/RunComparisonCard.tsx',
    "          {comparison.runs.map(item => <tr key={`${item.runId}:${item.caseId ?? \"\"}:${item.candidateRank ?? \"\"}`}>",
    "          {comparison.runs.map(item => <tr key={item.targetKey}>",
)
replace_once(
    'platform-ui/src/pages/agent/RunComparisonCard.tsx',
    "              const delta = deltas.get(`${item.runId}:${metricId}`);",
    "              const delta = deltas.get(`${item.targetKey}:${metricId}`);",
)

# Unit fixtures now carry the same target identity as production snapshots.
replace_once(
    'momo_agent/backend/tests/test_agent_run_comparison.py',
    "    return {\n        'runId': run['runId'],",
    "    return {\n        'targetKey': run['runId'],\n        'runId': run['runId'],",
)
replace_once(
    'platform-ui/src/pages/agent/RunComparisonCard.test.tsx',
    "          runId: \"agr_base\",",
    "          targetKey: \"agr_base\",\n          runId: \"agr_base\",",
)
replace_once(
    'platform-ui/src/pages/agent/RunComparisonCard.test.tsx',
    "          runId: \"agr_candidate\",",
    "          targetKey: \"agr_candidate\",\n          runId: \"agr_candidate\",",
)
replace_once(
    'platform-ui/src/pages/agent/RunComparisonCard.test.tsx',
    "      comparisons: [{\n        baselineRunId: \"agr_base\",\n        runId: \"agr_candidate\",",
    "      comparisons: [{\n        baselineTargetKey: \"agr_base\",\n        targetKey: \"agr_candidate\",\n        baselineRunId: \"agr_base\",\n        runId: \"agr_candidate\",",
)

print('PR6 target identity refinement applied')
