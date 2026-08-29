"""Test-only helpers for the H-series scripted tool-discovery responses."""

from __future__ import annotations

import json
from typing import Any

from agent.llm.tool_discovery_contract import MAX_DISCLOSED_TOOLS


def selection_response(prompt: str, *, limit: int = MAX_DISCLOSED_TOOLS) -> str:
    """Select only names present in the exact framed Level-0 index."""

    try:
        catalog_text = prompt.split("<untrusted_tool_catalog>", 1)[1].split(
            "</untrusted_tool_catalog>", 1
        )[0]
        catalog = json.loads(catalog_text.strip())
        names = [entry["name"] for entry in catalog if isinstance(entry, dict)]
    except (IndexError, KeyError, TypeError, json.JSONDecodeError):
        names = []
    return json.dumps({"tools": names[:limit]})


def semantic_boundary_count(gateway: Any) -> int:
    """Count H5 boundary decisions without counting orthogonal discovery calls."""

    count = 0
    for call in getattr(gateway, "calls", ()):
        messages = getattr(call, "messages", None)
        if messages and "Uma fronteira sem" in str(messages[-1].content):
            count += 1
    return count


def h5_response(gateway: Any, combined: str, prompt: str) -> str | None:
    if "Uma fronteira sem" not in prompt or "H5" not in combined:
        return None
    if semantic_boundary_count(gateway) >= 2:
        return '{"action":"complete","reason":"H5_FINAL_EVIDENCE basta"}'
    return json.dumps(
        {"action": "execute", "plan": [{"tool": "file_reader", "args": {"file_path": "h5_second.txt"}}]}
    )
