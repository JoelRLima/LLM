"""Test-only helpers for the H-series scripted tool-discovery responses."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from agent.llm.tool_discovery_contract import MAX_DISCLOSED_TOOLS


def selection_response(
    prompt: str,
    *,
    limit: int = MAX_DISCLOSED_TOOLS,
    required_tools: Sequence[str] = (),
) -> str:
    """Prioritize fixture-plan tools while preserving the exact catalog boundary."""

    try:
        catalog_text = prompt.split("<untrusted_tool_catalog>", 1)[1].split(
            "</untrusted_tool_catalog>", 1
        )[0]
        catalog = json.loads(catalog_text.strip())
        names = [
            entry["name"]
            for entry in catalog
            if isinstance(entry, dict) and isinstance(entry.get("name"), str)
        ]
    except (IndexError, KeyError, TypeError, json.JSONDecodeError):
        names = []
    catalog_names = tuple(dict.fromkeys(names))
    required = tuple(
        dict.fromkeys(
            tool for tool in required_tools if isinstance(tool, str) and tool in catalog_names
        )
    )
    selected = required + tuple(name for name in catalog_names if name not in required)
    return json.dumps({"tools": list(selected[:limit])})


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
