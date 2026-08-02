"""Strict deterministic codec for workspace-local extension configuration."""

from __future__ import annotations

import json
from typing import Any

from agent.tools.extension_catalog_errors import (
    WorkspaceCodecError,
    WorkspaceSchemaError,
    WorkspaceVersionError,
)
from agent.tools.extension_state import (
    WORKSPACE_SCHEMA_VERSION,
    WorkspaceExtensionSelection,
    WorkspaceExtensionsState,
    validate_extension_id,
    validate_schema_version,
)

_ROOT_FIELDS = frozenset(("schema_version", "extensions"))
_ENTRY_FIELDS = frozenset(("enabled", "grants"))


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise WorkspaceSchemaError("O documento contém uma chave JSON duplicada.")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise WorkspaceSchemaError("A configuração contém uma constante JSON inválida.")


def _exact_fields(payload: dict[str, Any], expected: frozenset[str], context: str) -> None:
    missing = sorted(expected - set(payload))
    unknown = sorted(set(payload) - expected)
    if missing:
        raise WorkspaceSchemaError("A configuração contém campos obrigatórios ausentes.")
    if unknown:
        raise WorkspaceSchemaError("A configuração contém campos desconhecidos.")


def _decode_json(data: bytes) -> object:
    if not isinstance(data, bytes):
        raise TypeError("workspace configuration bytes deve ser bytes")
    if not data:
        raise WorkspaceCodecError("Configuração do workspace vazia")
    if data.startswith(b"\xef\xbb\xbf"):
        raise WorkspaceCodecError("BOM UTF-8 não é aceito na configuração do workspace")
    try:
        return json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except WorkspaceCodecError:
        raise
    except UnicodeDecodeError as exc:
        raise WorkspaceCodecError("Configuração não está codificada em UTF-8") from exc
    except (json.JSONDecodeError, TypeError) as exc:
        raise WorkspaceCodecError("JSON de configuração inválido") from exc


def _decode_selection(extension_id: str, raw: object) -> WorkspaceExtensionSelection:
    if not isinstance(raw, dict):
        raise WorkspaceSchemaError("A configuração contém uma entrada de extension inválida.")
    _exact_fields(raw, _ENTRY_FIELDS, f"extension {extension_id}")
    if type(raw["enabled"]) is not bool:
        raise WorkspaceSchemaError("A configuração contém um valor enabled inválido.")
    grants = raw["grants"]
    if not isinstance(grants, list) or any(type(item) is not str for item in grants):
        raise WorkspaceSchemaError("A configuração contém grants inválidos.")
    try:
        return WorkspaceExtensionSelection(extension_id, tuple(grants), raw["enabled"])
    except (TypeError, ValueError) as exc:
        raise WorkspaceSchemaError("A configuração contém uma entrada de extension inválida.") from exc


def decode_workspace_extensions(data: bytes) -> WorkspaceExtensionsState:
    """Decode strict UTF-8 bytes into an immutable workspace snapshot."""

    payload = _decode_json(data)
    if not isinstance(payload, dict):
        raise WorkspaceSchemaError("A raiz da configuração deve ser um objeto.")
    _exact_fields(payload, _ROOT_FIELDS, "configuração do workspace")
    try:
        validate_schema_version(payload["schema_version"], WORKSPACE_SCHEMA_VERSION)
    except ValueError as exc:
        raise WorkspaceVersionError(str(exc)) from exc
    extensions = payload["extensions"]
    if not isinstance(extensions, dict):
        raise WorkspaceSchemaError("A configuração de extensions deve ser um objeto.")
    selections: list[WorkspaceExtensionSelection] = []
    for extension_id, raw in extensions.items():
        if not isinstance(extension_id, str):
            raise WorkspaceSchemaError("A configuração contém uma chave de extension inválida.")
        try:
            validate_extension_id(extension_id)
        except ValueError as exc:
            raise WorkspaceSchemaError("A configuração contém uma chave de extension inválida.") from exc
        selections.append(_decode_selection(extension_id, raw))
    try:
        return WorkspaceExtensionsState(tuple(selections))
    except (TypeError, ValueError) as exc:
        raise WorkspaceSchemaError("A configuração contém entradas inválidas.") from exc


def encode_workspace_extensions(state: WorkspaceExtensionsState) -> bytes:
    """Encode a workspace snapshot as deterministic UTF-8 JSON bytes."""

    if not isinstance(state, WorkspaceExtensionsState):
        raise TypeError("state deve ser WorkspaceExtensionsState")
    payload = {
        "schema_version": WORKSPACE_SCHEMA_VERSION,
        "extensions": {
            selection.extension_id: {
                "enabled": selection.enabled,
                "grants": list(selection.granted_capabilities),
            }
            for selection in state.selections
        },
    }
    try:
        return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
            "utf-8"
        )
    except (TypeError, ValueError) as exc:
        raise WorkspaceCodecError("Configuração não pôde ser codificada") from exc


__all__ = ["decode_workspace_extensions", "encode_workspace_extensions"]
