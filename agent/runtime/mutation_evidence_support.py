"""Structured metadata readers for mutation-evidence projection."""

from __future__ import annotations

import posixpath
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


def _as_mapping(result: Any) -> Mapping[str, Any]:
    if isinstance(result, Mapping):
        return result
    to_legacy = getattr(result, "to_legacy_dict", None)
    if callable(to_legacy):
        projected = to_legacy(include_details=True)
        return projected if isinstance(projected, Mapping) else {}
    return {}


def _metadata(result: Any) -> tuple[Mapping[str, Any], ...]:
    object_artifacts = getattr(result, "artifacts", None)
    object_records = (
        tuple(
            artifact.metadata
            for artifact in object_artifacts
            if isinstance(getattr(artifact, "metadata", None), Mapping)
        )
        if isinstance(object_artifacts, (list, tuple))
        else ()
    )
    object_metadata = getattr(result, "metadata", None)
    if isinstance(object_metadata, Mapping):
        object_records = (*object_records, object_metadata)
    value = _as_mapping(result)
    records: list[Mapping[str, Any]] = list(object_records)
    _append_artifact_metadata(records, value)
    data = value.get("data")
    _append_artifact_metadata(records, data)
    _append_nested_metadata(records, value, data)
    mutation_keys = {
        "affected_files", "attempted_files", "mutated_files", "surviving_files",
        "affected_resources", "mutated_resources", "surviving_resources", "mutation_attempted",
        "mutation_occurred", "persisted_mutation", "surviving_mutation", "rollback_occurred",
        "applied", "final_state", "validation", "validation_status",
    }
    for candidate in (value, data):
        if isinstance(candidate, Mapping) and any(key in candidate for key in mutation_keys):
            records.append(candidate)
    return tuple(records)


def _append_artifact_metadata(records: list[Mapping[str, Any]], candidate: Any) -> None:
    if not isinstance(candidate, Mapping):
        return
    artifacts = candidate.get("artifacts")
    if not isinstance(artifacts, Sequence) or isinstance(artifacts, (str, bytes, bytearray)):
        return
    for artifact in artifacts:
        if isinstance(artifact, Mapping) and isinstance(artifact.get("metadata"), Mapping):
            records.append(artifact["metadata"])


def _append_nested_metadata(
    records: list[Mapping[str, Any]], value: Mapping[str, Any], data: Any,
) -> None:
    for candidate in (value, data):
        if isinstance(candidate, Mapping) and isinstance(candidate.get("metadata"), Mapping):
            records.append(candidate["metadata"])


def _normalize_path(value: Any) -> str:
    return posixpath.normpath(str(value).replace("\\", "/").strip())


def artifact_metadata(result: Any) -> tuple[dict[str, Any], ...]:
    """Return structured artifact metadata without interpreting prose."""

    records: list[dict[str, Any]] = []
    object_artifacts = getattr(result, "artifacts", None)
    if isinstance(object_artifacts, (list, tuple)):
        records.extend(
            dict(artifact.metadata)
            for artifact in object_artifacts
            if isinstance(getattr(artifact, "metadata", None), Mapping)
        )
    object_metadata = getattr(result, "metadata", None)
    if isinstance(object_metadata, Mapping):
        records.append(dict(object_metadata))
    value = _as_mapping(result)
    _append_artifact_dicts(records, value)
    data = value.get("data")
    _append_artifact_dicts(records, data)
    for candidate in (value, data):
        if isinstance(candidate, Mapping) and isinstance(candidate.get("metadata"), Mapping):
            records.append(dict(candidate["metadata"]))
    return tuple(records)


def _append_artifact_dicts(records: list[dict[str, Any]], candidate: Any) -> None:
    if not isinstance(candidate, Mapping):
        return
    artifacts = candidate.get("artifacts")
    if not isinstance(artifacts, Sequence) or isinstance(artifacts, (str, bytes, bytearray)):
        return
    records.extend(
        dict(artifact["metadata"])
        for artifact in artifacts
        if isinstance(artifact, Mapping) and isinstance(artifact.get("metadata"), Mapping)
    )


def metadata_is_persisted_mutation(metadata: Mapping[str, Any]) -> bool:
    if metadata.get("final_state") == "unknown" or metadata.get("mutation_occurred") is False:
        return False
    return (
        metadata.get("persisted_mutation") is True
        and metadata.get("rollback_occurred") is not True
    ) or (
        metadata.get("applied") is True
        and metadata.get("mutation_occurred") is True
        and metadata.get("rollback_occurred") is not True
        and metadata.get("final_state") == "applied"
    ) or (
        metadata.get("surviving_mutation") is True
        and metadata.get("rollback_occurred") is not True
        and metadata.get("mutation_occurred") is not False
    )


@dataclass(frozen=True, slots=True)
class MetadataProjection:
    attempted: bool
    occurred: bool
    rollback: bool
    survives: bool
    explicit_unknown: bool
    validation: str | None
    affected_paths: tuple[str, ...]
    mutated_paths: tuple[str, ...]
    surviving_paths: tuple[str, ...]


def project_metadata_records(result: Any) -> tuple[MetadataProjection, ...]:
    return tuple(project_metadata(metadata) for metadata in _metadata(result))


def project_metadata(metadata: Mapping[str, Any]) -> MetadataProjection:
    affected_paths = _paths_for(
        metadata,
        "affected_files", "affected_resources", "attempted_files", "mutated_files",
        "mutated_resources", "surviving_files", "surviving_resources",
    )
    mutated_paths = _paths_for(metadata, "mutated_files", "mutated_resources")
    surviving_paths = _paths_for(metadata, "surviving_files", "surviving_resources")
    rollback_flag = metadata.get("rollback_occurred") is True
    final_state = metadata.get("final_state")
    applied = metadata.get("applied")
    attempted = any(
        (
            metadata.get("mutation_attempted") is True,
            applied is True,
            metadata.get("mutation_occurred") is True,
            metadata.get("persisted_mutation") is True,
            metadata.get("surviving_mutation") is True,
            bool(affected_paths),
        )
    )
    occurred = (
        metadata.get("mutation_occurred") is True
        and (applied is not False or rollback_flag or final_state == "unknown")
    ) or metadata.get("persisted_mutation") is True or metadata.get("surviving_mutation") is True
    occurred = occurred or bool(mutated_paths) or bool(surviving_paths)
    survives = (
        metadata_is_persisted_mutation(metadata)
        or metadata.get("surviving_mutation") is True
        or bool(surviving_paths)
        or (
            final_state == "applied"
            and (metadata.get("mutation_occurred") is True or applied is True or bool(affected_paths))
        )
    )
    rollback = rollback_flag or final_state == "restored"
    if metadata.get("mutation_occurred") is False and not rollback:
        occurred = False
        survives = False
    occurred = occurred or rollback
    attempted = attempted or rollback
    if occurred and not mutated_paths:
        mutated_paths = affected_paths
    if survives and not surviving_paths:
        surviving_paths = affected_paths
    raw_validation = metadata.get("validation")
    if raw_validation is None:
        raw_validation = metadata.get("validation_status")
    return MetadataProjection(
        attempted=attempted,
        occurred=occurred,
        rollback=rollback,
        survives=survives,
        explicit_unknown=final_state == "unknown",
        validation=str(raw_validation) if raw_validation is not None else None,
        affected_paths=affected_paths,
        mutated_paths=mutated_paths,
        surviving_paths=surviving_paths,
    )


def _paths_for(metadata: Mapping[str, Any], *keys: str) -> tuple[str, ...]:
    values: list[str] = []
    for key in keys:
        raw = metadata.get(key)
        if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
            continue
        for value in raw:
            if isinstance(value, str) and value.strip() and value not in values:
                values.append(value)
    return tuple(values)


__all__ = [
    "MetadataProjection",
    "artifact_metadata",
    "metadata_is_persisted_mutation",
    "project_metadata_records",
]
