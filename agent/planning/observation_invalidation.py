"""Pure helpers for filesystem-observation freshness.

``StepPolicies`` remains the execution-policy owner; this module only keeps
the footprint and state-scrubbing mechanics out of that policy's large module.
"""

from __future__ import annotations

import posixpath
from collections.abc import Mapping
from typing import Any, Dict

from agent.contracts import ToolArgs, ToolResult
from agent.planning.tool_metadata import ToolMetadata

_EFFECT_FIELDS = (
    "persisted_mutation",
    "surviving_mutation",
    "affected_files",
    "rollback_occurred",
    "final_state",
)
_MARKER_PREFIXES = (
    "code_analyzer_",
    "file_reader_",
    "fully_read_",
    "fully_analyzed_",
)


def _normalize_path(value: object) -> str:
    text = str(value).strip().replace("\\", "/")
    return posixpath.normpath(text) if text else ""


def _effect_values(result: ToolResult | None, field: str) -> tuple[object, ...]:
    if not isinstance(result, Mapping):
        return ()
    if field in result:
        return (result.get(field),)
    artifacts = result.get("artifacts")
    if not isinstance(artifacts, (list, tuple)):
        return ()
    values: list[object] = []
    for artifact in artifacts:
        if not isinstance(artifact, Mapping):
            continue
        metadata = artifact.get("metadata")
        if isinstance(metadata, Mapping) and field in metadata:
            values.append(metadata.get(field))
    return tuple(values)


def _has_effect_field(result: ToolResult | None, field: str) -> bool:
    return bool(_effect_values(result, field))


def _effect_flag(result: ToolResult | None, field: str) -> bool:
    return any(value is True for value in _effect_values(result, field))


def _is_restored(result: ToolResult | None) -> bool:
    return any(
        str(value or "").casefold() == "restored"
        for value in _effect_values(result, "final_state")
    )


def _affected_files(result: ToolResult | None) -> tuple[str, ...]:
    normalized: list[str] = []
    for candidates in _effect_values(result, "affected_files"):
        if isinstance(candidates, str):
            candidates = [candidates]
        if not isinstance(candidates, (list, tuple, set)):
            continue
        for candidate in candidates:
            path = _normalize_path(candidate)
            if path and path not in normalized:
                normalized.append(path)
    return tuple(normalized)


def _has_effect_projection(result: ToolResult | None) -> bool:
    return any(_has_effect_field(result, field) for field in _EFFECT_FIELDS)


def _can_have_legacy_mutated(result: ToolResult | None) -> bool:
    if not isinstance(result, Mapping):
        return True
    status = str(result.get("status") or "").casefold()
    if status in {"blocked", "cancelled", "permission_denied", "skipped"}:
        return False
    return result.get("ok") is True or status in {"succeeded", "unverified"}


def _metadata_indicates_mutation(
    tool: str, args: ToolArgs, metadata: ToolMetadata
) -> bool:
    if not (metadata.modifies_workspace or metadata.writes_disk):
        return False
    if tool == "code_task":
        return str(args.get("action") or "analyze").casefold() not in {
            "analyze", "review"
        }
    return True


def mutation_footprint(
    tool: str,
    args: ToolArgs,
    result: ToolResult | None,
    metadata: ToolMetadata,
) -> tuple[bool, tuple[str, ...]]:
    """Return ``(survives, affected_files)`` for one tool result."""

    affected = _affected_files(result)
    if _has_effect_projection(result):
        if _effect_flag(result, "surviving_mutation") or _effect_flag(
            result, "persisted_mutation"
        ):
            return True, affected
        if _effect_flag(result, "rollback_occurred") or _is_restored(result):
            return False, ()
        if _has_effect_field(result, "persisted_mutation") or _has_effect_field(
            result, "surviving_mutation"
        ):
            return False, ()
        return bool(affected and _can_have_legacy_mutated(result)), affected

    if not _can_have_legacy_mutated(result):
        return False, ()
    return _metadata_indicates_mutation(tool, args, metadata), affected


def _marker_matches(key: str, file_path: str) -> bool:
    normalized = key.replace("\\", "/")
    for prefix in ("code_analyzer_", "fully_read_", "fully_analyzed_"):
        if normalized.startswith(prefix):
            return _normalize_path(normalized[len(prefix):]) == file_path
    if normalized.startswith("file_reader_"):
        remainder = normalized[len("file_reader_"):]
        return remainder == file_path or remainder.startswith(f"{file_path}_")
    return False


def _matching_keys(mapping: object, affected: tuple[str, ...]) -> tuple[str, ...]:
    if not isinstance(mapping, dict):
        return ()
    keys = tuple(str(key) for key in mapping)
    if not affected:
        return keys
    targets = set(affected)
    return tuple(key for key in keys if _normalize_path(key) in targets)


def _clear_markers(usage: Dict[str, int], affected: tuple[str, ...]) -> None:
    for key in tuple(usage):
        if key.startswith(_MARKER_PREFIXES) and (
            not affected or any(_marker_matches(key, path) for path in affected)
        ):
            usage.pop(key, None)


def _clear_summaries(memory: Any, state: dict[str, Any], affected: tuple[str, ...]) -> None:
    summaries = state.get("file_summaries")
    keys = _matching_keys(summaries, affected)
    forget = getattr(memory, "forget", None)
    if callable(forget):
        for key in keys:
            forget(key, section="file_summaries")
    elif isinstance(summaries, dict):
        for key in keys:
            summaries.pop(key, None)


def clear_observation_state(
    context: Any, usage: Dict[str, int], affected: tuple[str, ...]
) -> None:
    """Clear runtime and memory projections, using AgentMemory.forget for summaries."""

    _clear_markers(usage, affected)

    memory = getattr(getattr(context, "agent_state", None), "memory", None)
    state = getattr(memory, "state", None)
    if not isinstance(state, dict):
        return

    _clear_summaries(memory, state, affected)

    for name in ("file_hashes", "file_cache_entries", "analyzed_files"):
        values = state.get(name)
        if isinstance(values, dict):
            for key in _matching_keys(values, affected):
                values.pop(key, None)


__all__ = ["clear_observation_state", "mutation_footprint"]
