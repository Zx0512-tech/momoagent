from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding='utf-8')
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'{path}: expected one match, found {count}: {old[:120]!r}')
    target.write_text(text.replace(old, new, 1), encoding='utf-8')


context_path = 'momo_agent/backend/app/services/agent_project_context.py'
replace_once(
    context_path,
    "        project_context: dict[str, Any] | None,\n        user_content: str,\n    ) -> tuple[Any, dict[str, str]]:\n",
    "        project_context: dict[str, Any] | None,\n        user_content: str,\n"
    "        prior_user_content: str = '',\n    ) -> tuple[Any, dict[str, str]]:\n",
)
replace_once(
    context_path,
    "        explicit_solver = _explicit_solver(user_content)\n",
    "        explicit_solver = _explicit_solver(user_content) or _explicit_solver(prior_user_content)\n",
)
replace_once(
    context_path,
    "        explicit_load = _explicit_load_kind(user_content)\n",
    "        explicit_load = _explicit_load_kind(user_content) or _explicit_load_kind(prior_user_content)\n",
)
replace_once(
    context_path,
    "        explicit_damper = _explicit_damper_type(user_content)\n",
    "        explicit_damper = _explicit_damper_type(user_content) or _explicit_damper_type(prior_user_content)\n",
)
replace_once(
    context_path,
    "        explicit_layout = _explicit_layout(user_content)\n",
    "        explicit_layout = _explicit_layout(user_content) or _explicit_layout(prior_user_content)\n",
)
replace_once(
    context_path,
    "        explicit_responses = _explicit_response_ids(user_content)\n",
    "        explicit_responses = _explicit_response_ids(user_content) or _explicit_response_ids(prior_user_content)\n",
)
replace_once(
    context_path,
    "        explicit_profile = _explicit_profile(user_content)\n",
    "        explicit_profile = _explicit_profile(user_content) or _explicit_profile(prior_user_content)\n",
)
replace_once(
    context_path,
    "        explicit_artifact = bool(re.search(r'art_[A-Za-z0-9_-]+', user_content))\n",
    "        explicit_artifact = bool(\n"
    "            re.search(r'art_[A-Za-z0-9_-]+', user_content)\n"
    "            or re.search(r'art_[A-Za-z0-9_-]+', prior_user_content)\n"
    "        )\n",
)
replace_once(
    context_path,
    "        trusted = [run for run in runs if self._trusted_run(run)]\n",
    "        trusted = [\n"
    "            run for run in runs\n"
    "            if self._trusted_run(run)\n"
    "            and str(run.get('ownerId') or owner) == owner\n"
    "        ]\n",
)

harness_path = 'momo_agent/backend/app/services/agent_harness.py'
replace_once(
    harness_path,
    "                intent_for_plan, memory_field_sources = engineering_project_context_service.resolve_intent(\n"
    "                    start.engineering_intent,\n                    project_context=project_context,\n"
    "                    user_content=content,\n                )\n",
    "                intent_for_plan, memory_field_sources = engineering_project_context_service.resolve_intent(\n"
    "                    start.engineering_intent,\n                    project_context=project_context,\n"
    "                    user_content=content,\n                    prior_user_content=str(run.get('goal') or ''),\n"
    "                )\n",
)

print('PR5 multi-turn precedence refinements applied')
