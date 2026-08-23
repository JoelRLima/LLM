"""Neutral completeness semantics for canonical tool results."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import Enum
from typing import Any


class EvidenceProvenance(str, Enum):
    """Canonical provenance of the value represented by a tool result.

    These values describe the source of ``result.data``.  They deliberately
    do not describe whether an invocation succeeded: a successful invocation
    may still return a bounded or lossy projection.
    """

    EXACT_SOURCE = "EXACT_SOURCE"
    BOUNDED_SOURCE = "BOUNDED_SOURCE"
    DERIVED_LOSSY = "DERIVED_LOSSY"
    UNKNOWN = "UNKNOWN"


_PROVENANCE_KEYS = ("evidence_provenance", "provenance")
_PROVENANCE_ALIASES = {
    "EXACT_SOURCE": EvidenceProvenance.EXACT_SOURCE,
    "EXACTSOURCE": EvidenceProvenance.EXACT_SOURCE,
    "BOUNDED_SOURCE": EvidenceProvenance.BOUNDED_SOURCE,
    "BOUNDEDSOURCE": EvidenceProvenance.BOUNDED_SOURCE,
    "DERIVED_LOSSY": EvidenceProvenance.DERIVED_LOSSY,
    "DERIVEDLOSSY": EvidenceProvenance.DERIVED_LOSSY,
    "UNKNOWN": EvidenceProvenance.UNKNOWN,
}


def _artifact_metadata(result: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    values: list[Mapping[str, Any]] = []
    containers: list[Mapping[str, Any]] = [result]
    data = result.get("data")
    if isinstance(data, Mapping):
        containers.append(data)
    for container in containers:
        artifacts = container.get("artifacts")
        if not isinstance(artifacts, Sequence) or isinstance(
            artifacts, (str, bytes, bytearray)
        ):
            continue
        values.extend(
            item["metadata"]
            for item in artifacts
            if isinstance(item, Mapping) and isinstance(item.get("metadata"), Mapping)
        )
    metadata = result.get("metadata")
    if isinstance(metadata, Mapping):
        values.append(metadata)
    return tuple(values)


def _normalize_provenance(value: Any) -> EvidenceProvenance | None:
    if isinstance(value, EvidenceProvenance):
        return value
    if not isinstance(value, str):
        return None
    token = value.strip().upper().replace("-", "_").replace(" ", "_")
    return _PROVENANCE_ALIASES.get(token)


def canonical_evidence_provenance(result: Mapping[str, Any]) -> EvidenceProvenance:
    """Return one canonical provenance classification for ``result.data``.

    Conflicting declarations are treated as ``UNKNOWN``.  A missing
    declaration is also ``UNKNOWN``; callers that support legacy synthetic
    result fixtures can use :func:`is_legacy_complete_result` explicitly
    rather than silently treating success as source fidelity.
    """

    values: list[EvidenceProvenance] = []
    for key in _PROVENANCE_KEYS:
        normalized = _normalize_provenance(result.get(key))
        if normalized is not None:
            values.append(normalized)
    for metadata in _artifact_metadata(result):
        for key in _PROVENANCE_KEYS:
            normalized = _normalize_provenance(metadata.get(key))
            if normalized is not None:
                values.append(normalized)
    if not values or len(set(values)) != 1:
        return EvidenceProvenance.UNKNOWN
    return values[0]


def has_explicit_evidence_provenance(result: Mapping[str, Any]) -> bool:
    """Whether the result carries a recognized provenance declaration."""

    if any(_normalize_provenance(result.get(key)) is not None for key in _PROVENANCE_KEYS):
        return True
    return any(
        any(_normalize_provenance(metadata.get(key)) is not None for key in _PROVENANCE_KEYS)
        for metadata in _artifact_metadata(result)
    )


def source_extent(result: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """Return the bounded source extent declared by a canonical result."""

    containers: list[Mapping[str, Any]] = [result]
    containers.extend(_artifact_metadata(result))
    for container in containers:
        for key in ("source_extent", "represented_extent"):
            extent = container.get(key)
            if isinstance(extent, Mapping):
                return extent
    return None


def source_identity_bound(result: Mapping[str, Any]) -> bool:
    """Whether the result binds its value to a concrete source identity."""

    containers: list[Mapping[str, Any]] = [result]
    containers.extend(_artifact_metadata(result))
    for container in containers:
        identity = container.get("source_identity")
        source_hash = container.get("source_hash")
        if isinstance(identity, str) and identity.strip() and isinstance(source_hash, str) and source_hash.strip():
            return True
    return False


def is_legacy_complete_result(result: Mapping[str, Any]) -> bool:
    """Compatibility predicate for old in-memory test/admin result shapes.

    This is intentionally narrower than success: an explicit ``complete``
    declaration is required and no untrusted provenance declaration may be
    present.  Production source tools now always emit explicit provenance.
    """

    return not has_explicit_evidence_provenance(result) and canonical_completeness(result)[0]


def exact_source_covers_whole_result(result: Mapping[str, Any]) -> bool:
    """Whether result data can prove an exact whole-source observation."""

    complete, truncated = canonical_completeness(result)
    if not complete or truncated:
        return False
    provenance = canonical_evidence_provenance(result)
    if provenance is EvidenceProvenance.EXACT_SOURCE:
        extent = source_extent(result)
        return source_identity_bound(result) and (
            extent is None or extent.get("kind") == "whole"
        )
    return is_legacy_complete_result(result)


def canonical_completeness(result: Mapping[str, Any]) -> tuple[bool, bool]:
    """Return ``(complete, truncated)`` from explicit result/artifact metadata."""

    truncated = result.get("truncated") is True
    explicit_complete = result.get("complete")
    complete = explicit_complete if type(explicit_complete) is bool else not truncated
    containers: list[Mapping[str, Any]] = [result]
    data = result.get("data")
    if isinstance(data, Mapping):
        containers.append(data)
    metadata = result.get("metadata")
    if isinstance(metadata, Mapping):
        containers.append({"artifacts": [{"metadata": metadata}]})
    for container in containers:
        artifacts = container.get("artifacts")
        if not isinstance(artifacts, Sequence) or isinstance(artifacts, (str, bytes, bytearray)):
            continue
        for artifact in artifacts:
            artifact_metadata = artifact.get("metadata") if isinstance(artifact, Mapping) else None
            if not isinstance(artifact_metadata, Mapping):
                continue
            if type(artifact_metadata.get("complete")) is bool:
                complete = complete and artifact_metadata["complete"]
            if artifact_metadata.get("truncated") is True:
                complete, truncated = False, True
    # A lossy or explicitly unknown representation cannot become complete
    # merely because a producer omitted/forged the low-level completeness bit.
    provenance = canonical_evidence_provenance(result)
    if has_explicit_evidence_provenance(result) and provenance in {
        EvidenceProvenance.DERIVED_LOSSY,
        EvidenceProvenance.UNKNOWN,
    }:
        complete = False
    return bool(complete and not truncated), truncated


__all__ = [
    "EvidenceProvenance",
    "canonical_completeness",
    "canonical_evidence_provenance",
    "exact_source_covers_whole_result",
    "has_explicit_evidence_provenance",
    "is_legacy_complete_result",
    "source_extent",
    "source_identity_bound",
]
