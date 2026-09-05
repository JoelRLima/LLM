"""Value and code-outcome projections for run reports."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import Any, cast

from agent.reporting.public_safety import sanitize_public_text
from agent.runtime.mutation_evidence import project_mutation_evidence

MAX_PROJECTION_HISTORY = 50
MAX_PROJECTION_FILES = 128
MAX_PROJECTION_PATH_CHARS = 512
MAX_PROJECTION_TEXT = 500


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _text(value: Any, limit: int = MAX_PROJECTION_TEXT) -> str:
    if value is None:
        raw = ""
    elif isinstance(value, str):
        raw = value
    else:
        try:
            raw = json.dumps(value, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            raw = str(value)
    safe = str(sanitize_public_text(raw))
    return safe[:limit] + ("..." if len(safe) > limit else "")


def _project_args(raw: Any) -> dict[str, str]:
    if not isinstance(raw, Mapping):
        return {}
    projected: dict[str, str] = {}
    for key in ("file_path", "path", "target", "mode", "action"):
        value = raw.get(key)
        if isinstance(value, (str, int, float, bool)):
            projected[key] = _text(value, 200)
    return projected


def _as_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    to_legacy = getattr(value, "to_legacy_dict", None)
    if callable(to_legacy):
        projected = to_legacy(include_details=True)
        return projected if isinstance(projected, Mapping) else {}
    return {}


def _bounded_code(value: Any) -> str | None:
    raw = getattr(value, "value", value)
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text or len(text) > 128:
        return None
    if not all(character.isupper() or character.isdigit() or character in "_-" for character in text):
        return None
    return text


def _metadata_records(result: Any, data: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    records: list[Mapping[str, Any]] = []
    object_metadata = getattr(result, "metadata", None)
    if isinstance(object_metadata, Mapping):
        records.append(object_metadata)
    result_mapping = _as_mapping(result)
    for candidate in (result_mapping, data):
        metadata = candidate.get("metadata")
        if isinstance(metadata, Mapping):
            records.append(metadata)
        artifacts = candidate.get("artifacts")
        if not isinstance(artifacts, Sequence) or isinstance(artifacts, (str, bytes, bytearray)):
            continue
        for artifact in list(artifacts)[:MAX_PROJECTION_HISTORY]:
            artifact_metadata = (
                artifact.get("metadata")
                if isinstance(artifact, Mapping)
                else getattr(artifact, "metadata", None)
            )
            if isinstance(artifact_metadata, Mapping):
                records.append(artifact_metadata)
    return tuple(records)


def _first_mapping_value(
    containers: Sequence[Mapping[str, Any]],
    key: str,
) -> Mapping[str, Any] | None:
    for container in containers:
        value = container.get(key)
        if isinstance(value, Mapping):
            return value
    return None


def _bounded_string_list(value: Any, *, limit: int, chars: int) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return ()
    values: list[str] = []
    for item in list(value)[:limit]:
        if not isinstance(item, str):
            continue
        bounded = _text(item, chars)
        if bounded not in values:
            values.append(bounded)
    return tuple(values)


def _canonical_code_outcome(history: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    """Select fixed code-task outcome facts before generic event bounding."""

    for entry in reversed(history):
        if entry.get("tool") != "code_task":
            continue
        result = entry.get("result")
        result_mapping = _as_mapping(result)
        data = result_mapping.get("data")
        data_mapping = data if isinstance(data, Mapping) else {}
        metadata = _metadata_records(result, data_mapping)
        containers = (*metadata, result_mapping, data_mapping)
        seal = _first_mapping_value(containers, "code_outcome")
        verification = _first_mapping_value(containers, "code_verification")
        kind = _bounded_code(seal.get("kind") if seal else None)
        if kind is None:
            kind = _bounded_code(
                next(
                    (
                        item.get("proposal_kind")
                        for item in containers
                        if item.get("proposal_kind") is not None
                    ),
                    None,
                )
            )
        reason_code = _bounded_code(
            next(
                (
                    item.get("reason_code", item.get("proposal_reason_code"))
                    for item in containers
                    if item.get("reason_code", item.get("proposal_reason_code")) is not None
                ),
                None,
            )
        )
        verdict = _bounded_code(seal.get("verification") if seal else None)
        if verdict is None and verification is not None:
            verdict = _bounded_code(verification.get("verdict"))
        failure_code = _bounded_code(
            next(
                (
                    item.get("failure_code")
                    for item in (data_mapping, result_mapping, *containers)
                    if item.get("failure_code") is not None
                ),
                None,
            )
        )
        evidence_ids_source = (
            seal.get("cited_evidence_ids")
            if seal is not None
            else verification.get("evidence_ids")
            if verification is not None
            else next((item.get("cited_evidence_ids") for item in containers), ())
        )
        mutation = project_mutation_evidence(result)
        if not any(
            (
                kind is not None,
                reason_code is not None,
                verdict is not None,
                failure_code is not None,
                mutation.attempted,
                mutation.occurred,
                mutation.validation_status is not None,
            )
        ):
            continue
        projected = {
            "kind": kind,
            "reason_code": reason_code,
            "verification": verdict,
            "failure_code": failure_code,
            "evidence_ids": _bounded_string_list(
                evidence_ids_source,
                limit=16,
                chars=128,
            ),
            "mutation_attempted": mutation.attempted,
            "mutation_occurred": mutation.occurred,
            "persisted_mutation": mutation.survives,
            "surviving_mutation": mutation.survives,
            "affected_files": _bounded_string_list(
                mutation.affected_files,
                limit=MAX_PROJECTION_FILES,
                chars=MAX_PROJECTION_PATH_CHARS,
            ),
        }
        return cast(Mapping[str, Any], _freeze(projected))
    return cast(Mapping[str, Any], _freeze(
        {
            "kind": None,
            "reason_code": None,
            "verification": None,
            "failure_code": None,
            "evidence_ids": (),
            "mutation_attempted": False,
            "mutation_occurred": False,
            "persisted_mutation": False,
            "surviving_mutation": False,
            "affected_files": (),
        }
    ))

__all__ = [
    "_as_mapping",
    "_bounded_string_list",
    "_canonical_code_outcome",
    "_freeze",
    "_metadata_records",
    "_project_args",
    "_text",
]
