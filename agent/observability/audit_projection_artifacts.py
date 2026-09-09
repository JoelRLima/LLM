"""Metadata-only artifact projection helpers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent.observability.audit_projection_fields import (
    MAX_AUDIT_TEXT,
    _bounded_text,
    _safe_relative_path,
)

_SAFE_ARTIFACT_KEYS = frozenset(
    {"name", "format", "media_type", "mime_type", "source", "provenance", "version"}
)
_STABLE_ARTIFACT_KEYS = frozenset(
    {"artifact_id", "id", "digest", "sha256", "content_hash", "manifest_sha256", "change_set_id"}
)


def _artifact_objects(result: Any) -> tuple[Any, ...]:
    values = getattr(result, "artifacts", None)
    if isinstance(values, (list, tuple)):
        return tuple(values)
    data = getattr(result, "data", None)
    nested = data.get("artifacts") if isinstance(data, Mapping) else None
    return tuple(nested) if isinstance(nested, (list, tuple)) else ()


def _artifact_metadata(artifact: Any) -> Mapping[str, Any]:
    metadata = getattr(artifact, "metadata", None)
    if isinstance(metadata, Mapping):
        return metadata
    if isinstance(artifact, Mapping):
        nested = artifact.get("metadata")
        return nested if isinstance(nested, Mapping) else artifact
    return {}


def _artifact_value(artifact: Any, metadata: Mapping[str, Any], key: str) -> Any:
    value = getattr(artifact, key, None)
    if value is not None:
        return value
    if isinstance(artifact, Mapping) and key in artifact:
        return artifact.get(key)
    return metadata.get(key)


def _metadata_fields(metadata: Mapping[str, Any], keys: frozenset[str]) -> dict[str, str]:
    fields: dict[str, str] = {}
    for key in sorted(keys):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            fields[key] = value.strip()[:MAX_AUDIT_TEXT]
    return fields


def _project_one_artifact(
    artifact: Any,
    workspace_root: str | Any,
) -> dict[str, Any] | None:
    metadata = _artifact_metadata(artifact)
    projected: dict[str, Any] = {}
    kind = _bounded_text(_artifact_value(artifact, metadata, "kind"))
    if kind is not None:
        projected["kind"] = kind
    path = _artifact_value(artifact, metadata, "path") or metadata.get("workspace_relative_path")
    if path is not None:
        projected["workspace_relative_path"] = _safe_relative_path(path, workspace_root)
    stable = _metadata_fields(metadata, _STABLE_ARTIFACT_KEYS)
    if stable:
        projected.update(stable)
    safe = _metadata_fields(metadata, _SAFE_ARTIFACT_KEYS)
    if safe:
        projected["metadata"] = safe
    return projected or None


__all__ = ["_artifact_objects", "_project_one_artifact"]
