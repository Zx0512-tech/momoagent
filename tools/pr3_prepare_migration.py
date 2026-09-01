from pathlib import Path

path = Path('tools/pr3_native_migration.py')
text = path.read_text(encoding='utf-8')

old = """if 'FULL_OPTIMIZATION' in text:
    raise SystemExit('agent_service still contains FULL_OPTIMIZATION after migration')
write(path, text)
"""
new = """text = exact(
    text,
    \"\"\"        optimization_defaults = (\n            FULL_OPTIMIZATION_CONTRACT\n            if task_type in {'FULL_OPTIMIZATION', 'DAMPER_OPTIMIZATION'}\n            else {}\n        )\n\"\"\",
    \"        optimization_defaults = {}  # canonical contracts freeze all optimization defaults\n\",
    'agent_service retired full defaults',
)
text = text.replace(
    \"orchestration_handler_or_generic('FULL_OPTIMIZATION').reflect(\n            self._agent_for('FULL_OPTIMIZATION'),\",
    \"orchestration_handler_or_generic('DAMPER_OPTIMIZATION').reflect(\n            self._agent_for('DAMPER_OPTIMIZATION'),\",
)
text = text.replace(
    \"self._register_agent_report(run, job, outcome, self._agent_for('FULL_OPTIMIZATION'))\",
    \"self._register_agent_report(run, job, outcome, self._agent_for('DAMPER_OPTIMIZATION'))\",
)
write(path, text)
"""
if text.count(old) != 1:
    raise SystemExit('prepare: agent_service assertion block not found exactly once')
text = text.replace(old, new)
path.write_text(text, encoding='utf-8')
