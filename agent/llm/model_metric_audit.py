"""Secret-safe identity fields shared by model-call metrics."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent.llm.identity import declared_model_audit_identity, observed_provider_model_id


def project_model_call_audit_fields(
    gateway: Any,
    response: Any,
    config: Mapping[str, Any],
) -> dict[str, Any]:
    """Return declared and observed identity without copying response content."""

    fields = declared_model_audit_identity(gateway, config=config)
    response_metadata = getattr(response, "provider_metadata", None)
    if response_metadata is None and isinstance(response, Mapping):
        response_metadata = response.get("provider_metadata")
    fields["observed_provider_model_id"] = observed_provider_model_id(response_metadata)
    return fields


__all__ = ["project_model_call_audit_fields"]
