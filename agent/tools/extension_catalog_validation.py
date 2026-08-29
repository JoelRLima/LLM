"""Manifest drift validation and safe observations for catalog entries."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from agent.tools.extension_catalog_document import ExtensionCatalogDocument, PersistedCatalogEntry
from agent.tools.extension_catalog_errors import CatalogDiagnostic
from agent.tools.extension_manifest_parser import (
    ManifestParseError,
    ManifestProtocolError,
    load_extension_manifest_bytes,
)
from agent.tools.extension_state import (
    fingerprint_for_bytes,
    validate_capability_id,
    validate_extension_id,
    validate_manifest_fingerprint,
)

HostFlavor = Literal["windows", "posix"]


@dataclass(frozen=True)
class ManifestSummary:
    """Safe manifest facts required by workspace resolution."""

    required_capabilities: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        try:
            capabilities = tuple(self.required_capabilities)
        except TypeError as exc:
            raise TypeError("required_capabilities deve ser iterável") from exc
        try:
            canonical = tuple(sorted({validate_capability_id(item) for item in capabilities}))
        except (TypeError, ValueError) as exc:
            raise ValueError("required_capabilities inválidas") from exc
        object.__setattr__(self, "required_capabilities", canonical)


@dataclass(frozen=True)
class ManifestObservation:
    """One immutable observation without manifest contents or descriptions."""

    extension_id: str
    manifest_status: str
    observed_fingerprint: str | None
    manifest_summary: ManifestSummary

    def __post_init__(self) -> None:
        try:
            validate_extension_id(self.extension_id)
        except (TypeError, ValueError) as exc:
            raise ValueError("extension_id inválido na observação") from exc
        if type(self.manifest_status) is not str or self.manifest_status not in {
            "changed",
            "incompatible",
            "invalid",
            "missing",
            "unchanged",
        }:
            raise ValueError("status inválido na observação")
        if not isinstance(self.manifest_summary, ManifestSummary):
            raise TypeError("manifest_summary inválido na observação")
        if self.observed_fingerprint is not None:
            try:
                validate_manifest_fingerprint(self.observed_fingerprint)
            except (TypeError, ValueError) as exc:
                raise ValueError("fingerprint inválido na observação") from exc
        readable = self.manifest_status in {"changed", "unchanged"}
        if readable != (self.observed_fingerprint is not None):
            raise ValueError("fingerprint incoerente com o status da observação")
        if not readable and self.manifest_summary.required_capabilities:
            raise ValueError("summary incoerente com o status da observação")


def _observation(
    entry: PersistedCatalogEntry,
    status: str,
    fingerprint: str | None = None,
    capabilities: tuple[str, ...] = (),
) -> ManifestObservation:
    return ManifestObservation(
        extension_id=entry.extension_id,
        manifest_status=status,
        observed_fingerprint=fingerprint,
        manifest_summary=ManifestSummary(capabilities),
    )


def _pair(
    entry: PersistedCatalogEntry,
    diagnostic: CatalogDiagnostic,
    *,
    status: str,
    fingerprint: str | None = None,
    capabilities: tuple[str, ...] = (),
) -> tuple[ManifestObservation, CatalogDiagnostic]:
    return _observation(entry, status, fingerprint, capabilities), diagnostic


def _observe_entry(
    entry: PersistedCatalogEntry, host_flavor: HostFlavor
) -> tuple[ManifestObservation, CatalogDiagnostic]:
    path = entry.manifest_path
    if not path.is_compatible_with(host_flavor):
        return _pair(
            entry,
            CatalogDiagnostic(
                entry.extension_id,
                path.persisted_value,
                path.flavor,
                "incompatible",
                "FOREIGN_FLAVOR",
                "manifest flavor incompatível com o host",
                entry.manifest_sha256,
            ),
            status="incompatible",
        )
    native = Path(path.persisted_value)
    if not native.is_file():
        return _pair(
            entry,
            CatalogDiagnostic(
                entry.extension_id,
                path.persisted_value,
                path.flavor,
                "missing",
                "MANIFEST_MISSING",
                "manifest ausente ou alvo de symlink indisponível",
                entry.manifest_sha256,
            ),
            status="missing",
        )
    try:
        content = native.read_bytes()
        manifest = load_extension_manifest_bytes(content, mode="strict_catalog")
    except OSError:
        return _pair(
            entry,
            CatalogDiagnostic(
                entry.extension_id,
                path.persisted_value,
                path.flavor,
                "missing",
                "MANIFEST_UNAVAILABLE",
                "manifest indisponível para leitura",
                entry.manifest_sha256,
            ),
            status="missing",
        )
    except ManifestProtocolError:
        return _pair(
            entry,
            CatalogDiagnostic(
                entry.extension_id,
                path.persisted_value,
                path.flavor,
                "incompatible",
                "MANIFEST_PROTOCOL_INCOMPATIBLE",
                "protocolo do manifest não é suportado",
                entry.manifest_sha256,
            ),
            status="incompatible",
        )
    except (ManifestParseError, TypeError, ValueError):
        return _pair(
            entry,
            CatalogDiagnostic(
                entry.extension_id,
                path.persisted_value,
                path.flavor,
                "invalid",
                "MANIFEST_INVALID",
                "manifest inválido",
                entry.manifest_sha256,
            ),
            status="invalid",
        )
    required_capabilities = tuple(
        sorted(
            {
                capability
                for tool in manifest.tools
                for capability in tool.get("capabilities", [])
            }
        )
    )
    if manifest.id != entry.extension_id:
        return _pair(
            entry,
            CatalogDiagnostic(
                entry.extension_id,
                path.persisted_value,
                path.flavor,
                "incompatible",
                "MANIFEST_ID_MISMATCH",
                "ID do manifest difere da extensão registrada",
                entry.manifest_sha256,
            ),
            status="incompatible",
            capabilities=(),
        )
    observed = fingerprint_for_bytes(content)
    if observed != entry.manifest_sha256:
        return _pair(
            entry,
            CatalogDiagnostic(
                entry.extension_id,
                path.persisted_value,
                path.flavor,
                "changed",
                "MANIFEST_DRIFT",
                "fingerprint do manifest mudou",
                entry.manifest_sha256,
                observed,
            ),
            status="changed",
            fingerprint=observed,
            capabilities=required_capabilities,
        )
    return _pair(
        entry,
        CatalogDiagnostic(
            entry.extension_id,
            path.persisted_value,
            path.flavor,
            "unchanged",
            "MANIFEST_UNCHANGED",
            "manifest permanece igual",
            entry.manifest_sha256,
            observed,
        ),
        status="unchanged",
        fingerprint=observed,
        capabilities=required_capabilities,
    )


def observe_catalog_document(
    document: ExtensionCatalogDocument,
    host_flavor: HostFlavor,
) -> tuple[tuple[ManifestObservation, CatalogDiagnostic], ...]:
    """Read and summarize each manifest exactly once."""

    return tuple(_observe_entry(entry, host_flavor) for entry in document.entries)


def validate_catalog_document(
    document: ExtensionCatalogDocument,
    host_flavor: HostFlavor,
) -> tuple[CatalogDiagnostic, ...]:
    """Preserve the diagnostic-only catalog API."""

    return tuple(diagnostic for _, diagnostic in observe_catalog_document(document, host_flavor))


__all__ = [
    "ManifestObservation",
    "ManifestSummary",
    "observe_catalog_document",
    "validate_catalog_document",
]
