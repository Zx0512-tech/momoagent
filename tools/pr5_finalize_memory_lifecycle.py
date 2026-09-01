from pathlib import Path

SERVICE = Path('momo_agent/backend/app/services/agent_service.py')
text = SERVICE.read_text(encoding='utf-8')

old_decorate_side_effect = """        try:\n            engineering_project_context_service.write_back_verified_run(run)\n        except Exception:\n            logger.warning('Project Workspace 记忆写回失败，不影响运行读取', exc_info=True)\n"""
if text.count(old_decorate_side_effect) != 1:
    raise SystemExit(f'decorate side effect count != 1: {text.count(old_decorate_side_effect)}')
text = text.replace(old_decorate_side_effect, '', 1)

anchor = """    def _decorate_run(self, run: dict[str, Any]) -> dict[str, Any]:\n"""
if text.count(anchor) != 1:
    raise SystemExit(f'decorate anchor count != 1: {text.count(anchor)}')
helper = """    def _materialize_project_memory_once(\n        self,\n        repository: AgentRepository,\n        run: dict[str, Any],\n    ) -> None:\n        \"\"\"成功工程 Run 在终态提交时一次性物化 Project Workspace 记忆。\n\n        这是写路径钩子，不允许从 _decorate_run/get_run/get_session 等读路径触发。\n        Project 更新与 Run 标记分两次 SQLite 提交；若两者之间进程退出，\n        write_back_verified_run 的值级幂等保证重试不会重复推进 workspaceRevision。\n        \"\"\"\n        if run.get('projectMemoryMaterialization'):\n            return\n        summary = run.get('resultSummary') if isinstance(run.get('resultSummary'), dict) else {}\n        if (\n            run.get('taskType') not in ENGINEERING_TASK_TYPES\n            or run.get('status') != 'SUCCEEDED'\n            or summary.get('evidenceMode') != 'REAL_FEM'\n            or not run.get('reportArtifactId')\n        ):\n            return\n        try:\n            project = engineering_project_context_service.write_back_verified_run(run)\n        except Exception:\n            logger.warning(\n                'Project Workspace 终态记忆写回失败，不影响已完成工程 Run',\n                exc_info=True,\n            )\n            return\n        if project is None:\n            return\n        run['projectMemoryMaterialization'] = {\n            'projectId': project.get('projectId'),\n            'workspaceRevision': int(project.get('workspaceRevision') or 0),\n            'materializedAt': utc_now(),\n        }\n        repository.save_run(run)\n\n"""
text = text.replace(anchor, helper + anchor, 1)

recover_start = text.index('    def _recover_persisted_agent_report(')
refresh_start = text.index('    def _refresh_agent_run_once(', recover_start)
recover = text[recover_start:refresh_start]
old_tail = """        repository.save_run(run)\n        return run\n\n"""
if recover.count(old_tail) != 1:
    raise SystemExit(f'recovery final save count != 1: {recover.count(old_tail)}')
recover = recover.replace(
    old_tail,
    """        repository.save_run(run)\n        self._materialize_project_memory_once(repository, run)\n        return run\n\n""",
    1,
)
text = text[:recover_start] + recover + text[refresh_start:]

refresh_start = text.index('    def _refresh_agent_run_once(')
display_start = text.index('    @staticmethod\n    def _display_result_values', refresh_start)
refresh = text[refresh_start:display_start]
if refresh.count(old_tail) != 1:
    raise SystemExit(f'refresh final save count != 1: {refresh.count(old_tail)}')
refresh = refresh.replace(
    old_tail,
    """        repository.save_run(run)\n        self._materialize_project_memory_once(repository, run)\n        return run\n\n""",
    1,
)
text = text[:refresh_start] + refresh + text[display_start:]

if "engineering_project_context_service.write_back_verified_run(run)" not in text:
    raise SystemExit('finalization writeback hook missing')
if "Project Workspace 记忆写回失败，不影响运行读取" in text:
    raise SystemExit('read-time writeback warning still present')

SERVICE.write_text(text, encoding='utf-8')
