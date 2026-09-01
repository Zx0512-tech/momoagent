from pathlib import Path


def replace_exact(path: str, old: str, new: str, label: str, count: int = 1) -> None:
    file_path = Path(path)
    text = file_path.read_text(encoding='utf-8')
    actual = text.count(old)
    if actual != count:
        raise SystemExit(f'{label}: expected {count} matches, got {actual}')
    file_path.write_text(text.replace(old, new), encoding='utf-8')


replace_exact(
    'momo_agent/backend/app/services/agent_harness.py',
    "        target = 'BASELINE' if normalized_task in {'DAMPER_OPTIMIZATION', 'FULL_OPTIMIZATION'}  # historical snapshot only else 'EXECUTION'",
    "        target = 'BASELINE' if normalized_task in {'DAMPER_OPTIMIZATION', 'FULL_OPTIMIZATION'} else 'EXECUTION'  # legacy FULL snapshots only",
    'harness waiting-job cursor',
)
replace_exact(
    'momo_agent/backend/app/services/agent_harness.py',
    "        target = 'REVIEW' if normalized_task in {'DAMPER_OPTIMIZATION', 'FULL_OPTIMIZATION'}  # historical snapshot only else 'EVIDENCE_REVIEW'",
    "        target = 'REVIEW' if normalized_task in {'DAMPER_OPTIMIZATION', 'FULL_OPTIMIZATION'} else 'EVIDENCE_REVIEW'  # legacy FULL snapshots only",
    'harness reviewing cursor',
)
replace_exact(
    'momo_agent/backend/app/services/agent_service.py',
    "                'RUN_ENGINEERING_WORKFLOW': ('FULL_OPTIMIZATION', '创建工程优化任务'),",
    "                'RUN_ENGINEERING_WORKFLOW': ('DAMPER_OPTIMIZATION', '创建工程优化任务'),",
    'canonical optimization idempotency prefix',
)
replace_exact(
    'platform-ui/src/pages/agent/FullOptimizationPanel.tsx',
    '          <Info label="求解器" value="ANSYS / MAPDL" />',
    '          <Info label="求解器" value="ANSYS / OpenSeesPy" />',
    'workbench solver capability',
)
replace_exact(
    'platform-ui/src/pages/agent/FullOptimizationPanel.tsx',
    '          <Info label="完整优化" value="40 s 地震 + 3600 s 运营" />',
    '          <Info label="优化 Profile" value="STANDARD / FULL / CUSTOM" />',
    'workbench profile capability',
)
replace_exact(
    'platform-ui/src/pages/agent/FullOptimizationPanel.tsx',
    '      {run?.pendingApproval && ["RUN_FULL_OPTIMIZATION", "RUN_DAMPER_COMPARISON"].includes(run.pendingApproval.action) && (',
    '      {run?.pendingApproval && ["RUN_ENGINEERING_WORKFLOW", "RUN_DAMPER_COMPARISON"].includes(run.pendingApproval.action) && (',
    'workbench canonical approval action',
)
replace_exact(
    'platform-ui/src/api/client.ts',
    '["ANALYSIS", "DAMPER_COMPARISON", "DAMPER_PARAMETER_SWEEP", "DAMPER_OPTIMIZATION", "FULL_OPTIMIZATION", "RESULT_INQUIRY"',
    '["ANALYSIS", "DAMPER_COMPARISON", "DAMPER_PARAMETER_SWEEP", "DAMPER_OPTIMIZATION", "RESULT_INQUIRY"',
    'mock capability catalog',
)

# The final tree must not retain this one-shot migration machinery.
for temporary in (
    'tools/pr3_finalize.py',
    '.github/workflows/pr3-finalize.yml',
):
    file_path = Path(temporary)
    if file_path.exists():
        file_path.unlink()
