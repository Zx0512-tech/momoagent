from pathlib import Path

path = Path('tools/pr3_native_migration.py')
text = path.read_text(encoding='utf-8')

old = """if 'FULL_OPTIMIZATION' in text:
    raise SystemExit('agent_service still contains FULL_OPTIMIZATION after migration')
write(path, text)
"""

optimization_block = """        optimization_defaults = (
            FULL_OPTIMIZATION_CONTRACT
            if task_type in {'FULL_OPTIMIZATION', 'DAMPER_OPTIMIZATION'}
            else {}
        )
"""
canonical_default_line = (
    '        optimization_defaults = {}  # canonical contracts freeze all optimization defaults\n'
)
old_reflect = (
    "orchestration_handler_or_generic('FULL_OPTIMIZATION').reflect(\n"
    "            self._agent_for('FULL_OPTIMIZATION'),"
)
new_reflect = (
    "orchestration_handler_or_generic('DAMPER_OPTIMIZATION').reflect(\n"
    "            self._agent_for('DAMPER_OPTIMIZATION'),"
)
old_report = "self._register_agent_report(run, job, outcome, self._agent_for('FULL_OPTIMIZATION'))"
new_report = "self._register_agent_report(run, job, outcome, self._agent_for('DAMPER_OPTIMIZATION'))"

new = (
    "text = exact(\n"
    "    text,\n"
    f"    {optimization_block!r},\n"
    f"    {canonical_default_line!r},\n"
    "    'agent_service retired full defaults',\n"
    ")\n"
    f"text = text.replace({old_reflect!r}, {new_reflect!r})\n"
    f"text = text.replace({old_report!r}, {new_report!r})\n"
    "write(path, text)\n"
)

if text.count(old) != 1:
    raise SystemExit('prepare: agent_service assertion block not found exactly once')
text = text.replace(old, new)

# The original migration removed AgentIntent through EngineeringPlannerResult in one regex,
# which also swallowed _strip_think and _parse_engineering_intent. Retire only the two legacy
# full-planner models and keep the native engineering parser intact.
broad_model_removal = """text = regex(
    text,
    r"\\nclass AgentIntent\\(BaseModel\\):[\\s\\S]*?(?=\\nclass EngineeringPlannerResult\\(BaseModel\\):)",
    "\\n",
    'llm retired AgentIntent models',
)
"""
narrow_model_removal = """text = regex(
    text,
    r"\\nclass AgentIntent\\(BaseModel\\):[\\s\\S]*?(?=\\nclass PlannerResult\\(BaseModel\\):)",
    "\\n",
    'llm retired AgentIntent model',
)
text = regex(
    text,
    r"\\nclass PlannerResult\\(BaseModel\\):[\\s\\S]*?(?=\\n_RE_THINK =)",
    "\\n",
    'llm retired PlannerResult model',
)
"""
if text.count(broad_model_removal) != 1:
    raise SystemExit('prepare: broad AgentIntent removal block not found exactly once')
text = text.replace(broad_model_removal, narrow_model_removal)

# Ensure all migration-only files disappear from the bot commit.
cleanup_old = """    '.github/workflows/pr3-native-migration-run.yml',
    'tools/pr3_native_migration.py',
"""
cleanup_new = """    '.github/workflows/pr3-native-migration-run.yml',
    '.github/workflows/pr3-native-migration-run-v2.yml',
    'tools/pr3_native_migration.py',
    'tools/pr3_prepare_migration.py',
"""
if text.count(cleanup_old) != 1:
    raise SystemExit('prepare: migration cleanup block not found exactly once')
text = text.replace(cleanup_old, cleanup_new)

path.write_text(text, encoding='utf-8')
