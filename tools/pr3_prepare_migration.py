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
path.write_text(text, encoding='utf-8')
