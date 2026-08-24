"""Per-call identity primitives for the transparent model trace recorder."""

from __future__ import annotations

from typing import Any

from agent.evaluation.block7_model_identity import normalize_endpoint_identity


def bounded_identity_text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value).strip()[:256]


def call_identity(gateway: Any, request: Any, call_index: int) -> dict[str, Any]:
    return {
        "call_index": call_index,
        "provider": bounded_identity_text(getattr(gateway, "provider_name", None)),
        "endpoint_identity": bounded_identity_text(
            normalize_endpoint_identity(getattr(gateway, "endpoint_identity", None))
        ),
        "declared_model": bounded_identity_text(
            getattr(request, "model", None) or getattr(gateway, "model", None)
        ),
        "observed_provider_model_id": None,
        "identity_source": "unavailable",
    }


def observed_provider_model_id(metadata: Any) -> str | None:
    if not isinstance(metadata, dict):
        return None
    for key in ("observed_provider_model_id", "provider_model_id", "model_id", "model"):
        value = metadata.get(key)
        if value not in (None, ""):
            return bounded_identity_text(value)
    return None


__all__ = ["bounded_identity_text", "call_identity", "observed_provider_model_id"]
