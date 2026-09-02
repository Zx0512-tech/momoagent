from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from pydantic import BaseModel, ValidationError

from app.agents.tools import ToolExecutionError, ToolRisk


_CAPABILITY_ID = re.compile(r'^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$')


class CapabilitySideEffect(str, Enum):
    NONE = 'NONE'
    ARTIFACT_WRITE = 'ARTIFACT_WRITE'
    EXTERNAL_COMPUTE = 'EXTERNAL_COMPUTE'
    STATE_MUTATION = 'STATE_MUTATION'


class ApprovalPolicy(str, Enum):
    NONE = 'NONE'
    REQUIRED = 'REQUIRED'


class EvidencePolicy(str, Enum):
    NONE = 'NONE'
    REGISTERED_ARTIFACT_ONLY = 'REGISTERED_ARTIFACT_ONLY'
    REAL_FEM_REQUIRED = 'REAL_FEM_REQUIRED'


@dataclass(frozen=True)
class EngineeringCapability:
    capability_id: str
    description: str
    input_model: type[BaseModel]
    version: str = '1.0.0'
    risk: ToolRisk = ToolRisk.READ_ONLY
    requires_approval: bool = False
    prerequisites: tuple[str, ...] = ()
    evidence_policy: EvidencePolicy = EvidencePolicy.NONE
    idempotency_key_source: str | None = None
    artifact_kinds: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not _CAPABILITY_ID.fullmatch(self.capability_id):
            raise ValueError(f'非法 capabilityId: {self.capability_id}')
        if not self.version.strip():
            raise ValueError('Capability version 不能为空')
        if self.risk is ToolRisk.SOLVER_EXECUTION and not self.requires_approval:
            raise ValueError('求解 Capability 必须要求审批')

    @property
    def side_effect(self) -> CapabilitySideEffect:
        return {
            ToolRisk.READ_ONLY: CapabilitySideEffect.NONE,
            ToolRisk.ARTIFACT_WRITE: CapabilitySideEffect.ARTIFACT_WRITE,
            ToolRisk.SOLVER_EXECUTION: CapabilitySideEffect.EXTERNAL_COMPUTE,
            ToolRisk.MUTATING: CapabilitySideEffect.STATE_MUTATION,
        }[self.risk]

    @property
    def approval_policy(self) -> ApprovalPolicy:
        return ApprovalPolicy.REQUIRED if self.requires_approval else ApprovalPolicy.NONE

    def tool_schema(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            'name': self.capability_id,
            'description': self.description,
            'inputSchema': self.input_model.model_json_schema(by_alias=True),
            'capability': self.runtime_descriptor(),
        }
        if self.idempotency_key_source:
            result['idempotencyKeySource'] = self.idempotency_key_source
        return result

    def runtime_descriptor(self) -> dict[str, Any]:
        return {
            'capabilityId': self.capability_id,
            'version': self.version,
            'sideEffect': self.side_effect.value,
            'approvalPolicy': self.approval_policy.value,
            'prerequisites': list(self.prerequisites),
            'evidencePolicy': self.evidence_policy.value,
        }


class CapabilityRegistry:
    def __init__(self) -> None:
        self._capabilities: dict[str, EngineeringCapability] = {}

    def register(self, capability: EngineeringCapability) -> None:
        if capability.capability_id in self._capabilities:
            raise ValueError(f'Capability 已注册: {capability.capability_id}')
        self._capabilities[capability.capability_id] = capability

    def require(self, capability_id: str) -> EngineeringCapability:
        try:
            return self._capabilities[capability_id]
        except KeyError as exc:
            raise ToolExecutionError(
                'CAPABILITY_NOT_REGISTERED',
                f'未注册工程能力: {capability_id}',
            ) from exc

    def list_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._capabilities))

    def tool_schemas(self, capability_ids: list[str] | tuple[str, ...] | set[str]) -> list[dict[str, Any]]:
        ordered = sorted(dict.fromkeys(str(item) for item in capability_ids))
        return [self.require(capability_id).tool_schema() for capability_id in ordered]

    def runtime_context(self, capability_ids: list[str] | tuple[str, ...] | set[str]) -> list[dict[str, Any]]:
        ordered = sorted(dict.fromkeys(str(item) for item in capability_ids))
        return [self.require(capability_id).runtime_descriptor() for capability_id in ordered]


@dataclass(frozen=True)
class CapabilityExecutionContext:
    owner: str | None = None
    session_id: str | None = None
    project_id: str | None = None
    run_id: str | None = None
    workflow_state: dict[str, Any] = field(default_factory=dict)


class CapabilityDispatcher:
    """统一做 Capability 发现、阶段授权与类型校验；业务执行仍委托既有服务。"""

    def __init__(self, registry: CapabilityRegistry) -> None:
        self.registry = registry

    def authorize_and_validate(
        self,
        capability_id: str,
        payload: dict[str, Any] | BaseModel,
        *,
        allowed_capabilities: list[str] | tuple[str, ...] | set[str],
        approved: bool = False,
        idempotency_key: str | None = None,
    ) -> BaseModel:
        capability = self.registry.require(capability_id)
        allowed = {str(item) for item in allowed_capabilities}
        if capability_id not in allowed:
            raise ToolExecutionError(
                'CAPABILITY_NOT_ALLOWED',
                f'当前工作流阶段未授权工程能力: {capability_id}',
            )
        if capability.requires_approval and not approved:
            raise ToolExecutionError('APPROVAL_REQUIRED', f'工程能力 {capability_id} 需要审批')
        if capability.risk is not ToolRisk.READ_ONLY and not idempotency_key:
            raise ToolExecutionError(
                'IDEMPOTENCY_KEY_REQUIRED',
                f'有副作用工程能力 {capability_id} 必须提供幂等键',
            )
        try:
            return capability.input_model.model_validate(payload)
        except ValidationError as exc:
            raise ToolExecutionError(
                'INPUT_VALIDATION_ERROR',
                f'工程能力 {capability_id} 输入校验失败',
                details=exc.errors(include_url=False),
            ) from exc
