from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


CapabilityState = Literal['LIVE', 'MOCK_ONLY', 'DISABLED']
CapabilityMode = Literal['AGENT', 'PLATFORM_API', 'CONTROLLED_AGENT', 'MOCK']


class RealJobRequest(BaseModel):
    """共享执行器的最小请求合同；实现阶段再绑定具体 JobType 模型。"""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra='forbid',
        strict=True,
    )

    job_type: str = Field(min_length=1, alias='jobType')
    run_id: str | None = Field(default=None, alias='runId')
    source: Literal['AGENT', 'PLATFORM_API']
    solver: str | None = None
    scenario: str | None = None
    input_artifact_ids: list[str] = Field(default_factory=list, alias='inputArtifactIds')
    frozen_config: dict[str, Any] = Field(default_factory=dict, alias='frozenConfig')
    budget: dict[str, int | float] = Field(default_factory=dict)
    idempotency_key: str = Field(min_length=1, alias='idempotencyKey')


class CapabilityDescriptor(BaseModel):
    """对 API 与 Live 前端公开的能力状态。"""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra='forbid',
    )

    job_type: str = Field(min_length=1, alias='jobType')
    mode: CapabilityMode
    status: CapabilityState
    handler: str = Field(min_length=1)
    solvers: tuple[str, ...] = ()
    scenarios: tuple[str, ...] = ()
    solver_scenarios: dict[str, tuple[str, ...]] = Field(
        default_factory=dict, alias='solverScenarios',
    )
    input_artifacts: tuple[str, ...] = Field(default=(), alias='inputArtifacts')
    output_artifacts: tuple[str, ...] = Field(default=(), alias='outputArtifacts')
    supports_cancel: bool = Field(default=False, alias='supportsCancel')
    supports_resume: bool = Field(default=False, alias='supportsResume')
    reason: str = Field(min_length=1)
    unlock_requirements: tuple[str, ...] = Field(default=(), alias='unlockRequirements')

    def supports(self, *, solver: str, scenario: str) -> bool:
        """按 solver × scenario 判定组合是否被本能力广告。

        `solverScenarios` 为空表示该能力对所有登记求解器支持同一组工况。
        """
        if solver not in self.solvers:
            return False
        return scenario in self.solver_scenarios.get(solver, self.scenarios)


class CapabilityCatalog(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra='forbid',
    )

    version: str = Field(min_length=1)
    data: tuple[CapabilityDescriptor, ...]
