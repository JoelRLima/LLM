"""Shared safe document projection for MCP results."""

from __future__ import annotations

from typing import Any, cast


def response_document(response: Any) -> dict[str, Any]:
    return cast(dict[str, Any], response.to_dict())


__all__ = ["response_document"]
