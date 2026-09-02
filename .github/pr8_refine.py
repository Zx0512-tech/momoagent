from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / 'momo_agent' / 'backend'
HARNESS = BACKEND / 'app' / 'services' / 'agent_harness.py'

text = HARNESS.read_text(encoding='utf-8')

old_doc = '''    """返回字节稳定的模型可调用工具目录。\n\n    所有调用点共用这一张表：各入口能接受的工具其实不同（入口只认\n    workflow.start，审批只认 approval.decide），但按调用点分表会产生多份\n    请求前缀并压低 KV cache 命中率，因此暴露固定并集，仍由 WorkflowGuard\n    按步骤逐次裁决。阶段授权不通过删工具实现。\n    """'''
new_doc = '''    """返回兼容用的全局模型可调用能力目录。\n\n    新的 Chat/Harness 主路径使用 ``harness_step_tool_catalog`` 按 Workflow 阶段\n    渐进披露；保留全局目录仅用于兼容旧调用点和目录一致性测试。实际授权仍由\n    WorkflowGuard 与 CapabilityDispatcher fail-closed 裁决。\n    """'''
if old_doc not in text:
    raise RuntimeError('harness_tool_catalog docstring anchor missing')
text = text.replace(old_doc, new_doc, 1)

active_start = text.index('    def _dispatch_harness_active_run_message(')
active_end = text.index('\n    def _dispatch_harness_approval_reply(', active_start)
active = text[active_start:active_end]
old_active_validation = '''            effective_arguments = HarnessNoInput.model_validate(call.arguments).model_dump(\n                by_alias=True,\n                mode='json',\n            )'''
new_active_validation = '''            effective_arguments = _CAPABILITY_DISPATCHER.authorize_and_validate(\n                'workflow.observe',\n                call.arguments,\n                allowed_capabilities=['workflow.observe'],\n            ).model_dump(by_alias=True, mode='json')'''
if old_active_validation not in active:
    raise RuntimeError('active capability validation anchor missing')
active = active.replace(old_active_validation, new_active_validation, 1)
old_active_snapshot = "                'content': self._harness_user_content(workflow_state, content),"
new_active_snapshot = "                'content': self._harness_user_content(workflow_state, content, tools=capability_tools),"
if old_active_snapshot not in active:
    raise RuntimeError('active runtime snapshot anchor missing')
active = active.replace(old_active_snapshot, new_active_snapshot, 1)
text = text[:active_start] + active + text[active_end:]

approval_start = text.index('    def _dispatch_harness_approval_reply(')
approval_end = text.index('\n    def _dispatch_harness_clarification_reply(', approval_start)
approval = text[approval_start:approval_end]
old_history = '''        history = self._history_for_harness_turn(\n            repository,\n            session['sessionId'],\n            self._workflow_state_from_run(run),\n            content,\n        )\n        capability_tools = harness_step_tool_catalog(['approval.decide'])'''
new_history = '''        workflow_state = self._workflow_state_from_run(run)\n        history = self._history_for_harness_turn(\n            repository,\n            session['sessionId'],\n            workflow_state,\n            content,\n        )\n        capability_tools = harness_step_tool_catalog(['approval.decide'])'''
if old_history not in approval:
    raise RuntimeError('approval workflow state anchor missing')
approval = approval.replace(old_history, new_history, 1)
approval = approval.replace('workflow_state=self._workflow_state_from_run(run),', 'workflow_state=workflow_state,', 1)
old_approval_validation = '''            approval_input = HarnessApprovalDecisionInput.model_validate(call.arguments)'''
new_approval_validation = '''            approval_input = _CAPABILITY_DISPATCHER.authorize_and_validate(\n                'approval.decide',\n                call.arguments,\n                allowed_capabilities=['approval.decide'],\n                approved=True,\n                idempotency_key=f'{run["runId"]}:approval:{call.tool_call_id}',\n            )'''
if old_approval_validation not in approval:
    raise RuntimeError('approval capability validation anchor missing')
approval = approval.replace(old_approval_validation, new_approval_validation, 1)
text = text[:approval_start] + approval + text[approval_end:]

HARNESS.write_text(text, encoding='utf-8')

runtime = BACKEND / 'app' / 'capabilities' / 'runtime.py'
runtime_text = runtime.read_text(encoding='utf-8')
runtime_text = runtime_text.replace("raise ValueError('求解或状态变更 Capability 必须要求审批')", "raise ValueError('求解 Capability 必须要求审批')", 1)
runtime.write_text(runtime_text, encoding='utf-8')

subprocess.run(['python', '-m', 'compileall', '-q', str(BACKEND / 'app')], cwd=BACKEND, check=True)
subprocess.run(['python', '-m', 'ruff', 'check', 'app', 'tests', '--select', 'E9,F63,F7,F82'], cwd=BACKEND, check=True)
subprocess.run(['python', '-m', 'pytest', '-q', 'tests/test_engineering_capability_runtime.py', 'tests/test_agent_llm.py', 'tests/test_agent_harness.py'], cwd=BACKEND, check=True)

for helper in (ROOT / '.github' / 'pr8_refine.py', ROOT / '.github' / 'workflows' / 'pr8-refine.yml'):
    if helper.exists():
        helper.unlink()

subprocess.run(['git', 'config', 'user.name', 'github-actions[bot]'], cwd=ROOT, check=True)
subprocess.run(['git', 'config', 'user.email', '41898282+github-actions[bot]@users.noreply.github.com'], cwd=ROOT, check=True)
subprocess.run(['git', 'add', '-A'], cwd=ROOT, check=True)
subprocess.run(['git', 'commit', '-m', 'fix: tighten capability runtime guards'], cwd=ROOT, check=True)
subprocess.run(['git', 'push', 'origin', 'HEAD:feat/engineering-capability-runtime'], cwd=ROOT, check=True)
