from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


def narrative_safe(value: Any) -> Any:
    """移除不应进入自然语言 grounding 的哈希和节点拓扑字段。"""
    blocked = {'sha256', 'nodepairs', 'node_i', 'node_j'}
    if isinstance(value, dict):
        return {
            key: narrative_safe(child)
            for key, child in value.items()
            if str(key).replace('-', '_').lower() not in blocked
        }
    if isinstance(value, list):
        return [narrative_safe(child) for child in value]
    return value


@dataclass(frozen=True)
class PreparedApproval:
    """工程 Agent 生成的审批准备结果。

    Agent 只负责计算结果，不写 run、审批或 Artifact。持久化和命令流注册
    由 AgentService 在跨请求工作流边界统一完成。
    """

    passed: bool
    frozen_action: dict[str, Any] | None = None
    approval_action: str | None = None
    approval_summary: str | None = None
    preflight: dict[str, Any] = field(default_factory=dict)
    contract_updates: dict[str, Any] = field(default_factory=dict)
    plan: list[str] = field(default_factory=list)
    failure_status: str | None = None
    failure_message: str | None = None
    extra_run_fields: dict[str, Any] = field(default_factory=dict)
    pending_command_streams: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class ReviewOutcome:
    """工程 Job 的确定性证据验收结果。"""

    accepted: bool
    run_status: str
    evidence_mode: str
    checks: dict[str, bool]
    message: str
    extra: dict[str, Any] = field(default_factory=dict)


class EngineeringAgent(ABC):
    """一种工程任务的完整定义；执行编排仍由 AgentService 驱动。"""

    task_type: str
    approval_action: str
    workflow_paths: dict[str, str] = {}

    @abstractmethod
    def plan(self, context: Any) -> Any:
        """解析意图并构造工程契约。"""

    @abstractmethod
    def prepare_approval(
        self,
        run: dict[str, Any],
        *,
        mapping: dict[str, Any],
        standard_artifact_id: str | None,
        standard_sha256: str | None,
    ) -> PreparedApproval:
        """预检并构造冻结动作，不写入持久化状态。"""

    @abstractmethod
    def review(
        self,
        job: dict[str, Any],
        *,
        workflow_contract: dict[str, Any] | None = None,
    ) -> ReviewOutcome:
        """按确定性证据门槛验收 Job。"""

    @abstractmethod
    def build_report(
        self,
        run: dict[str, Any],
        job: dict[str, Any],
        outcome: ReviewOutcome,
    ) -> dict[str, Any]:
        """构造报告预览，不注册 Artifact。"""

    @abstractmethod
    def narrative_facts(
        self,
        report: dict[str, Any],
        job: dict[str, Any],
    ) -> dict[str, Any]:
        """挑选可安全交给 LLM 的事实。"""
