"""Provider execution helpers for the read-only code proposal workflow."""

from __future__ import annotations

from typing import Any

from llm_agent.agent.llm.contracts import ModelRequest


def context_limit(service: Any) -> int | None:
    hardware = getattr(service.context, "hardware_profile", None)
    value = getattr(hardware, "context_limit", None)
    if isinstance(value, int) and value > 0:
        return value
    metadata = getattr(service.context, "metadata", {})
    value = metadata.get("context_limit") if isinstance(metadata, dict) else None
    return value if isinstance(value, int) and value > 0 else None


def complete(service: Any, request: ModelRequest) -> tuple[Any, int]:
    """Delegate the coding provider attempt to the canonical lifecycle owner."""

    from llm_agent.agent.runtime.model_call import ModelCallService

    outcome = ModelCallService.for_context(service.context).complete(
        request,
        operation="propose_changes",
    )
    return outcome.response, outcome.call_number
