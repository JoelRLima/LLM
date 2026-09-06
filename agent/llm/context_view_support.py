"""Bounded data projections used when a conversation is compacted."""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from agent.llm.context_projection import (
    UNTRUSTED_MEMORY,
    UNTRUSTED_REPOSITORY_STATE,
    UNTRUSTED_SESSION,
    ContextSourceRecord,
    render_untrusted_context_envelope,
)
from agent.memory.prompt_context import build_memory_prompt_context
from agent.skills.repository_state import RepositoryStateSnapshot
from agent.tools.result_adapter import result_artifacts, result_data, result_metadata, result_status


def requires_compaction(messages: Sequence[Mapping[str, Any]]) -> bool:
    total_chars = sum(len(str(message.get("content", ""))) for message in messages)
    return total_chars > 8_000 or any(
        len(str(message.get("content", ""))) > 2_000 for message in messages
    )


def recent_message_views(
    messages: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    body = list(messages[1:])
    latest = body[-1] if body and body[-1].get("role") == "user" else None
    recent = body[:-1] if latest is not None else body
    views: list[dict[str, Any]] = []
    for message in recent[-8:]:
        content = str(message.get("content", ""))
        if len(content) > 2_000:
            content = content[:1_000] + "\n[…conteúdo truncado para contexto…]\n" + content[-1_000:]
        views.append({"role": str(message.get("role", "user")), "content": content})
    return views, dict(latest) if latest is not None else None


def _is_mutating_history_entry(tool_name: str, result: Any) -> bool:
    if tool_name not in {"code_task", "file_writer", "shell", "python_executor"}:
        return False
    metadata = result_metadata(result)
    if metadata.get("mutation_occurred") is True:
        return True
    return any(
        isinstance(item, Mapping)
        and isinstance(item.get("metadata"), Mapping)
        and item["metadata"].get("mutation_occurred") is True
        for item in result_artifacts(result)
    )


def repository_state_records(
    tool_history: Sequence[Mapping[str, Any]],
) -> tuple[ContextSourceRecord, ...]:
    """Project only the latest non-invalidated runtime repository observation."""

    latest: ContextSourceRecord | None = None
    mutated_after = False
    for entry in list(tool_history)[-6:]:
        if not isinstance(entry, Mapping):
            continue
        result = entry.get("result")
        tool_name = str(entry.get("tool", ""))[:128]
        if tool_name == "repository_state" and isinstance(result, Mapping) and result_status(result) in {
            "succeeded",
            "success",
        }:
            try:
                raw_snapshot = result_data(result)
                snapshot = RepositoryStateSnapshot.from_dict(
                    raw_snapshot if isinstance(raw_snapshot, Mapping) else {}
                )
            except (TypeError, ValueError):
                latest = None
                mutated_after = False
            else:
                latest = ContextSourceRecord(
                    source_id=(
                        "repository-state:"
                        + str(entry.get("invocation_id", "latest"))[:128]
                    ),
                    source_kind="repository_state",
                    trust_class=UNTRUSTED_REPOSITORY_STATE,
                    reason="fresh bounded repository_state observation; data only",
                    estimated_tokens=1024,
                    truncated=snapshot.truncated,
                    complete=snapshot.complete,
                    data=snapshot.to_context_dict(),
                    freshness="CURRENT_TOOL_OBSERVATION",
                )
                mutated_after = False
        elif latest is not None and _is_mutating_history_entry(tool_name, result):
            mutated_after = True
    return (latest,) if latest is not None and not mutated_after else ()


def tool_history_view(tool_history: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    records = []
    for entry in list(tool_history)[-6:]:
        if isinstance(entry, Mapping):
            result = entry.get("result")
            records.append(
                {
                    "tool": str(entry.get("tool", ""))[:128],
                    "status": str(entry.get("status") or (result_status(result) if isinstance(result, Mapping) else ""))[:32],
                    "invocation_id": str(entry.get("invocation_id", ""))[:128],
                }
            )
    if not records:
        return None
    record = ContextSourceRecord(
        source_id="compact:tool-history",
        source_kind="tool_result",
        trust_class=UNTRUSTED_SESSION,
        reason="bounded prior tool metadata",
        data={"records": records},
    )
    return {
        "role": "tool",
        "content": render_untrusted_context_envelope(
            (record, *repository_state_records(tool_history))
        ),
    }


def memory_view(
    memory_state: Mapping[str, Any],
    *,
    workspace_root: str | os.PathLike[str] | None = None,
) -> dict[str, Any] | None:
    if not memory_state:
        return None
    projection = build_memory_prompt_context(
        memory_state,
        budget_tokens=800,
        workspace_root=Path(workspace_root) if workspace_root is not None else None,
    )
    if not projection:
        return None
    record = ContextSourceRecord(
        source_id="compact:memory",
        source_kind="memory",
        trust_class=UNTRUSTED_MEMORY,
        reason="bounded memory projection after freshness filtering",
        data={"content": projection},
    )
    return {"role": "tool", "content": render_untrusted_context_envelope((record,))}


__all__ = [
    "memory_view",
    "recent_message_views",
    "repository_state_records",
    "requires_compaction",
    "tool_history_view",
]
