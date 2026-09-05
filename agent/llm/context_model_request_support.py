"""Request-building helpers for context-aware model calls."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Any

from agent.llm.context_projection import ContextFitError
from agent.llm.decision_contract import ModelRequestContract
from agent.llm.grammars import AutoGrammar, get_grammar


def _resolve_grammar(
    manager: Any,
    grammar: str | None | AutoGrammar,
    step_type: str,
    request_contract: ModelRequestContract | str | None,
    exact_contract: ModelRequestContract | None,
) -> str | None:
    if not isinstance(grammar, AutoGrammar):
        return grammar
    return get_grammar(
        step_type,
        manager.session.config,
        request_contract=(
            request_contract if request_contract is not None else exact_contract
        ),
    )


def _supported_grammar(manager: Any, grammar: str | None) -> str | None:
    if getattr(manager.session, "_grammar_supports_grammar", None) is False:
        return None
    return grammar


def _build_request(
    manager: Any,
    estimated: int,
    grammar: str | None,
    budget: int,
    contract: ModelRequestContract | None,
) -> Any:
    if estimated > int(manager.hardware_profile.context_limit * 0.75):
        compact_messages = manager.build_compact_view()
        original_messages = manager.session.messages
        manager.session.messages = compact_messages
        try:
            request = manager.session.build_request(
                grammar=grammar,
                stream=False,
                max_output_tokens=budget,
                request_contract=contract,
            )
        finally:
            manager.session.messages = original_messages
        return replace(request, context_compacted=True)
    return manager.session.build_request(
        grammar=grammar,
        stream=False,
        max_output_tokens=budget,
        request_contract=contract,
    )


def _build_retry_request(
    session: Any,
    effective_grammar: str | None,
    hardware_profile: Any,
    default_max_tokens: int,
    request_contract: ModelRequestContract | str | None = None,
) -> Any:
    retry_grammar = (
        effective_grammar
        if getattr(session, "_grammar_supports_grammar", None) is not False
        else None
    )
    config = getattr(session, "config", {})
    configured = config.get("agent_max_tokens") if isinstance(config, Mapping) else None
    if isinstance(configured, int) and not isinstance(configured, bool) and configured > 0:
        retry_budget = configured
    else:
        profile_limit = getattr(hardware_profile, "default_output_tokens", None)
        if (
            not isinstance(profile_limit, int)
            or isinstance(profile_limit, bool)
            or profile_limit <= 0
        ):
            profile_limit = default_max_tokens * 2
        retry_budget = min(default_max_tokens * 2, profile_limit)
    return session.build_request(
        grammar=retry_grammar,
        stream=False,
        max_output_tokens=int(retry_budget),
        request_contract=request_contract,
    )


def _retry_fit_request(
    prepare_attempt: Callable[[int], Any],
    session: Any,
    effective_grammar: str | None,
    hardware_profile: Any,
    default_max_tokens: int,
    request_contract: ModelRequestContract | str | None,
) -> Any:
    """Rebuild and refit a structured-response retry with its own reserve."""

    # Keep the retry reserve calculation aligned with the historical owner;
    # the fit itself is intentionally recomputed by ``prepare_attempt``.
    config = getattr(session, "config", {})
    configured = config.get("agent_max_tokens") if isinstance(config, Mapping) else None
    if isinstance(configured, int) and not isinstance(configured, bool) and configured > 0:
        retry_budget = configured
    else:
        profile_limit = getattr(hardware_profile, "default_output_tokens", None)
        if (
            not isinstance(profile_limit, int)
            or isinstance(profile_limit, bool)
            or profile_limit <= 0
        ):
            profile_limit = default_max_tokens * 2
        retry_budget = min(default_max_tokens * 2, profile_limit)
    fit = prepare_attempt(int(retry_budget))
    if getattr(fit, "mandatory_overflow", False):
        raise ContextFitError(
            "O conteúdo obrigatório não cabe no retry com o reserve efetivo."
        )
    return fit.request

__all__ = [
    "_build_request",
    "_build_retry_request",
    "_resolve_grammar",
    "_retry_fit_request",
    "_supported_grammar",
]
