from __future__ import annotations

from typing import Any


def capability_context_from_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for tool in sorted(tools, key=lambda item: str(item.get('name') or '')):
        metadata = tool.get('capability')
        if isinstance(metadata, dict):
            result.append(dict(metadata))
            continue
        result.append({
            'capabilityId': str(tool.get('name') or ''),
            'version': 'legacy',
            'sideEffect': 'UNKNOWN',
            'approvalPolicy': 'UNKNOWN',
            'prerequisites': [],
            'evidencePolicy': 'UNKNOWN',
        })
    return result


def build_runtime_turn_payload(
    *,
    workflow_state: dict[str, Any],
    user_content: str,
    tools: list[dict[str, Any]],
    turn_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构造可持久化的 Turn Snapshot；旧 snapshot 只作历史记录，最新块才有权威性。"""
    runtime_context: dict[str, Any] = {
        'version': 1,
        'scope': 'TURN_SNAPSHOT',
        'workflowState': workflow_state,
        'availableCapabilities': capability_context_from_tools(tools),
    }
    if turn_context:
        runtime_context.update(turn_context)
    return {
        'runtimeContext': runtime_context,
        'userContent': str(user_content or '')[:4000],
    }


def runtime_context_metrics(payload: dict[str, Any], tools: list[dict[str, Any]]) -> dict[str, int]:
    runtime = payload.get('runtimeContext') if isinstance(payload, dict) else None
    return {
        'capabilityCountExposed': len(tools),
        'runtimeContextKeyCount': len(runtime) if isinstance(runtime, dict) else 0,
        'toolSchemaPropertyCount': sum(
            len(((tool.get('inputSchema') or {}).get('properties') or {}))
            for tool in tools
            if isinstance(tool, dict)
        ),
    }
