from __future__ import annotations

from typing import Any


_VOLATILE_RUNTIME_KEYS = frozenset({
    'allowedTools',
    'availableCapabilities',
    'engineeringProjectContext',
    'resultInquiryContext',
})


def build_compression_state_anchor(
    *,
    workflow_state: dict[str, Any] | None,
    run: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """压缩 Epoch 的结构化锚点：只保留不能靠语义摘要安全恢复的引用与冻结状态。"""
    state = workflow_state or {}
    anchor: dict[str, Any] = {
        'version': 1,
        'runId': state.get('runId') or (run or {}).get('runId'),
        'taskType': state.get('taskType') or (run or {}).get('taskType'),
        'currentStep': state.get('currentStep') or (run or {}).get('currentStep'),
        'completedSteps': list(state.get('completedSteps') or (run or {}).get('completedSteps') or []),
        'requiredGate': state.get('requiredGate'),
    }
    if run:
        anchor.update({
            'pendingApprovalId': run.get('pendingApprovalId'),
            'approvalStatus': run.get('approvalStatus'),
            'reportArtifactId': run.get('reportArtifactId'),
            'artifactIds': list(run.get('artifactIds') or []),
            'contractHash': (run.get('engineeringContract') or {}).get('contractHash')
                if isinstance(run.get('engineeringContract'), dict) else None,
            'missingFields': list((run.get('intent') or {}).get('missingFields') or [])
                if isinstance(run.get('intent'), dict) else [],
        })
    return {key: value for key, value in anchor.items() if value not in (None, '', [], {})}


def volatile_runtime_keys() -> frozenset[str]:
    return _VOLATILE_RUNTIME_KEYS
