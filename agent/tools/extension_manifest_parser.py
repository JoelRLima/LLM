"""Reusable manifest parser with explicit legacy and strict policies."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, Literal, Tuple

SUPPORTED_PROTOCOL = "1.0"
ManifestParserMode = Literal["legacy_stdio_compatibility", "strict_catalog"]
_ALLOWED_MANIFEST_FIELDS = frozenset(
    {"id", "version", "protocol_version", "transport", "entrypoint", "timeout_seconds", "tools"}
)
_ALLOWED_TOOL_FIELDS = frozenset({"name", "description", "schema", "capabilities", "cost"})


class ManifestParseError(ValueError):
    """The manifest bytes cannot be parsed under the selected policy."""


class ManifestStructureError(ManifestParseError):
    """The manifest JSON shape or field types are invalid."""


class ManifestProtocolError(ManifestStructureError):
    """The manifest requests an unsupported protocol."""


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
        raise ManifestStructureError(f"Campo '{field_name}' invalido no manifest")
    return value


def _validate_manifest_identity(payload: dict[str, Any]) -> None:
    for key in ("id", "version", "protocol_version", "transport"):
        _require_non_blank_string(payload[key], key)
    if payload["transport"] != "stdio":
        raise ManifestStructureError("Somente transport stdio é suportado")
    if payload["protocol_version"] != SUPPORTED_PROTOCOL:
        raise ManifestProtocolError("Versão de protocolo não suportada")


def _reject_unknown_fields(payload: dict[str, Any], allowed: frozenset[str], context: str) -> None:
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ManifestStructureError(f"Campos desconhecidos em {context}: {', '.join(unknown)}")


def _parse_entrypoint(payload: dict[str, Any]) -> Tuple[str, ...]:
    raw_entrypoint = payload["entrypoint"]
    if not isinstance(raw_entrypoint, list) or not raw_entrypoint:
        raise ManifestStructureError("entrypoint invalido")
    entrypoint = tuple(raw_entrypoint)
    if not all(isinstance(item, str) and item.strip() for item in entrypoint):
        raise ManifestStructureError("entrypoint inválido")
    return entrypoint


def _validate_tool(tool: Any, transport: str) -> Dict[str, Any]:
    if not isinstance(tool, dict):
        raise ManifestStructureError("Manifest contem tool invalida")
    _reject_unknown_fields(tool, _ALLOWED_TOOL_FIELDS, "tool")
    name = tool.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ManifestStructureError("Manifest contem tool invalida")
    if "description" in tool and not isinstance(tool["description"], str):
        raise ManifestStructureError(f"Descricao invalida para tool '{name}'")
    if "schema" in tool and not isinstance(tool["schema"], dict):
        raise ManifestStructureError(f"Schema invalido para tool '{name}'")
    capabilities = tool.get("capabilities", [])
    if not isinstance(capabilities, list) or any(
        not isinstance(capability, str) or not capability.strip() for capability in capabilities
    ):
        raise ManifestStructureError(f"Capabilities invalidas para tool '{name}'")
    validate_transport_capabilities(transport, capabilities, tool_name=name)
    if "cost" in tool and (
        not isinstance(tool["cost"], int) or isinstance(tool["cost"], bool) or tool["cost"] < 0
    ):
        raise ManifestStructureError(f"Custo invalido para tool '{name}'")
    return tool


def validate_transport_capabilities(
    transport: str,
    capabilities: Any,
    *,
    tool_name: str = "tool",
) -> None:
    """Enforce the canonical effect required by each supported transport."""

    if transport == "stdio" and "process" not in capabilities:
        raise ManifestStructureError(
            f"Tool stdio '{tool_name}' requer capability process"
        )


def _parse_tools(payload: dict[str, Any], transport: str) -> Tuple[Dict[str, Any], ...]:
    raw_tools = payload["tools"]
    if not isinstance(raw_tools, list) or not raw_tools:
        raise ManifestStructureError("Manifest deve conter uma lista nao vazia de tools")
    tools = tuple(_validate_tool(tool, transport) for tool in raw_tools)
    names = [tool["name"] for tool in tools]
    if len(names) != len(set(names)):
        raise ManifestStructureError("Manifest contém tools duplicadas")
    return tools


def _parse_timeout(payload: dict[str, Any]) -> int:
    timeout_value = payload["timeout_seconds"]
    if not isinstance(timeout_value, int) or isinstance(timeout_value, bool):
        raise ManifestStructureError("timeout_seconds invalido")
    if timeout_value <= 0 or timeout_value > 3600:
        raise ManifestStructureError("timeout_seconds fora do limite")
    return timeout_value


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ManifestStructureError(f"Chave JSON duplicada no manifest: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ManifestParseError(f"Constante JSON nao suportada no manifest: {value}")


def load_extension_manifest_bytes(
    content: bytes,
    *,
    mode: ManifestParserMode = "strict_catalog",
) -> ExtensionManifest:
    """Validate exact bytes under strict catalog semantics."""

    if not isinstance(content, bytes):
        raise TypeError("content deve ser bytes")
    if mode not in {"legacy_stdio_compatibility", "strict_catalog"}:
        raise ValueError(f"Modo de parser desconhecido: {mode}")

    if mode == "legacy_stdio_compatibility":
        # This deliberately preserves the historical path API: json.loads
        # accepts NaN/Infinity and keeps the last duplicate key.
        payload = json.loads(content.decode("utf-8"))
    else:
        if not content:
            raise ManifestParseError("Manifest vazio")
        if content.startswith(b"\xef\xbb\xbf"):
            raise ManifestParseError("Manifest nao pode conter BOM UTF-8")
        try:
            payload = json.loads(
                content.decode("utf-8"),
                object_pairs_hook=_unique_object,
                parse_constant=_reject_constant,
            )
        except UnicodeDecodeError as exc:
            raise ManifestParseError("Manifest nao esta codificado em UTF-8") from exc
        except json.JSONDecodeError as exc:
            raise ManifestParseError("Manifest contem JSON invalido") from exc

    if not isinstance(payload, dict):
        raise ManifestStructureError("Manifest de extensão deve ser um objeto JSON")
    _reject_unknown_fields(payload, _ALLOWED_MANIFEST_FIELDS, "manifest")
    required = (
        "id", "version", "protocol_version", "transport", "entrypoint", "timeout_seconds", "tools"
    )
    if any(key not in payload for key in required):
        raise ManifestStructureError("Manifest de extensão incompleto")
    _validate_manifest_identity(payload)
    return ExtensionManifest(
        id=payload["id"],
        version=payload["version"],
        protocol_version=payload["protocol_version"],
        transport=payload["transport"],
        entrypoint=_parse_entrypoint(payload),
        timeout_seconds=_parse_timeout(payload),
        tools=_parse_tools(payload, payload["transport"]),
    )


__all__ = [
    "ExtensionManifest",
    "ManifestParseError",
    "ManifestParserMode",
    "ManifestProtocolError",
    "ManifestStructureError",
    "SUPPORTED_PROTOCOL",
    "load_extension_manifest_bytes",
    "validate_transport_capabilities",
]
