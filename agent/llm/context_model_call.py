"""Model-call orchestration kept separate from context state management."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Any, Dict

from agent.llm.admitted_decisions import ModelDecisionValue
from agent.llm.decision_compat import (
    build_request_with_contract,
    build_retry_request,
    record_legacy_metadata,
)
from agent.llm.decision_contract import ModelRequestContract, resolve_request_contract
from agent.llm.grammars import AutoGrammar, get_grammar
from agent.llm.structured_output import resolve_model_decision


def run_model_call(
    manager: Any,
    prompt: str,
    *,
    step_type: str,
    base_prompt: str | None,
    log_metric_callback: Callable[[Dict[str, Any]], None] | None,
    grammar: str | None | AutoGrammar,
    request_contract: ModelRequestContract | str | None,
    typed: bool,
    step_budgets: Mapping[str, int],
    default_max_tokens: int,
) -> Dict[str, Any] | ModelDecisionValue:
    exact_contract = resolve_request_contract(
        request_contract=request_contract,
        step_type=step_type,
    )
    effective_grammar = _resolve_grammar(
        manager,
        grammar,
        step_type,
        request_contract,
        exact_contract,
    )
    original_messages = [message.copy() for message in manager.session.messages]
    original_system_content = (
        manager.session.messages[0]["content"] if manager.session.messages else ""
    )
    if manager.verbose:
        manager.check_prompt_size()
    try:
        context_addition = manager.build_context(prompt)
        if base_prompt is None:
            base_prompt = manager.build_base_system_prompt("", "")
        manager.session.messages[0]["content"] = base_prompt + context_addition
        manager.session.add_user_message(prompt)
        estimated = manager.estimate_conversation_tokens()
        config_max = manager.session.config.get("agent_max_tokens")
        budget = config_max if config_max is not None else min(
            step_budgets.get(step_type, default_max_tokens),
            manager.hardware_profile.default_output_tokens,
        )
        grammar_for_request = _supported_grammar(manager, effective_grammar)
        request = _build_request(
            manager,
            estimated,
            grammar_for_request,
            int(budget),
            exact_contract,
        )
        if manager.verbose:
            print(
                f"â³ Consultando o modelo (step={step_type}, budget={budget})...",
                end="",
                flush=True,
            )
        started_at = time.monotonic()
        return resolve_model_decision(
            request,
            complete=manager.session.complete_request,
            retry_request=lambda: build_retry_request(
                manager.session,
                effective_grammar,
                manager.hardware_profile,
                default_max_tokens,
                exact_contract,
            ),
            grammar=grammar_for_request,
            grammar_supported=getattr(
                manager.session, "_grammar_supports_grammar", None
            ),
            set_grammar_supported=lambda value: setattr(
                manager.session, "_grammar_supports_grammar", value
            ),
            fallback_request=lambda current: replace(
                current, structured_output=None
            ),
            retry_authorizer=manager._authorize_structured_response_repair,
            step_type=step_type,
            request_contract=(
                request_contract if request_contract is not None else exact_contract
            ),
            typed=typed,
            on_initial_response=lambda response, parsed, active_request: record_legacy_metadata(
                log_metric_callback,
                response,
                parsed,
                active_request,
                step_type,
                started_at,
                request_contract=(
                    request_contract if request_contract is not None else exact_contract
                ),
            ),
        )
    finally:
        manager.session.messages = original_messages
        if manager.session.messages:
            manager.session.messages[0]["content"] = original_system_content


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
            request = build_request_with_contract(
                manager.session,
                grammar=grammar,
                stream=False,
                max_output_tokens=budget,
                request_contract=contract,
            )
        finally:
            manager.session.messages = original_messages
        return replace(request, context_compacted=True)
    return build_request_with_contract(
        manager.session,
        grammar=grammar,
        stream=False,
        max_output_tokens=budget,
        request_contract=contract,
    )
