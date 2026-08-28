"""Compatibilidade de métricas e retry para a decisão canônica do agente."""

from __future__ import annotations

import datetime as dt
import inspect
import time
from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Any, Dict, cast

from agent.llm.contracts import ModelProviderError, ModelRequest, ModelResponse, normalize_usage
from agent.llm.decision_contract import (
    ModelRequestContract,
    request_contract_for_request,
    request_contract_value,
)
from agent.llm.legacy_payload import (
    build_legacy_model_request,
    complete_legacy_payload_request,
)
from agent.llm.structured_output import (
    is_model_decision_contract_valid,
    normalize_model_decision,
)
from agent.runtime.budget import BudgetExhausted


def record_legacy_metadata(
    callback: Callable[[Dict[str, Any]], None] | None,
    response: ModelResponse,
    decision: Dict[str, Any] | None,
    request: ModelRequest,
    step_type: str,
    started_at: float,
    request_contract: ModelRequestContract | str | None = None,
) -> None:
    """Project provider return and resolver validity without owning task truth.

    ``success`` is retained only as the historical alias for
    ``structured_decision_valid``.  Provider-attempt accounting remains owned by
    ``TaskBudgetLedger`` and the canonical ``model_call`` event.
    """

    if callback is None:
        return
    input_tokens, output_tokens, total_tokens, _, _ = normalize_usage(response.usage)
    exact_contract = request_contract_for_request(
        request,
        request_contract=request_contract,
        step_type=step_type,
    )
    carried_contract = getattr(request, "request_contract", None)
    structured_decision_valid = (
        is_model_decision_contract_valid(
            response,
            step_type,
            request_contract=exact_contract,
        )
        if exact_contract is not None or (
            request_contract is None
            and carried_contract is None
            and step_type is not None
        )
        else False
    )
    entry: Dict[str, Any] = {
        "type": "model_metadata",
        "metric_type": "model_metadata",
        "timestamp": dt.datetime.now().isoformat(),
        "step_type": step_type,
        "request_contract": request_contract_value(exact_contract),
        "tool": decision.get("tool") if decision else None,
        "budget": request.max_output_tokens,
        "prompt_tokens": input_tokens,
        "completion_tokens": output_tokens,
        "duration_ms": int((time.monotonic() - started_at) * 1000),
        "provider_call_succeeded": True,
        "structured_decision_valid": structured_decision_valid,
        "success": structured_decision_valid,
        "canonical": False,
        "scope": "initial_response_compatibility",
    }
    if total_tokens is not None:
        entry["total_tokens"] = total_tokens
    callback(entry)


def build_request_with_contract(
    session: Any,
    *,
    grammar: str | None,
    stream: bool,
    max_output_tokens: int,
    request_contract: ModelRequestContract | None,
) -> ModelRequest:
    """Build through old doubles while preserving the exact request identity."""
    kwargs: Dict[str, Any] = {
        "grammar": grammar,
        "stream": stream,
        "max_output_tokens": max_output_tokens,
    }
    if request_contract is not None and _accepts_keyword(session.build_request, "request_contract"):
        kwargs["request_contract"] = request_contract
    request = session.build_request(**kwargs)
    if request_contract is not None and getattr(request, "request_contract", None) is None:
        try:
            request = replace(request, request_contract=request_contract)
        except TypeError:
            pass
    return cast(ModelRequest, request)


def build_retry_request(
    session: Any,
    effective_grammar: str | None,
    hardware_profile: Any,
    default_max_tokens: int,
    request_contract: ModelRequestContract | str | None = None,
) -> ModelRequest:
    """Build the retry request using the session's current grammar capability."""

    retry_grammar = (
        effective_grammar
        if getattr(session, "_grammar_supports_grammar", None) is not False
        else None
    )
    retry_budget = _retry_budget(session, hardware_profile, default_max_tokens)
    kwargs: Dict[str, Any] = {
        "grammar": retry_grammar,
        "stream": False,
        "max_output_tokens": int(retry_budget),
    }
    if request_contract is not None and _accepts_keyword(session.build_request, "request_contract"):
        kwargs["request_contract"] = request_contract
    request = session.build_request(**kwargs)
    if request_contract is not None and getattr(request, "request_contract", None) is None:
        try:
            request = replace(request, request_contract=request_contract)
        except TypeError:
            pass
    return cast(ModelRequest, request)


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
    request_contract: ModelRequestContract | str | None = None,
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
        kwargs: Dict[str, Any] = {"grammar": retry_grammar}
        if request_contract is not None and _accepts_keyword(
            session.build_legacy_request, "request_contract"
        ):
            kwargs["request_contract"] = request_contract
        request = session.build_legacy_request(retry_payload, **kwargs)
        if request_contract is not None and getattr(request, "request_contract", None) is None:
            try:
                request = replace(request, request_contract=request_contract)
            except TypeError:
                pass
        return cast(ModelRequest, request)
    return build_legacy_model_request(
        session,
        retry_payload,
        grammar=retry_grammar,
        request_contract=request_contract,
    )


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
    return normalize_model_decision(
        response,
        request_contract=getattr(request, "request_contract", None),
    )


def _accepts_keyword(callable_value: Any, name: str) -> bool:
    """Keep compatibility test doubles that predate the identity keyword."""

    try:
        parameters = inspect.signature(callable_value).parameters.values()
    except (TypeError, ValueError):
        return True
    return any(
        parameter.name == name or parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters
    )
