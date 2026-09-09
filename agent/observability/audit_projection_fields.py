"""Small pure helpers used by the bounded run-audit projection."""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping, Sequence
from types import MappingProxyType
from typing import Any

from agent.runtime.mutation_evidence import project_mutation_evidence
from agent.runtime.path_safety import workspace_relative_path

MAX_AUDIT_TEXT = 256
MAX_AUDIT_CAPABILITIES = 32
MAX_AUDIT_TOOLS = 32
MAX_AUDIT_ARTIFACTS = 32
MAX_AUDIT_PATHS = 32
MAX_AUDIT_MODEL_IDS = 32
APPROVAL_DISPOSITIONS = frozenset(
    {"not_required", "approved", "required", "denied", "failed", "unknown"}
)
_EXTERNAL_SCOPE = MappingProxyType({"scope": "external"})


def _external_scope() -> dict[str, str]:
    return dict(_EXTERNAL_SCOPE)


def _bounded_text(value: Any, *, limit: int = MAX_AUDIT_TEXT) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:limit] if text else None


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return tuple(_freeze(item) for item in sorted(value, key=repr))
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _safe_string(value: Any) -> str | None:
    return _bounded_text(value)


def _safe_capabilities(value: Any) -> tuple[str, ...] | None:
    if value is None:
        return None
    values: Iterable[Any] = (value,) if isinstance(value, str) else value
    selected: set[str] = set()
    try:
        for item in values:
            text = _bounded_text(item)
            if text:
                selected.add(text)
    except TypeError:
        return None
    return tuple(sorted(selected)[:MAX_AUDIT_CAPABILITIES])


def project_required_capabilities(value: Any) -> list[str] | None:
    projected = _safe_capabilities(value)
    return list(projected) if projected is not None else None


def project_approval_disposition(value: Any) -> str:
    selected = _bounded_text(value)
    return selected if selected in APPROVAL_DISPOSITIONS else "unknown"


def _is_absolute_path(value: str) -> bool:
    normalized = value.replace("\\", "/")
    return normalized.startswith(("/", "//")) or (
        len(normalized) >= 3
        and normalized[0].isalpha()
        and normalized[1:3] == ":/"
    )


def _safe_relative_path(
    value: Any,
    workspace_root: str | os.PathLike[str] | None,
) -> str | Mapping[str, str] | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip().replace("\\", "/")
    if workspace_root is None:
        if _is_absolute_path(raw):
            return _external_scope()
        normalized = os.path.normpath(raw).replace("\\", "/")
        if normalized in {"", "."}:
            return None
        return _external_scope() if normalized == ".." or normalized.startswith("../") else normalized[:MAX_AUDIT_TEXT]
    try:
        relative = workspace_relative_path(str(workspace_root), raw)
    except (OSError, RuntimeError, ValueError):
        return _external_scope()
    if not relative or relative == "." or relative == ".." or relative.startswith("../"):
        return _external_scope()
    return relative[:MAX_AUDIT_TEXT]


def _bounded_paths(
    values: Any,
    workspace_root: str | os.PathLike[str] | None,
) -> list[Any]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)):
        return []
    result: list[Any] = []
    for value in values:
        projected = _safe_relative_path(value, workspace_root)
        if projected is not None and projected not in result:
            result.append(projected)
        if len(result) >= MAX_AUDIT_PATHS:
            break
    return result


def project_effect(
    result: Any,
    workspace_root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Project canonical mutation evidence without raw result content."""

    evidence = project_mutation_evidence(result)
    return {
        "attempted": bool(evidence.attempted),
        "occurred": bool(evidence.occurred),
        "persisted": bool(evidence.survives),
        "survives": bool(evidence.survives),
        "rollback_occurred": bool(evidence.rollback_occurred),
        "validation_status": _bounded_text(evidence.validation_status),
        "final_state": _bounded_text(evidence.final_state),
        "affected_files": _bounded_paths(evidence.affected_files, workspace_root),
    }


def _event_parts(event: Any) -> tuple[str | None, Mapping[str, Any]]:
    if hasattr(event, "to_legacy_dict") and callable(event.to_legacy_dict):
        event = event.to_legacy_dict()
    if not isinstance(event, Mapping):
        return None, {}
    event_type = event.get("type") or event.get("kind")
    event_type = getattr(event_type, "value", event_type)
    data = event.get("data")
    return str(event_type) if event_type is not None else None, data if isinstance(data, Mapping) else event


__all__ = [
    "APPROVAL_DISPOSITIONS",
    "MAX_AUDIT_ARTIFACTS",
    "MAX_AUDIT_CAPABILITIES",
    "MAX_AUDIT_MODEL_IDS",
    "MAX_AUDIT_PATHS",
    "MAX_AUDIT_TEXT",
    "MAX_AUDIT_TOOLS",
    "_bounded_text",
    "_event_parts",
    "_freeze",
    "_safe_capabilities",
    "_safe_relative_path",
    "_safe_string",
    "_thaw",
    "project_approval_disposition",
    "project_effect",
    "project_required_capabilities",
]
