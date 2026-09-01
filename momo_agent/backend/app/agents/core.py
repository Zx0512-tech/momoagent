from __future__ import annotations

from collections.abc import Callable
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class AgentState(str, Enum):
    NEEDS_CLARIFICATION = 'NEEDS_CLARIFICATION'
    PLANNING = 'PLANNING'
    PREFLIGHT = 'PREFLIGHT'
    WAITING_MAPPING = 'WAITING_MAPPING'
    WAITING_APPROVAL = 'WAITING_APPROVAL'
    READY_FOR_SOLVER = 'READY_FOR_SOLVER'
    WAITING_JOB = 'WAITING_JOB'
    REVIEWING = 'REVIEWING'
    SUCCEEDED = 'SUCCEEDED'
    COMPLETED_DIAGNOSTIC = 'COMPLETED_DIAGNOSTIC'
    FAILED = 'FAILED'
    CANCELLED = 'CANCELLED'
    UNSUPPORTED = 'UNSUPPORTED'


class AgentStateMachine:
    """校验工程 Agent 生命周期中的关键状态转换。"""

    _allowed = {
        AgentState.NEEDS_CLARIFICATION: {
            AgentState.NEEDS_CLARIFICATION,
            AgentState.PLANNING,
            AgentState.WAITING_APPROVAL,
            AgentState.FAILED,
            AgentState.UNSUPPORTED,
        },
        AgentState.PLANNING: {
            AgentState.PLANNING,
            AgentState.PREFLIGHT,
            AgentState.WAITING_MAPPING,
            AgentState.WAITING_APPROVAL,
            AgentState.NEEDS_CLARIFICATION,
            AgentState.UNSUPPORTED,
            AgentState.FAILED,
        },
        AgentState.WAITING_MAPPING: {
            AgentState.WAITING_MAPPING,
            AgentState.WAITING_APPROVAL,
            AgentState.PREFLIGHT,
            AgentState.FAILED,
            AgentState.UNSUPPORTED,
        },
        AgentState.PREFLIGHT: {
            AgentState.PREFLIGHT,
            AgentState.WAITING_APPROVAL,
            AgentState.FAILED,
            AgentState.UNSUPPORTED,
        },
        AgentState.WAITING_APPROVAL: {
            AgentState.WAITING_APPROVAL,
            AgentState.READY_FOR_SOLVER,
            AgentState.WAITING_JOB,
            AgentState.WAITING_MAPPING,
            AgentState.UNSUPPORTED,
            AgentState.FAILED,
            AgentState.CANCELLED,
        },
        AgentState.READY_FOR_SOLVER: {
            AgentState.READY_FOR_SOLVER,
            AgentState.WAITING_APPROVAL,
            AgentState.WAITING_JOB,
            AgentState.CANCELLED,
        },
        AgentState.WAITING_JOB: {
            AgentState.WAITING_JOB,
            AgentState.REVIEWING,
            AgentState.SUCCEEDED,
            AgentState.COMPLETED_DIAGNOSTIC,
            AgentState.FAILED,
            AgentState.CANCELLED,
        },
        AgentState.REVIEWING: {
            AgentState.REVIEWING,
            AgentState.SUCCEEDED,
            AgentState.COMPLETED_DIAGNOSTIC,
            AgentState.FAILED,
            AgentState.CANCELLED,
        },
        AgentState.SUCCEEDED: {AgentState.SUCCEEDED},
        AgentState.COMPLETED_DIAGNOSTIC: {AgentState.COMPLETED_DIAGNOSTIC},
        AgentState.FAILED: {AgentState.FAILED},
        AgentState.CANCELLED: {AgentState.CANCELLED},
        AgentState.UNSUPPORTED: {AgentState.UNSUPPORTED},
    }

    def transition(self, current: AgentState, target: AgentState) -> AgentState:
        if target not in self._allowed.get(current, set()):
            raise ValueError(f'不允许的智能体状态转换: {current.value} -> {target.value}')
        return target


class AgentMessage(BaseModel):
    model_config = ConfigDict(extra='forbid')

    role: Literal['user', 'assistant', 'system', 'tool']
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentContext(BaseModel):
    model_config = ConfigDict(extra='forbid')

    session_id: str = Field(min_length=1)
    goal: str = Field(min_length=1, max_length=4000)
    requested_task: Literal[
        'ANALYSIS',
        'DAMPER_OPTIMIZATION',
        'DAMPER_COMPARISON',
        'DAMPER_PARAMETER_SWEEP',
    ] = 'ANALYSIS'
    has_attachment: bool = False
    attachment_summary: dict[str, Any] | None = None


class RepositorySessionMemory:
    """把现有 AgentRepository 消息转换为框架消息，不另建持久化。"""

    def __init__(
        self,
        message_loader: Callable[[str], list[dict[str, Any]]],
        *,
        max_messages: int = 20,
    ) -> None:
        if max_messages < 1:
            raise ValueError('max_messages 必须大于 0')
        self._message_loader = message_loader
        self.max_messages = max_messages

    def load(self, session_id: str) -> list[AgentMessage]:
        records = self._message_loader(session_id)[-self.max_messages:]
        return [
            AgentMessage(
                role=str(record['role']).lower(),
                content=str(record['content']),
                metadata={key: value for key, value in record.items() if key not in {'role', 'content'}},
            )
            for record in records
        ]
