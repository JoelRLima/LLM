"""Adapter for stdio-based external tool extensions."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from agent.tools.contracts import ToolAdapter, ToolDescriptor, ToolError, ToolInvocation, ToolResult, ToolStatus
from agent.tools.stdio_process import ProcessFailure, run_stdio_process

SUPPORTED_PROTOCOL = "1.0"
MAX_OUTPUT_BYTES = 1_048_576
MAX_STDERR_BYTES = 1_048_576
_ALLOWED_MANIFEST_FIELDS = frozenset(
    {
        "id",
        "version",
        "protocol_version",
        "transport",
        "entrypoint",
        "timeout_seconds",
        "tools",
    }
)
_ALLOWED_TOOL_FIELDS = frozenset(
    {"name", "description", "schema", "capabilities", "cost"}
)


@dataclass(frozen=True)
class ExtensionManifest:
    id: str
    version: str
    protocol_version: str
    transport: str
    entrypoint: Tuple[str, ...]
    timeout_seconds: int
    tools: Tuple[Dict[str, Any], ...] = field(default_factory=tuple)


def _require_non_blank_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Campo '{field_name}' invalido no manifest")
    return value


def _validate_manifest_identity(payload: dict[str, Any]) -> None:
    for key in ("id", "version", "protocol_version", "transport"):
        _require_non_blank_string(payload[key], key)
    if payload["transport"] != "stdio":
        raise ValueError("Somente transport stdio é suportado")
    if payload["protocol_version"] != SUPPORTED_PROTOCOL:
        raise ValueError("Versão de protocolo não suportada")


def _reject_unknown_fields(
    payload: dict[str, Any], allowed: frozenset[str], context: str
) -> None:
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ValueError(
            f"Campos desconhecidos em {context}: {', '.join(unknown)}"
        )


def _parse_manifest_entrypoint(payload: dict[str, Any]) -> Tuple[str, ...]:
    raw_entrypoint = payload["entrypoint"]
    if not isinstance(raw_entrypoint, list) or not raw_entrypoint:
        raise ValueError("entrypoint invalido")
    entrypoint = tuple(raw_entrypoint)
    if not all(isinstance(item, str) and item.strip() for item in entrypoint):
        raise ValueError("entrypoint inválido")
    return entrypoint


def _validate_manifest_tool(tool: Any) -> Dict[str, Any]:
    if not isinstance(tool, dict):
        raise ValueError("Manifest contem tool invalida")
    _reject_unknown_fields(tool, _ALLOWED_TOOL_FIELDS, "tool")
    name = tool.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Manifest contem tool invalida")
    if "description" in tool and not isinstance(tool["description"], str):
        raise ValueError(f"Descricao invalida para tool '{name}'")
    if "schema" in tool and not isinstance(tool["schema"], dict):
        raise ValueError(f"Schema invalido para tool '{name}'")
    capabilities = tool.get("capabilities", [])
    if not isinstance(capabilities, list) or any(
        not isinstance(capability, str) or not capability.strip()
        for capability in capabilities
    ):
        raise ValueError(f"Capabilities invalidas para tool '{name}'")
    if "cost" in tool and (
        not isinstance(tool["cost"], int) or isinstance(tool["cost"], bool)
    ):
        raise ValueError(f"Custo invalido para tool '{name}'")
    if "cost" in tool and tool["cost"] < 0:
        raise ValueError(f"Custo invalido para tool '{name}'")
    return tool


def _parse_manifest_tools(payload: dict[str, Any]) -> Tuple[Dict[str, Any], ...]:
    raw_tools = payload["tools"]
    if not isinstance(raw_tools, list) or not raw_tools:
        raise ValueError("Manifest deve conter uma lista nao vazia de tools")
    tools = tuple(_validate_manifest_tool(tool) for tool in raw_tools)
    names = [tool["name"] for tool in tools]
    if len(names) != len(set(names)):
        raise ValueError("Manifest contém tools duplicadas")
    return tools


def _parse_manifest_timeout(payload: dict[str, Any]) -> int:
    timeout_value = payload["timeout_seconds"]
    if not isinstance(timeout_value, int) or isinstance(timeout_value, bool):
        raise ValueError("timeout_seconds invalido")
    if timeout_value <= 0 or timeout_value > 3600:
        raise ValueError("timeout_seconds fora do limite")
    return timeout_value


def load_extension_manifest(path: str | Path) -> ExtensionManifest:
    manifest_path = Path(path).expanduser().resolve()
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Manifest de extensão deve ser um objeto JSON")
    _reject_unknown_fields(payload, _ALLOWED_MANIFEST_FIELDS, "manifest")
    required = (
        "id",
        "version",
        "protocol_version",
        "transport",
        "entrypoint",
        "timeout_seconds",
        "tools",
    )
    if any(key not in payload for key in required):
        raise ValueError("Manifest de extensão incompleto")
    _validate_manifest_identity(payload)
    entrypoint = _parse_manifest_entrypoint(payload)
    tools = _parse_manifest_tools(payload)
    timeout = _parse_manifest_timeout(payload)
    return ExtensionManifest(
        id=payload["id"], version=payload["version"],
        protocol_version=payload["protocol_version"], transport=payload["transport"],
        entrypoint=entrypoint, timeout_seconds=timeout, tools=tools,
    )


class StdioToolAdapter(ToolAdapter):
    """Executes a stdio-based tool extension as a subprocess."""

    def __init__(self, manifest: ExtensionManifest, *, cwd: str | Path | None = None) -> None:
        self.manifest = manifest
        self.cwd = Path(cwd).expanduser().resolve() if cwd is not None else None

    def descriptors(self) -> Tuple[ToolDescriptor, ...]:
        return tuple(self._descriptor_from_tool(tool) for tool in self.manifest.tools)

    def invoke(self, invocation: ToolInvocation) -> ToolResult:
        descriptor = self._descriptor_for(invocation.tool_name)
        if descriptor is None:
            return self._failure(invocation, ToolStatus.UNAVAILABLE, "TOOL_NOT_FOUND", "Ferramenta não encontrada.")

        payload = {
            "tool": invocation.tool_name,
            "args": invocation.args,
            "invocation_id": invocation.invocation_id,
        }
        process_result = self._run_process(payload, invocation.invocation_id)
        if isinstance(process_result, ToolResult):
            return process_result
        response_or_error = self._decode_response(process_result.stdout, invocation.invocation_id)
        if isinstance(response_or_error, ToolResult):
            return response_or_error
        response = response_or_error
        status = response.get("status", "succeeded")
        return self._result_for_status(invocation.invocation_id, status, response)

    def _run_process(self, payload: dict[str, Any], invocation_id: str) -> Any:
        outcome = run_stdio_process(
            entrypoint=self.manifest.entrypoint,
            cwd=self.cwd,
            timeout_seconds=self.manifest.timeout_seconds,
            payload=payload,
            stdout_limit=MAX_OUTPUT_BYTES,
            stderr_limit=MAX_STDERR_BYTES,
        )
        if outcome.failure is not None:
            failure: ProcessFailure = outcome.failure
            return self._failure_by_id(
                invocation_id,
                failure.status,
                failure.code,
                failure.detail,
                failure.message,
            )
        if outcome.completed is None:
            return self._failure_by_id(
                invocation_id,
                ToolStatus.UNAVAILABLE,
                "PROCESS_ERROR",
                "Execução externa não produziu resultado.",
                "Não foi possível executar a extensão.",
            )
        return outcome.completed

    def _decode_response(self, output: str, invocation_id: str) -> Any:
        if len(output.encode("utf-8")) > MAX_OUTPUT_BYTES:
            return self._failure_by_id(invocation_id, ToolStatus.PROTOCOL_ERROR, "OUTPUT_LIMIT", "Saída da extensão excedeu o limite.", "Resposta externa muito grande.")
        lines = [line for line in output.splitlines() if line.strip()]
        if len(lines) != 1:
            return self._failure_by_id(invocation_id, ToolStatus.PROTOCOL_ERROR, "INVALID_RESPONSE", "A extensão deve emitir exatamente uma linha JSON.", "Protocolo stdio inválido.")
        try:
            response = json.loads(lines[0])
        except json.JSONDecodeError as exc:
            return self._failure_by_id(invocation_id, ToolStatus.PROTOCOL_ERROR, "INVALID_RESPONSE", str(exc), "Resposta inválida da extensão.")
        if not isinstance(response, dict):
            return self._failure_by_id(invocation_id, ToolStatus.PROTOCOL_ERROR, "INVALID_RESPONSE", "Resposta deve ser um objeto JSON.", "Resposta inválida da extensão.")
        response_id = response.get("invocation_id")
        if not isinstance(response_id, str) or not response_id:
            return self._failure_by_id(invocation_id, ToolStatus.PROTOCOL_ERROR, "MISSING_INVOCATION_ID", "invocation_id ausente ou vazio.", "Resposta sem invocation_id.")
        if response_id != invocation_id:
            return self._failure_by_id(invocation_id, ToolStatus.PROTOCOL_ERROR, "INVOCATION_MISMATCH", "invocation_id divergente.", "Resposta não corresponde à invocação.")
        return response

    @staticmethod
    def _result_for_status(invocation_id: str, status: Any, response: dict[str, Any]) -> ToolResult:
        if status == "succeeded":
            return ToolResult(invocation_id=invocation_id, status=ToolStatus.SUCCEEDED, data=response.get("data"), message=response.get("message"))
        if status == "failed":
            message = str(response.get("message") or "Falha na extensão.")
            return ToolResult(invocation_id=invocation_id, status=ToolStatus.FAILED, error=ToolError("TOOL_ERROR", message), message=response.get("message"))
        return ToolResult(invocation_id=invocation_id, status=ToolStatus.PROTOCOL_ERROR, error=ToolError("INVALID_RESPONSE", f"status desconhecido: {status}"), message="Resposta inválida da extensão.")

    @staticmethod
    def _failure(invocation: ToolInvocation, status: ToolStatus, code: str, detail: str) -> ToolResult:
        return StdioToolAdapter._failure_by_id(invocation.invocation_id, status, code, detail, detail)

    @staticmethod
    def _failure_by_id(invocation_id: str, status: ToolStatus, code: str, detail: str, message: str) -> ToolResult:
        return ToolResult(invocation_id=invocation_id, status=status, error=ToolError(code, detail), message=message)

    def _descriptor_from_tool(self, tool: Dict[str, Any]) -> ToolDescriptor:
        name = tool.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Tool name invalido")
        description = tool.get("description", "")
        if not isinstance(description, str):
            raise ValueError(f"Descricao invalida para tool '{name}'")
        schema = tool.get("schema", {})
        if not isinstance(schema, dict):
            raise ValueError(f"Schema invalido para tool '{name}'")
        capabilities = tool.get("capabilities", [])
        if not isinstance(capabilities, list) or any(
            not isinstance(capability, str) or not capability.strip()
            for capability in capabilities
        ):
            raise ValueError(f"Capabilities invalidas para tool '{name}'")
        cost = tool.get("cost", 5)
        if not isinstance(cost, int) or isinstance(cost, bool) or cost < 0:
            raise ValueError(f"Custo invalido para tool '{name}'")
        timeout = self.manifest.timeout_seconds
        if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
            raise ValueError("timeout_seconds invalido")
        return ToolDescriptor(
            name=name,
            description=description,
            schema=schema,
            capabilities=frozenset(capabilities),
            cost=cost,
            timeout_seconds=timeout,
            category="EXECUTE",
            adapter_id=self.manifest.id,
            source_version=self.manifest.version,
            protocol_version=self.manifest.protocol_version,
            supports_cancellation=False,
        )

    def _descriptor_for(self, tool_name: str) -> Optional[ToolDescriptor]:
        for descriptor in self.descriptors():
            if descriptor.name == tool_name:
                return descriptor
        return None
