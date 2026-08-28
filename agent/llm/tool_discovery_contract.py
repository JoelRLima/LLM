"""Canonical static contract for model tool-discovery selections."""

from __future__ import annotations

from typing import Any

MAX_DISCLOSED_TOOLS = 8
MAX_TOOL_NAME_CHARS = 128


def valid_tool_discovery(decision: Any) -> bool:
    """Admit only a bounded, duplicate-free exact-name selection object."""

    if not isinstance(decision, dict) or set(decision) != {"tools"}:
        return False
    tools = decision.get("tools")
    return (
        isinstance(tools, list)
        and len(tools) <= MAX_DISCLOSED_TOOLS
        and all(
            type(tool) is str
            and bool(tool.strip())
            and len(tool) <= MAX_TOOL_NAME_CHARS
            for tool in tools
        )
        and len(tools) == len(set(tools))
    )


__all__ = ["MAX_DISCLOSED_TOOLS", "MAX_TOOL_NAME_CHARS", "valid_tool_discovery"]
