"""Resume-time freshness revalidation for restored source observations."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def _value(value: Any, *names: str) -> Any:
    if isinstance(value, Mapping):
        for name in names:
            if name in value:
                return value[name]
        return None
    for name in names:
        candidate = getattr(value, name, None)
        if candidate is not None:
            return candidate
    return None


def revalidate_state_observation_freshness(
    state: Any,
    *,
    workspace_root: Any = None,
) -> dict[int, str]:
    """Revalidate restored source hashes through the existing freshness owner."""

    from agent.memory.prompt_context import file_fact_freshness

    memory = getattr(getattr(state, "memory", None), "state", None)
    if not isinstance(memory, Mapping):
        return {}
    selected_root = workspace_root or getattr(state, "_w15_workspace_root", None)
    if selected_root is None:
        return {}
    result: dict[int, str] = {}
    for index, entry in enumerate(getattr(state, "tool_history", ())):
        if not isinstance(entry, Mapping):
            continue
        raw_result = entry.get("result")
        source_hash = _value(raw_result, "source_hash", "hash")
        if not isinstance(source_hash, str) or not source_hash:
            continue
        args = entry.get("args", {})
        if not isinstance(args, Mapping):
            continue
        path = args.get("target") or args.get("file_path")
        if not isinstance(path, str) or not path:
            continue
        freshness = file_fact_freshness(
            memory,
            path,
            workspace_root=selected_root,
            expected_hash=source_hash,
        )
        result[index] = "CURRENT" if freshness == "FRESH_FILE_FACT" else "STALE"
    return result


__all__ = ["revalidate_state_observation_freshness"]
