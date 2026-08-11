"""Adapter for stdio-based external tool extensions."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple

from agent.tools.contracts import (
    FrozenJsonObject,
    ToolAdapter,
    ToolDescriptor,
    ToolError,
    ToolInvocation,
    ToolOriginKind,
    ToolResult,
    ToolStatus,
    freeze_json_like,
    thaw_json_like,
)
from agent.tools.extension_manifest_parser import (
    SUPPORTED_PROTOCOL as _SUPPORTED_PROTOCOL,
)
from agent.tools.extension_manifest_parser import (
    ManifestParseError,
    ManifestProtocolError,
    ManifestStructureError,
    load_extension_manifest_bytes,
    validate_transport_capabilities,
)
from agent.tools.stdio_process import ProcessFailure, run_stdio_process

SUPPORTED_PROTOCOL = _SUPPORTED_PROTOCOL
MAX_OUTPUT_BYTES = 1_048_576
MAX_STDERR_BYTES = 1_048_576


@dataclass(frozen=True)
class ExtensionManifest:
    """Historical public manifest model exported by the stdio adapter."""

    id: str
    version: str
    protocol_version: str
    transport: str
    entrypoint: Tuple[str, ...]
    timeout_seconds: int
    tools: Tuple[Dict[str, Any], ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class _ManifestSnapshot:
    id: str
    version: str
    protocol_version: str
    transport: str
    entrypoint: Tuple[str, ...]
    timeout_seconds: int
    tools: Tuple[FrozenJsonObject, ...]


def load_extension_manifest(path: str | Path) -> ExtensionManifest:
    manifest_path = Path(path).expanduser().resolve()
    try:
        parsed = load_extension_manifest_bytes(
            manifest_path.read_bytes(),
            mode="legacy_stdio_compatibility",
        )
    except ManifestProtocolError as exc:
        raise ValueError(str(exc)) from None
    except (ManifestParseError, ManifestStructureError) as exc:
        raise ValueError(str(exc)) from None
    return ExtensionManifest(
        id=parsed.id,
        version=parsed.version,
        protocol_version=parsed.protocol_version,
        transport=parsed.transport,
        entrypoint=parsed.entrypoint,
        timeout_seconds=parsed.timeout_seconds,
        tools=parsed.tools,
    )


class StdioToolAdapter(ToolAdapter):
    """Executes a stdio-based tool extension as a subprocess."""

    _manifest_snapshot: _ManifestSnapshot
    _cwd: Path | None
    __slots__ = ("_manifest_snapshot", "_cwd")

    def __init__(self, manifest: ExtensionManifest, *, cwd: str | Path | None = None) -> None:
        for tool in manifest.tools:
            if not isinstance(tool, Mapping):
                raise TypeError("tools do adapter devem ser objetos JSON")
            validate_transport_capabilities(
                manifest.transport,
                tool.get("capabilities", []),
                tool_name=str(tool.get("name", "tool")),
            )
        copied_tools = tuple(freeze_json_like(tool) for tool in manifest.tools)
        if not all(isinstance(tool, FrozenJsonObject) for tool in copied_tools):
            raise TypeError("tools do adapter devem ser objetos JSON")
        snapshot = _ManifestSnapshot(
            id=manifest.id,
            version=manifest.version,
            protocol_version=manifest.protocol_version,
            transport=manifest.transport,
            entrypoint=tuple(manifest.entrypoint),
            timeout_seconds=manifest.timeout_seconds,
            tools=copied_tools,
        )
        object.__setattr__(self, "_manifest_snapshot", snapshot)
        object.__setattr__(
            self,
            "_cwd",
            Path(cwd).expanduser().resolve() if cwd is not None else None,
        )

    def __setattr__(self, name: str, value: object) -> None:
        del value
        raise AttributeError(f"configuração do adapter é somente leitura: {name}")

    @property
    def manifest(self) -> ExtensionManifest:
        snapshot = self._manifest_snapshot
        return ExtensionManifest(
            id=snapshot.id,
            version=snapshot.version,
            protocol_version=snapshot.protocol_version,
            transport=snapshot.transport,
            entrypoint=snapshot.entrypoint,
            timeout_seconds=snapshot.timeout_seconds,
            tools=tuple(thaw_json_like(tool) for tool in snapshot.tools),
        )

    @property
    def cwd(self) -> Path | None:
        return self._cwd

    def descriptors(self) -> Tuple[ToolDescriptor, ...]:
        return tuple(
            self._descriptor_from_tool(tool) for tool in self._manifest_snapshot.tools
        )

    def invoke(self, invocation: ToolInvocation) -> ToolResult:
        descriptor = self._descriptor_for(invocation.tool_name)
        if descriptor is None:
            return self._failure(invocation, ToolStatus.UNAVAILABLE, "TOOL_NOT_FOUND", "Ferramenta não encontrada.")

        payload = {
            "tool": invocation.tool_name,
            "args": invocation.args,
            "invocation_id": invocation.invocation_id,
        }
        process_result = self._run_process(
            payload,
            invocation.invocation_id,
            cancellation_token=getattr(invocation, "cancellation_token", None),
            cancellation_event=getattr(invocation, "cancellation_event", None),
        )
        if isinstance(process_result, ToolResult):
            return process_result
        response_or_error = self._decode_response(process_result.stdout, invocation.invocation_id)
        if isinstance(response_or_error, ToolResult):
            return response_or_error
        response = response_or_error
        status = response.get("status", "succeeded")
        return self._result_for_status(invocation.invocation_id, status, response)

    def _run_process(
        self,
        payload: dict[str, Any],
        invocation_id: str,
        *,
        cancellation_token: Any | None = None,
        cancellation_event: Any | None = None,
    ) -> Any:
        outcome = run_stdio_process(
            entrypoint=self._manifest_snapshot.entrypoint,
            cwd=self._cwd,
            timeout_seconds=self._manifest_snapshot.timeout_seconds,
            payload=payload,
            stdout_limit=MAX_OUTPUT_BYTES,
            stderr_limit=MAX_STDERR_BYTES,
            cancellation_token=cancellation_token,
            cancellation_event=cancellation_event,
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

    def _descriptor_from_tool(self, tool: Mapping[str, Any]) -> ToolDescriptor:
        name = tool.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Tool name invalido")
        description = tool.get("description", "")
        if not isinstance(description, str):
            raise ValueError(f"Descricao invalida para tool '{name}'")
        schema = tool.get("schema", {})
        if not isinstance(schema, Mapping):
            raise ValueError(f"Schema invalido para tool '{name}'")
        capabilities = tool.get("capabilities", [])
        if not isinstance(capabilities, (list, tuple)) or any(
            not isinstance(capability, str) or not capability.strip()
            for capability in capabilities
        ):
            raise ValueError(f"Capabilities invalidas para tool '{name}'")
        validate_transport_capabilities(
            self._manifest_snapshot.transport,
            capabilities,
            tool_name=name,
        )
        cost = tool.get("cost", 5)
        if not isinstance(cost, int) or isinstance(cost, bool) or cost < 0:
            raise ValueError(f"Custo invalido para tool '{name}'")
        timeout = self._manifest_snapshot.timeout_seconds
        if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
            raise ValueError("timeout_seconds invalido")
        return ToolDescriptor(
            name=name,
            description=description,
            schema=dict(schema),
            capabilities=frozenset(capabilities),
            cost=cost,
            timeout_seconds=timeout,
            category="EXECUTE",
            origin_kind=ToolOriginKind.EXTENSION,
            extension_id=self._manifest_snapshot.id,
            adapter_id=self._manifest_snapshot.id,
            source_version=self._manifest_snapshot.version,
            protocol_version=self._manifest_snapshot.protocol_version,
            supports_cancellation=False,
        )

    def _descriptor_for(self, tool_name: str) -> Optional[ToolDescriptor]:
        for descriptor in self.descriptors():
            if descriptor.name == tool_name:
                return descriptor
        return None
