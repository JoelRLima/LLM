"""Adapter wrapping legacy SkillRegistry into the canonical ToolAdapter interface."""

from __future__ import annotations

from typing import Tuple

from agent.skills.descriptor import SkillDescriptor
from agent.skills.registry import SkillRegistry
from agent.tools.contracts import (
    ToolAdapter,
    ToolDescriptor,
    ToolError,
    ToolInvocation,
    ToolResult,
    ToolStatus,
)


class BuiltinToolAdapter(ToolAdapter):
    """Adapts builtin skills to canonical tool descriptors and invocations."""

    def __init__(self, skill_registry: SkillRegistry) -> None:
        self.skill_registry = skill_registry

    def _convert_descriptor(self, descriptor: SkillDescriptor) -> ToolDescriptor:
        spec = descriptor.spec
        capabilities = frozenset(c.value for c in spec.capabilities)
        return ToolDescriptor(
            name=spec.name,
            description=descriptor.skill.description,
            schema=self._normalize_schema(descriptor.schema),
            capabilities=capabilities,
            cost=spec.cost,
            timeout_seconds=spec.timeout_seconds,
            cacheable=spec.cacheable,
            idempotent=spec.idempotent,
            category=spec.category,
            adapter_id="builtin",
            source_version="1",
        )

    @staticmethod
    def _normalize_schema(schema: object) -> dict[str, object]:
        if not isinstance(schema, dict):
            raise ValueError("Schema builtin inválido: esperado objeto JSON")
        normalized: dict[str, object] = dict(schema)
        properties = normalized.get("properties")
        if isinstance(properties, dict):
            normalized["properties"] = {
                key: {"type": value} if isinstance(value, str) else value
                for key, value in properties.items()
            }
        normalized.setdefault("type", "object")
        return normalized

    def descriptors(self) -> Tuple[ToolDescriptor, ...]:
        return tuple(
            self._convert_descriptor(descriptor)
            for descriptor in self.skill_registry
        )

    def invoke(self, invocation: ToolInvocation) -> ToolResult:
        try:
            skill = self.skill_registry.skill(invocation.tool_name)
        except KeyError as exc:
            return ToolResult(
                invocation_id=invocation.invocation_id,
                status=ToolStatus.UNAVAILABLE,
                error=ToolError("TOOL_NOT_FOUND", str(exc)),
                message=f"Ferramenta não registrada: {invocation.tool_name}",
            )

        try:
            raw_result = skill.execute(invocation.args)
        except Exception as exc:
            return ToolResult(
                invocation_id=invocation.invocation_id,
                status=ToolStatus.FAILED,
                error=ToolError("EXECUTION_ERROR", str(exc)),
                message=f"Exceção durante a execução da ferramenta '{invocation.tool_name}': {exc}",
            )

        if not isinstance(raw_result, dict):
            return ToolResult(
                invocation_id=invocation.invocation_id,
                status=ToolStatus.SUCCEEDED,
                data=raw_result,
            )

        raw_status = raw_result.get("status")
        if raw_status is not None:
            try:
                status = ToolStatus(str(raw_status))
            except ValueError:
                return ToolResult(
                    invocation_id=invocation.invocation_id,
                    status=ToolStatus.PROTOCOL_ERROR,
                    error=ToolError("INVALID_STATUS", f"Status desconhecido: {raw_status}"),
                    message="Resultado inválido da skill builtin.",
                )
        else:
            status = ToolStatus.SUCCEEDED if raw_result.get("ok") is True else ToolStatus.FAILED
        error_text = raw_result.get("error")
        message_text = raw_result.get("message")
        data = raw_result.get("data")

        if status == ToolStatus.SUCCEEDED:
            return ToolResult(
                invocation_id=invocation.invocation_id,
                status=ToolStatus.SUCCEEDED,
                data=data,
                message=message_text,
            )

        return ToolResult(
            invocation_id=invocation.invocation_id,
            status=status,
            data=data,
            error=ToolError(
                code="TOOL_ERROR",
                message=str(error_text or message_text or "Falha na execução da ferramenta."),
            ),
            message=message_text,
        )
