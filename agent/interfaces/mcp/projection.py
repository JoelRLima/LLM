"""Shared safe document projection for MCP results."""

from __future__ import annotations

from typing import Any

from agent.engineering.model_safe import ModelSafeResponse


def response_document(response: ModelSafeResponse) -> dict[str, Any]:
    return response.to_dict()


__all__ = ["response_document"]
