"""Pure helpers for filesystem-observation freshness.

``StepPolicies`` remains the execution-policy owner; this module only keeps
the footprint and state-scrubbing mechanics out of that policy's large module.
"""

from __future__ import annotations

import posixpath
from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any, Dict

from agent.capabilities import Capability, capability_values
from agent.contracts import ToolArgs
from agent.planning.tool_metadata import ToolMetadata
from agent.runtime.mutation_evidence import project_mutation_evidence
from agent.runtime.outcome_taxonomy import OperationalStatus, operational_status_for
from agent.tools.contracts import ToolResult
from agent.tools.invocation_semantics import resolve_invocation_semantics

_MARKER_PREFIXES = (
    "code_analyzer_",
    "file_reader_",
    "fully_read_",
    "fully_analyzed_",
)


def _normalize_path(value: object) -> str:
    """Normalize observation keys lexically, without filesystem resolution."""

    text = str(value).strip().replace("\\", "/")
    return posixpath.normpath(text) if text else ""


def _can_have_legacy_mutated(result: Any) -> bool:
    if not isinstance(result, Mapping):
        return True
    status = operational_status_for(result.get("status"))
    if status in {
        OperationalStatus.BLOCKED.value,
        OperationalStatus.CANCELLED.value,
        OperationalStatus.PERMISSION_DENIED.value,
    }:
        return False
    return result.get("ok") is True or status in {
        OperationalStatus.SUCCEEDED.value,
        OperationalStatus.UNVERIFIED.value,
    }


def mutation_footprint(
    tool: str,
    args: ToolArgs,
    result: ToolResult | Mapping[str, Any] | None,
    metadata: ToolMetadata,
    *,
    descriptor: Any | None = None,
) -> tuple[bool, tuple[str, ...]]:
    """Return ``(survives, affected_files)`` for one tool result."""

    evidence = project_mutation_evidence(result)
    if evidence.survives:
        return True, evidence.surviving_files
    if evidence.attempted or evidence.occurred or evidence.rollback_occurred:
        return False, ()
    if not _can_have_legacy_mutated(result):
        return False, ()
    # Legacy tools that predate structured artifact evidence remain a narrow
    # compatibility case.  The action/effect authority still comes from the
    # canonical invocation resolver; metadata is adapted only when a legacy
    # test/context has no live ToolDescriptor to provide.
    trusted_descriptor = descriptor or SimpleNamespace(
        name=tool,
        capabilities=capability_values(
            item
            for item, enabled in (
                (Capability.READ, metadata.reads_disk),
                (Capability.WRITE, metadata.writes_disk or metadata.modifies_workspace),
            )
            if enabled
        ),
        cacheable=metadata.cacheable,
        idempotent=False,
        cancellation_safety="unsupported",
    )
    semantics = resolve_invocation_semantics(trusted_descriptor, args)
    return semantics.may_mutate, evidence.affected_files


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
