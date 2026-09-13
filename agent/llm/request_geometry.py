"""Single generic owner for effective model-call request geometry."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent.llm.contracts import ProviderCapabilities, StructuredOutputMode
from agent.llm.model_compatibility import (
    ModelCompatibility,
    StructuredReasoningPolicy,
)

STRUCTURED_REASONING_DISABLED_BY_PROFILE = (
    "STRUCTURED_REASONING_DISABLED_BY_PROFILE"
)
_CONSTRAINED_MODES = frozenset(
    {StructuredOutputMode.GBNF, StructuredOutputMode.JSON_SCHEMA}
)


@dataclass(frozen=True, slots=True)
class EffectiveRequestGeometry:
    requested_reasoning_budget: int
    effective_reasoning_budget: int
    structured_output_mode: StructuredOutputMode | None
    compatibility_adjusted: bool
    compatibility_reason_code: str | None


def resolve_ordinary_reasoning_budget(
    requested_reasoning_budget: int,
    max_output_tokens: int,
    reasoning_supported: bool,
) -> int:
    """Preserve the W12 output-budget clamp exactly."""

    if not reasoning_supported:
        return 0
    requested = max(0, requested_reasoning_budget)
    output = max_output_tokens
    if output <= 1 or requested == 0:
        return 0
    final_output_reserve = min(256, max(1, output // 4))
    max_safe_reasoning = max(0, output - final_output_reserve)
    return min(requested, max_safe_reasoning)


def resolve_effective_structured_mode(
    mode: StructuredOutputMode | None,
    capabilities: ProviderCapabilities | Any | None = None,
) -> StructuredOutputMode | None:
    """Concretize AUTO from typed profile capabilities, or fail closed."""

    if mode is None:
        return None
    if not isinstance(mode, StructuredOutputMode):
        try:
            mode = StructuredOutputMode(mode)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"unknown structured output mode: {mode!r}") from exc
    if mode is not StructuredOutputMode.AUTO:
        return mode
    declared = getattr(capabilities, "structured_output_modes", None)
    if not isinstance(declared, (tuple, list)) or not declared:
        raise ValueError("AUTO structured output mode cannot be resolved")
    first = declared[0]
    if not isinstance(first, StructuredOutputMode) or first is StructuredOutputMode.AUTO:
        raise ValueError("AUTO structured output mode is not concretely declared")
    return first


def resolve_effective_request_geometry(
    requested_reasoning_budget: int,
    max_output_tokens: int,
    reasoning_supported: bool,
    structured_output_mode: StructuredOutputMode | None,
    compatibility: ModelCompatibility | None = None,
    *,
    capabilities: ProviderCapabilities | Any | None = None,
) -> EffectiveRequestGeometry:
    """Resolve ordinary output geometry, then the typed compatibility clamp."""

    if isinstance(requested_reasoning_budget, bool):
        requested = 0
    else:
        try:
            requested = max(0, int(requested_reasoning_budget))
        except (TypeError, ValueError):
            requested = 0
    mode = resolve_effective_structured_mode(structured_output_mode, capabilities)
    ordinary = resolve_ordinary_reasoning_budget(
        requested,
        max_output_tokens,
        reasoning_supported,
    )
    policy = compatibility or ModelCompatibility()
    adjusted = (
        ordinary > 0
        and mode in _CONSTRAINED_MODES
        and policy.structured_reasoning
        is StructuredReasoningPolicy.DISABLE_REASONING
    )
    return EffectiveRequestGeometry(
        requested_reasoning_budget=requested,
        effective_reasoning_budget=0 if adjusted else ordinary,
        structured_output_mode=mode,
        compatibility_adjusted=adjusted,
        compatibility_reason_code=(
            STRUCTURED_REASONING_DISABLED_BY_PROFILE if adjusted else None
        ),
    )


def is_constrained_structured_output_mode(
    mode: StructuredOutputMode | None,
) -> bool:
    return mode in _CONSTRAINED_MODES


__all__ = [
    "EffectiveRequestGeometry",
    "STRUCTURED_REASONING_DISABLED_BY_PROFILE",
    "is_constrained_structured_output_mode",
    "resolve_effective_request_geometry",
    "resolve_effective_structured_mode",
    "resolve_ordinary_reasoning_budget",
]
