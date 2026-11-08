"""Manifest models and compatibility loaders for stdio extensions."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Tuple

from llm_agent.agent.operation.schema import FrozenJsonObject
from llm_agent.extensions.extension_manifest_parser import (
    ManifestParseError,
    ManifestParserMode,
    ManifestProtocolError,
    ManifestStructureError,
    load_extension_manifest_bytes,
)


def _strict_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _strict_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Non-finite JSON number")
    return number


def _reject_constant(_value: str) -> Any:
    raise ValueError("Non-finite JSON constant")


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


def _load_extension_manifest(path: str | Path, *, mode: ManifestParserMode) -> ExtensionManifest:
    manifest_path = Path(path).expanduser().resolve()
    try:
        parsed = load_extension_manifest_bytes(
            manifest_path.read_bytes(),
            mode=mode,
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


def load_strict_extension_manifest(path: str | Path) -> ExtensionManifest:
    """Read a manifest for registry/bootstrap use with strict catalog rules."""

    return _load_extension_manifest(path, mode="strict_catalog")
