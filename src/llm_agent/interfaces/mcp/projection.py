"""Shared safe document projection for MCP results."""

from __future__ import annotations

from typing import Any, cast

from llm_agent.application.agent_boundary import ModelSafeResponse


def response_document(response: ModelSafeResponse) -> dict[str, Any]:
    return cast(dict[str, Any], response.to_dict())


__all__ = ["response_document"]
