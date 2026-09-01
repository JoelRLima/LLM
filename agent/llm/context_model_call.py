"""Model-call orchestration kept separate from context state management."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Any, Dict

from agent.llm.admitted_decisions import ModelDecisionValue
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
    include_task_definition: bool,
    step_budgets: Mapping[str, int],
    default_max_tokens: int,
) -> Dict[str, Any] | ModelDecisionValue:
    if log_metric_callback is not None and getattr(
        manager.session, "model_call_callback", None
    ) is None:
        manager.session.set_model_call_callback(log_metric_callback)
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
        if include_task_definition:
            trusted_builder = getattr(manager, 'build_trusted_task_context', None)
            if callable(trusted_builder):
                trusted_context = str(trusted_builder() or '')
                if trusted_context:
                    context_addition = trusted_context + '\n\n' + context_addition
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
        return resolve_model_decision(
            request,
            complete=manager.session.complete_request,
            retry_request=lambda: _build_retry_request(
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
