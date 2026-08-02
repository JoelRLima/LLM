"""Runtime materialization for already-resolved workspace extensions."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from agent.tools.contracts import ToolAdapter, ToolDescriptor, freeze_json_like, thaw_json_like
from agent.tools.extension_manifest_parser import ExtensionManifest as ParsedExtensionManifest
from agent.tools.extension_manifest_parser import (
    ManifestParseError,
    ManifestProtocolError,
    ManifestStructureError,
    load_extension_manifest_bytes,
)
from agent.tools.extension_path import PathFlavor
from agent.tools.extension_state import fingerprint_for_bytes
from agent.tools.stdio_adapter import ExtensionManifest, StdioToolAdapter
from agent.tools.workspace_extensions_resolver import (
    ResolvedWorkspaceExtension,
    ResolvedWorkspaceExtensions,
)

_KNOWN_PLACEHOLDERS = frozenset(("extension_dir", "python"))


class _UnknownPlaceholderError(ValueError):
    """Raised when an entrypoint contains an unsupported placeholder."""


class _InvalidPlaceholderError(ValueError):
    """Raised when a placeholder start is not a complete token."""


@dataclass(frozen=True)
class ExtensionRuntimeDiagnostic:
    """Safe, immutable diagnostic emitted during runtime materialization."""

    extension_id: str | None
    code: str
    severity: str
    safe_message: str
    tool_name: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return {
            "extension_id": self.extension_id,
            "code": self.code,
            "severity": self.severity,
            "message": self.safe_message,
            "tool_name": self.tool_name,
        }


@dataclass(frozen=True)
class ExtensionRuntimeBinding:
    """Immutable runtime snapshot for one extension and all its tools."""

    extension_id: str
    approved_fingerprint: str
    adapter: ToolAdapter
    descriptors: tuple[ToolDescriptor, ...]
    metadata: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "descriptors", tuple(self.descriptors))
        object.__setattr__(self, "metadata", freeze_json_like(dict(self.metadata)))

    def __getattribute__(self, name: str) -> object:
        if name == "metadata":
            snapshot = object.__getattribute__(self, "metadata")
            return thaw_json_like(snapshot)
        return object.__getattribute__(self, name)


@dataclass(frozen=True)
class ExtensionRuntimeMaterialization:
    """Complete deterministic result of materializing a resolution snapshot."""

    bindings: tuple[ExtensionRuntimeBinding, ...] = ()
    diagnostics: tuple[ExtensionRuntimeDiagnostic, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "bindings",
            tuple(sorted(self.bindings, key=lambda binding: binding.extension_id)),
        )
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics))


class ExtensionRuntimeMaterializer:
    """Turn ready resolution entries into side-effect-free runtime bindings."""

    def __init__(self, workspace_root: str | Path, *, host_flavor: PathFlavor) -> None:
        self.workspace_root = Path(workspace_root).absolute()
        self.host_flavor = host_flavor

    def materialize(
        self, resolved: ResolvedWorkspaceExtensions
    ) -> ExtensionRuntimeMaterialization:
        bindings: list[ExtensionRuntimeBinding] = []
        diagnostics: list[ExtensionRuntimeDiagnostic] = []
        for entry in resolved.entries:
            if entry.activation_status != "ready":
                diagnostics.append(
                    self._diagnostic(entry.extension_id, "EXTENSION_RUNTIME_NOT_ELIGIBLE")
                )
                continue
            binding, diagnostic = self._materialize_entry(entry)
            if binding is not None:
                bindings.append(binding)
            if diagnostic is not None:
                diagnostics.append(diagnostic)
        return ExtensionRuntimeMaterialization(tuple(bindings), tuple(diagnostics))

    def _materialize_entry(
        self, entry: ResolvedWorkspaceExtension
    ) -> tuple[ExtensionRuntimeBinding | None, ExtensionRuntimeDiagnostic | None]:
        catalog_entry = entry.catalog_entry
        if catalog_entry is None:
            return None, self._diagnostic(entry.extension_id, "EXTENSION_RUNTIME_MATERIALIZATION_FAILED")
        manifest_path = catalog_entry.manifest_path
        if not manifest_path.is_compatible_with(self.host_flavor):
            return None, self._diagnostic(
                entry.extension_id, "EXTENSION_RUNTIME_PROTOCOL_INCOMPATIBLE"
            )
        native_path = Path(manifest_path.persisted_value)
        content, diagnostic = self._read_manifest(entry, native_path)
        if diagnostic is not None:
            return None, diagnostic
        parsed, diagnostic = self._parse_manifest(entry, content)
        if diagnostic is not None or parsed is None:
            return None, diagnostic
        try:
            entrypoint = self._expand_entrypoint(parsed.entrypoint, native_path.parent)
        except _UnknownPlaceholderError:
            return None, self._diagnostic(
                entry.extension_id, "EXTENSION_RUNTIME_PLACEHOLDER_UNKNOWN"
            )
        except _InvalidPlaceholderError:
            return None, self._diagnostic(
                entry.extension_id, "EXTENSION_RUNTIME_PLACEHOLDER_INVALID"
            )
        return self._build_binding(entry, parsed, entrypoint)

    def _read_manifest(
        self, entry: ResolvedWorkspaceExtension, native_path: Path
    ) -> tuple[bytes | None, ExtensionRuntimeDiagnostic | None]:
        try:
            content = native_path.read_bytes()
        except FileNotFoundError:
            return None, self._diagnostic(entry.extension_id, "EXTENSION_RUNTIME_MANIFEST_MISSING")
        except OSError:
            return None, self._diagnostic(entry.extension_id, "EXTENSION_RUNTIME_MATERIALIZATION_FAILED")
        catalog_entry = entry.catalog_entry
        if catalog_entry is None:
            return None, self._diagnostic(entry.extension_id, "EXTENSION_RUNTIME_MATERIALIZATION_FAILED")
        if fingerprint_for_bytes(content) != catalog_entry.manifest_sha256:
            return None, self._diagnostic(entry.extension_id, "EXTENSION_RUNTIME_FINGERPRINT_MISMATCH")
        return content, None

    def _parse_manifest(
        self, entry: ResolvedWorkspaceExtension, content: bytes | None
    ) -> tuple[ParsedExtensionManifest | None, ExtensionRuntimeDiagnostic | None]:
        if content is None:
            return None, self._diagnostic(entry.extension_id, "EXTENSION_RUNTIME_MATERIALIZATION_FAILED")
        try:
            parsed = load_extension_manifest_bytes(content, mode="strict_catalog")
        except ManifestProtocolError:
            return None, self._diagnostic(entry.extension_id, "EXTENSION_RUNTIME_PROTOCOL_INCOMPATIBLE")
        except (ManifestParseError, ManifestStructureError, TypeError, ValueError):
            return None, self._diagnostic(entry.extension_id, "EXTENSION_RUNTIME_MANIFEST_INVALID")
        if parsed.id != entry.extension_id:
            return None, self._diagnostic(entry.extension_id, "EXTENSION_RUNTIME_ID_MISMATCH")
        if parsed.protocol_version != "1.0":
            return None, self._diagnostic(entry.extension_id, "EXTENSION_RUNTIME_PROTOCOL_INCOMPATIBLE")
        required_capabilities = tuple(
            sorted({capability for tool in parsed.tools for capability in tool.get("capabilities", [])})
        )
        if required_capabilities != entry.required_capabilities:
            return None, self._diagnostic(entry.extension_id, "EXTENSION_RUNTIME_MANIFEST_CHANGED")
        return parsed, None

    def _build_binding(
        self, entry: ResolvedWorkspaceExtension, parsed: ParsedExtensionManifest, entrypoint: tuple[str, ...]
    ) -> tuple[ExtensionRuntimeBinding | None, ExtensionRuntimeDiagnostic | None]:
        catalog_entry = entry.catalog_entry
        if catalog_entry is None:
            return None, self._diagnostic(entry.extension_id, "EXTENSION_RUNTIME_MATERIALIZATION_FAILED")
        manifest = ExtensionManifest(
            id=parsed.id,
            version=parsed.version,
            protocol_version=parsed.protocol_version,
            transport=parsed.transport,
            entrypoint=entrypoint,
            timeout_seconds=parsed.timeout_seconds,
            tools=parsed.tools,
        )
        try:
            adapter = StdioToolAdapter(manifest, cwd=self.workspace_root)
            descriptors = tuple(adapter.descriptors())
        except (TypeError, ValueError):
            return None, self._diagnostic(
                entry.extension_id, "EXTENSION_RUNTIME_DESCRIPTOR_INVALID"
            )
        if not descriptors:
            return None, self._diagnostic(
                entry.extension_id, "EXTENSION_RUNTIME_DESCRIPTOR_INVALID"
            )
        return (
            ExtensionRuntimeBinding(
                extension_id=entry.extension_id,
                approved_fingerprint=catalog_entry.manifest_sha256,
                adapter=adapter,
                descriptors=descriptors,
                metadata={
                    "protocol_version": parsed.protocol_version,
                    "manifest_version": parsed.version,
                    "cwd": str(self.workspace_root),
                },
            ),
            None,
        )

    @staticmethod
    def _expand_entrypoint(entrypoint: tuple[str, ...], extension_dir: Path) -> tuple[str, ...]:
        replacements = {
            "extension_dir": str(extension_dir.absolute()),
            "python": sys.executable,
        }
        expanded: list[str] = []
        for argument in entrypoint:
            cursor = 0
            parts: list[str] = []
            while True:
                start = argument.find("${", cursor)
                if start < 0:
                    parts.append(argument[cursor:])
                    break
                parts.append(argument[cursor:start])
                end = argument.find("}", start + 2)
                if end < 0:
                    raise _InvalidPlaceholderError("placeholder incompleto")
                name = argument[start + 2 : end]
                if not name or "{" in name or "}" in name:
                    raise _InvalidPlaceholderError("placeholder inválido")
                if name not in _KNOWN_PLACEHOLDERS:
                    raise _UnknownPlaceholderError("placeholder desconhecido")
                parts.append(replacements[name])
                cursor = end + 1
            expanded.append("".join(parts))
        return tuple(expanded)

    @staticmethod
    def _diagnostic(extension_id: str, code: str) -> ExtensionRuntimeDiagnostic:
        messages = {
            "EXTENSION_RUNTIME_MANIFEST_CHANGED": "Manifest mudou entre resolução e materialização.",
            "EXTENSION_RUNTIME_MANIFEST_MISSING": "Manifest não está disponível.",
            "EXTENSION_RUNTIME_MANIFEST_INVALID": "Manifest é inválido.",
            "EXTENSION_RUNTIME_PROTOCOL_INCOMPATIBLE": "Protocolo do manifest não é compatível.",
            "EXTENSION_RUNTIME_ID_MISMATCH": "ID do manifest não corresponde à extensão registrada.",
            "EXTENSION_RUNTIME_FINGERPRINT_MISMATCH": "Fingerprint do manifest não corresponde ao catálogo.",
            "EXTENSION_RUNTIME_PLACEHOLDER_UNKNOWN": "Entrypoint contém placeholder desconhecido.",
            "EXTENSION_RUNTIME_PLACEHOLDER_INVALID": "Entrypoint contém placeholder inválido.",
            "EXTENSION_RUNTIME_DESCRIPTOR_INVALID": "Descriptor de tool é inválido.",
            "EXTENSION_RUNTIME_COLLISION": "Extensão rejeitada por colisão de nome de tool.",
            "EXTENSION_RUNTIME_MATERIALIZATION_FAILED": "Extensão não pôde ser materializada.",
            "EXTENSION_RUNTIME_NOT_ELIGIBLE": "Extensão não está elegível para materialização.",
        }
        return ExtensionRuntimeDiagnostic(
            extension_id=extension_id,
            code=code,
            severity="error",
            safe_message=messages.get(code, "Falha na materialização da extensão."),
        )


__all__ = [
    "ExtensionRuntimeBinding",
    "ExtensionRuntimeDiagnostic",
    "ExtensionRuntimeMaterialization",
    "ExtensionRuntimeMaterializer",
]
