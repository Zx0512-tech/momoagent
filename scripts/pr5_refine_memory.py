from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding='utf-8')
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'{path}: expected one match, found {count}: {old[:120]!r}')
    target.write_text(text.replace(old, new, 1), encoding='utf-8')


path = 'momo_agent/backend/app/services/agent_project_context.py'

replace_once(
    path,
    "    if _contains_any(text, ('风荷载', '风工况', 'wind')):\n        matches.append('WIND')\n"
    "    if _contains_any(text, ('车流', '交通荷载', 'traffic')):\n        matches.append('TRAFFIC')\n",
    "    if _contains_any(text, ('风', 'wind')):\n        matches.append('WIND')\n"
    "    if _contains_any(text, ('车流', '交通', 'traffic')):\n        matches.append('TRAFFIC')\n",
)
replace_once(
    path,
    "    if _contains_any(text, ('摩擦阻尼', 'friction')):\n        matches.append('FRICTION')\n",
    "    if _contains_any(text, ('摩擦', 'friction')):\n        matches.append('FRICTION')\n",
)
replace_once(
    path,
    "    if any(token in lowered for token in ('full', '完整优化', '全流程优化')):\n        return 'FULL'\n",
    "    if any(token in lowered for token in ('full', '完整', '全流程')):\n        return 'FULL'\n",
)
replace_once(
    path,
    "        elif explicit_load is not None:\n            sources['loadKind'] = 'USER_SPECIFIED'\n",
    "        elif explicit_load is not None:\n            updates['load_kind'] = explicit_load\n            sources['loadKind'] = 'USER_SPECIFIED'\n",
)
replace_once(
    path,
    "        elif explicit_damper is not None:\n            sources['damperType'] = 'USER_SPECIFIED'\n",
    "        elif explicit_damper is not None:\n            updates['damper_type'] = explicit_damper\n            sources['damperType'] = 'USER_SPECIFIED'\n",
)
replace_once(
    path,
    "        elif explicit_layout is not None:\n            sources['selectedLayoutId'] = 'USER_SPECIFIED'\n",
    "        elif explicit_layout is not None:\n            updates['selected_layout_id'] = explicit_layout\n            sources['selectedLayoutId'] = 'USER_SPECIFIED'\n",
)
replace_once(
    path,
    "        elif explicit_profile is not None:\n            sources['optimizationProfile'] = 'USER_SPECIFIED'\n",
    "        elif explicit_profile is not None:\n            updates['optimization_profile'] = explicit_profile\n            sources['optimizationProfile'] = 'USER_SPECIFIED'\n",
)
replace_once(
    path,
    "        explicit_artifact = bool(re.search(r'\\bart_[A-Za-z0-9_-]+\\b', user_content))\n",
    "        explicit_artifact = bool(re.search(r'art_[A-Za-z0-9_-]+', user_content))\n",
)
replace_once(
    path,
    "        if inherited_slots and getattr(intent, 'missing_fields', None):\n"
    "            updates['missing_fields'] = [\n"
    "                slot for slot in intent.missing_fields if slot not in inherited_slots\n"
    "            ]\n"
    "        resolved = intent.model_copy(update=updates) if updates else intent\n",
    "        if inherited_slots and getattr(intent, 'missing_fields', None):\n"
    "            updates['missing_fields'] = [\n"
    "                slot for slot in intent.missing_fields if slot not in inherited_slots\n"
    "            ]\n"
    "        # build_engineering_contract treats an explicitly supplied fieldSources mapping as\n"
    "        # authoritative, so memory resolution must return a complete provenance baseline.\n"
    "        sources.setdefault('solver', 'DEFAULT')\n"
    "        sources.setdefault('loadKind', 'DEFAULT')\n"
    "        sources.setdefault('responseIds', 'DEFAULT')\n"
    "        sources.setdefault('budget', 'DEFAULT')\n"
    "        if getattr(intent, 'selected_layout_id', None) or workspace.get('selectedLayoutId'):\n"
    "            sources.setdefault('selectedLayoutId', 'DEFAULT')\n"
    "        if getattr(intent, 'task_type', None) == 'DAMPER_OPTIMIZATION':\n"
    "            sources.setdefault('optimizationProfile', 'DEFAULT')\n"
    "        resolved = intent.model_copy(update=updates) if updates else intent\n",
)

replace_once(
    path,
    "        return {\n"
    "            'runId': run_id,\n"
    "            'sessionId': run.get('sessionId'),\n",
    "        raw_summary = run.get('resultSummary') if isinstance(run.get('resultSummary'), dict) else {}\n"
    "        compact_summary = {\n"
    "            key: raw_summary.get(key)\n"
    "            for key in (\n"
    "                'evidenceMode', 'objectives', 'baselineObjectives', 'recommendedObjectives',\n"
    "                'responseComparison', 'validationStatus', 'reviewStatus',\n"
    "                'finalRecommendationStatus',\n"
    "            )\n"
    "            if raw_summary.get(key) is not None\n"
    "        }\n"
    "        if raw_summary.get('message'):\n"
    "            compact_summary['message'] = str(raw_summary['message'])[:500]\n"
    "        return {\n"
    "            'runId': run_id,\n"
    "            'sessionId': run.get('sessionId'),\n",
)
replace_once(
    path,
    "            'resultSummary': run.get('resultSummary'),\n",
    "            'resultSummary': compact_summary,\n",
)

print('PR5 memory resolver refinements applied')
