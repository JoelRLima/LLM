"""Compatibility exports for runtime-owned model trace identity primitives."""

from __future__ import annotations

from agent.llm.identity import (
    bounded_identity_text,
    call_identity,
    observed_provider_model_id,
)

__all__ = ["bounded_identity_text", "call_identity", "observed_provider_model_id"]
