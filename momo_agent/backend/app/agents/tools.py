from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

from pydantic import BaseModel, ValidationError


class ToolRisk(str, Enum):
    READ_ONLY = 'READ_ONLY'
    ARTIFACT_WRITE = 'ARTIFACT_WRITE'
    SOLVER_EXECUTION = 'SOLVER_EXECUTION'
    MUTATING = 'MUTATING'


class ToolExecutionError(ValueError):
    """工具边界的结构化错误。"""

    def __init__(self, code: str, message: str, *, details: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


class ToolApprovalRequired(ToolExecutionError, RuntimeError):
    """工具需要人工审批但调用方未提供审批证明。"""

    def __init__(self, message: str) -> None:
        super().__init__('APPROVAL_REQUIRED', message)


class ToolDescriptor(BaseModel):
    name: str
    version: str
    description: str
    risk: ToolRisk
    requires_approval: bool
    timeout_seconds: int | None
    artifact_kinds: tuple[str, ...]
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]


@dataclass(frozen=True)
class TypedAgentTool:
    name: str
    description: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    risk: ToolRisk
    requires_approval: bool
    handler: Callable[[BaseModel], BaseModel | dict[str, Any]]
    version: str = '1.0.0'
    timeout_seconds: int | None = None
    artifact_kinds: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.risk in {ToolRisk.SOLVER_EXECUTION, ToolRisk.MUTATING} and not self.requires_approval:
            raise ValueError('有副作用工具必须要求审批')
        if not self.version.strip():
            raise ValueError('工具版本不能为空')
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise ValueError('工具超时必须为正整数')


class TypedToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, TypedAgentTool] = {}

    def register(self, tool: TypedAgentTool) -> None:
        if tool.name in self._tools:
            raise ValueError(f'工具已注册: {tool.name}')
        self._tools[tool.name] = tool

    def describe(self, name: str) -> ToolDescriptor:
        try:
            tool = self._tools[name]
        except KeyError as exc:
            raise ToolExecutionError('TOOL_NOT_FOUND', f'未注册工具: {name}') from exc
        return ToolDescriptor(
            name=tool.name,
            version=tool.version,
            description=tool.description,
            risk=tool.risk,
            requires_approval=tool.requires_approval,
            timeout_seconds=tool.timeout_seconds,
            artifact_kinds=tool.artifact_kinds,
            input_schema=tool.input_model.model_json_schema(),
            output_schema=tool.output_model.model_json_schema(),
        )

    def list_tools(self) -> list[ToolDescriptor]:
        return [self.describe(name) for name in sorted(self._tools)]

    def execute(
        self,
        name: str,
        payload: BaseModel | dict[str, Any],
        *,
        approved: bool = False,
        idempotency_key: str | None = None,
    ) -> BaseModel:
        try:
            tool = self._tools[name]
        except KeyError as exc:
            raise ToolExecutionError('TOOL_NOT_FOUND', f'未注册工具: {name}') from exc
        if tool.requires_approval and not approved:
            raise ToolApprovalRequired(f'工具 {name} 需要人工审批')
        if tool.risk is not ToolRisk.READ_ONLY and not idempotency_key:
            raise ToolExecutionError(
                'IDEMPOTENCY_KEY_REQUIRED',
                f'有副作用工具 {name} 必须提供 idempotency_key',
            )
        try:
            validated_input = tool.input_model.model_validate(payload)
        except ValidationError as exc:
            details = self._validation_details(exc)
            raise ToolExecutionError(
                'INPUT_VALIDATION_ERROR',
                f'工具 {name} 输入校验失败: {self._validation_summary(details)}',
                details=details,
            ) from exc
        input_details = self._normalization_details(
            self._canonical_payload(tool.input_model, payload),
            validated_input.model_dump(mode='json', by_alias=True),
        )
        if input_details:
            raise ToolExecutionError(
                'INPUT_VALIDATION_ERROR',
                f'工具 {name} 输入校验不得静默修改已提供参数: {self._validation_summary(input_details)}',
                details=input_details,
            )
        try:
            raw_output = tool.handler(validated_input)
            validated_output = tool.output_model.model_validate(raw_output)
        except ValidationError as exc:
            details = self._validation_details(exc)
            raise ToolExecutionError(
                'OUTPUT_VALIDATION_ERROR',
                f'工具 {name} 输出校验失败: {self._validation_summary(details)}',
                details=details,
            ) from exc
        except ToolExecutionError:
            raise
        except Exception as exc:
            # handler 的业务异常不能越过工具边界变成 HTTP 500。
            raise ToolExecutionError(
                'TOOL_HANDLER_FAILED',
                f'工具 {name} 执行失败: {exc}',
                details={'toolName': name, 'errorType': type(exc).__name__},
            ) from exc
        output_details = self._normalization_details(
            self._canonical_payload(tool.output_model, raw_output),
            validated_output.model_dump(mode='json', by_alias=True),
        )
        if output_details:
            raise ToolExecutionError(
                'OUTPUT_VALIDATION_ERROR',
                f'工具 {name} 输出校验不得静默修改或裁剪字段: {self._validation_summary(output_details)}',
                details=output_details,
            )
        return validated_output

    @staticmethod
    def _canonical_payload(
        model: type[BaseModel],
        payload: BaseModel | dict[str, Any],
    ) -> dict[str, Any]:
        if isinstance(payload, BaseModel):
            return payload.model_dump(mode='json', by_alias=True)
        aliases: dict[str, str] = {}
        for field_name, field in model.model_fields.items():
            alias = field.serialization_alias or field.alias or field_name
            if isinstance(alias, str):
                aliases[field_name] = alias
                aliases[alias] = alias
        return {
            aliases.get(str(key), str(key)): TypedToolRegistry._canonical_value(value)
            for key, value in payload.items()
        }

    @staticmethod
    def _canonical_value(value: Any) -> Any:
        """仅展开调用方已构造的类型对象，不改变普通 JSON 标量。"""
        if isinstance(value, BaseModel):
            return value.model_dump(mode='json', by_alias=True)
        if isinstance(value, dict):
            return {
                str(key): TypedToolRegistry._canonical_value(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [TypedToolRegistry._canonical_value(item) for item in value]
        return value

    @classmethod
    def _normalization_details(
        cls,
        provided: Any,
        effective: Any,
        path: tuple[str, ...] = (),
    ) -> list[dict[str, Any]]:
        if isinstance(provided, dict):
            if not isinstance(effective, dict):
                return [cls._normalization_detail(path, provided, effective)]
            details: list[dict[str, Any]] = []
            for key, value in provided.items():
                key_path = (*path, str(key))
                if key not in effective:
                    details.append(cls._normalization_detail(key_path, value, None))
                    continue
                details.extend(cls._normalization_details(value, effective[key], key_path))
            return details
        if isinstance(provided, (list, tuple)):
            if not isinstance(effective, (list, tuple)) or len(provided) != len(effective):
                return [cls._normalization_detail(path, provided, effective)]
            details = []
            for index, value in enumerate(provided):
                details.extend(
                    cls._normalization_details(value, effective[index], (*path, str(index)))
                )
            return details
        if type(provided) is not type(effective) or provided != effective:
            return [cls._normalization_detail(path, provided, effective)]
        return []

    @staticmethod
    def _normalization_detail(
        path: tuple[str, ...],
        provided: Any,
        effective: Any,
    ) -> dict[str, Any]:
        return {
            'location': '.'.join(path),
            'message': '校验会修改模型提供的参数或字段',
            'type': 'normalization_not_allowed',
            'provided': provided,
            'effective': effective,
        }

    @staticmethod
    def _validation_details(exc: ValidationError) -> list[dict[str, str]]:
        return [
            {
                'location': '.'.join(str(item) for item in error.get('loc', ())),
                'message': str(error.get('msg') or '校验失败'),
                'type': str(error.get('type') or 'validation_error'),
            }
            for error in exc.errors(include_url=False)
        ]

    @staticmethod
    def _validation_summary(details: list[dict[str, Any]]) -> str:
        return '; '.join(
            f"{item['location']}: {item['message']}" if item['location'] else item['message']
            for item in details
        )
