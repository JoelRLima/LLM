"""Compatibilidade de métricas e retry para a decisão canônica do agente."""

from __future__ import annotations

import datetime as dt
import time
from collections.abc import Callable, Mapping
from typing import Any, Dict, cast

from agent.llm.contracts import ModelProviderError, ModelRequest, ModelResponse, normalize_usage
from agent.llm.legacy_payload import (
    build_legacy_model_request,
    complete_legacy_payload_request,
)
from agent.llm.structured_output import normalize_model_decision
from agent.runtime.budget import BudgetExhausted


def record_legacy_metadata(
    callback: Callable[[Dict[str, Any]], None] | None,
    response: ModelResponse,
    decision: Dict[str, Any] | None,
    request: ModelRequest,
    step_type: str,
    started_at: float,
) -> None:
    """Preserve the historical metric projection without owning transport."""

    if callback is None:
        return
    input_tokens, output_tokens, total_tokens, _, _ = normalize_usage(response.usage)
    entry: Dict[str, Any] = {
        "type": "model_metadata",
        "metric_type": "model_metadata",
        "timestamp": dt.datetime.now().isoformat(),
        "step_type": step_type,
        "tool": decision.get("tool") if decision else None,
        "budget": request.max_output_tokens,
        "prompt_tokens": input_tokens,
        "completion_tokens": output_tokens,
        "duration_ms": int((time.monotonic() - started_at) * 1000),
        "success": bool(decision and "action" in decision),
    }
    if total_tokens is not None:
        entry["total_tokens"] = total_tokens
    callback(entry)


def build_retry_request(
    session: Any,
    effective_grammar: str | None,
    hardware_profile: Any,
    default_max_tokens: int,
) -> ModelRequest:
    """Build the retry request using the session's current grammar capability."""

    retry_grammar = (
        effective_grammar
        if getattr(session, "_grammar_supports_grammar", None) is not False
        else None
    )
    retry_budget = _retry_budget(session, hardware_profile, default_max_tokens)
    return cast(
        ModelRequest,
        session.build_request(
            grammar=retry_grammar,
            stream=False,
            max_output_tokens=int(retry_budget),
        ),
    )


def _retry_budget(session: Any, hardware_profile: Any, default_max_tokens: int) -> int:
    config = getattr(session, "config", {})
    configured = config.get("agent_max_tokens") if isinstance(config, Mapping) else None
    if isinstance(configured, int) and not isinstance(configured, bool) and configured > 0:
        return configured
    profile_limit = getattr(hardware_profile, "default_output_tokens", None)
    if not isinstance(profile_limit, int) or isinstance(profile_limit, bool) or profile_limit <= 0:
        profile_limit = default_max_tokens * 2
    return min(default_max_tokens * 2, profile_limit)


def build_legacy_retry_request(
    session: Any,
    base_payload: Mapping[str, Any],
    effective_grammar: str | None,
    hardware_profile: Any,
    default_max_tokens: int,
) -> ModelRequest:
    """Build a legacy retry through the same canonical request translator."""

    retry_grammar = (
        effective_grammar
        if getattr(session, "_grammar_supports_grammar", None) is not False
        else None
    )
    retry_payload = dict(base_payload)
    retry_payload["max_tokens"] = _retry_budget(
        session, hardware_profile, default_max_tokens
    )
    retry_payload["stream"] = False
    builder = getattr(type(session), "build_legacy_request", None)
    if callable(builder):
        return cast(
            ModelRequest,
            session.build_legacy_request(retry_payload, grammar=retry_grammar),
        )
    return build_legacy_model_request(session, retry_payload, grammar=retry_grammar)


def complete_legacy_retry(
    session: Any,
    base_payload: Mapping[str, Any],
    request: ModelRequest,
) -> Dict[str, Any] | None:
    """Compatibility-only one-shot retry using the canonical parser."""

    try:
        response = (
            session.complete_request(request)
            if callable(getattr(type(session), "complete_request", None))
            else complete_legacy_payload_request(session, base_payload, request)
        )
    except (BudgetExhausted, ModelProviderError):
        raise
    except Exception as exc:
        raise ModelProviderError(str(exc), cause=exc) from exc
    return normalize_model_decision(response)
