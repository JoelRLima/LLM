"""Model-call orchestration kept separate from context state management."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Any, Dict

from agent.llm.admitted_decisions import ModelDecisionValue
from agent.llm.context_model_request_support import (
    _resolve_grammar,
    _retry_fit_request,
    _supported_grammar,
)
from agent.llm.context_projection import (
    ContextFitError,
    fit_contextual_request,
    fixed_untrusted_data_policy,
)
from agent.llm.decision_contract import ModelRequestContract, resolve_request_contract
from agent.llm.grammars import AutoGrammar
from agent.llm.structured_output import resolve_model_decision


def _apply_projection(
    manager: Any,
    base_messages: list[dict[str, Any]],
    required_message: str | None,
    optional_message: str | None,
) -> None:
    messages = [message.copy() for message in base_messages]
    data_messages = [
        {"role": "user", "content": content}
        for content in (required_message, optional_message)
        if content
    ]
    insertion = len(messages)
    if messages and messages[-1].get("role") == "user":
        insertion -= 1
    messages[insertion:insertion] = data_messages
    manager.session.messages = messages


def _build_attempt(
    manager: Any,
    base_messages: list[dict[str, Any]],
    required_message: str | None,
    optional_message: str | None,
    max_output_tokens: int,
    effective_grammar: str | None,
    exact_contract: ModelRequestContract | None,
) -> Any:
    _apply_projection(manager, base_messages, required_message, optional_message)
    return manager.session.build_request(
        grammar=_supported_grammar(manager, effective_grammar),
        stream=False,
        max_output_tokens=max_output_tokens,
        request_contract=exact_contract,
    )


def _prepare_attempt(
    manager: Any,
    max_output_tokens: int,
    base_messages: list[dict[str, Any]],
    optional_records: tuple[Any, ...],
    effective_grammar: str | None,
    exact_contract: ModelRequestContract | None,
) -> Any:
    _apply_projection(manager, base_messages, None, None)
    mandatory_request = manager.session.build_request(
        grammar=_supported_grammar(manager, effective_grammar),
        stream=False,
        max_output_tokens=max_output_tokens,
        request_contract=exact_contract,
    )
    context_limit = getattr(mandatory_request, "context_limit", None)
    if not isinstance(context_limit, int) or context_limit <= 0:
        context_limit = getattr(manager.hardware_profile, "context_limit", None)
    fit = fit_contextual_request(
        mandatory_request=mandatory_request,
        optional_records=optional_records,
        context_limit=context_limit,
        gateway=getattr(manager.session, "gateway", None),
        build_request=lambda required, optional: _build_attempt(
            manager,
            base_messages,
            required,
            optional,
            max_output_tokens,
            effective_grammar,
            exact_contract,
        ),
    )
    _apply_projection(
        manager,
        base_messages,
        fit.projection.required_evidence_message,
        fit.projection.optional_auxiliary_message,
    )
    recorder = getattr(manager, "record_context_projection", None)
    if callable(recorder):
        recorder(fit.projection)
    return fit


def _prepare_model_state(
    manager: Any,
    prompt: str,
    base_prompt: str | None,
    include_task_definition: bool,
    step_type: str,
    effective_grammar: str | None,
    exact_contract: ModelRequestContract | None,
    step_budgets: Mapping[str, int],
    default_max_tokens: int,
) -> tuple[int, str | None, list[dict[str, Any]], tuple[Any, ...]]:
    if base_prompt is None:
        base_prompt = manager.build_base_system_prompt("", "")
    system_content = base_prompt
    fixed_policy = fixed_untrusted_data_policy()
    if fixed_policy not in system_content:
        system_content = system_content + "\n\n" + fixed_policy
    if include_task_definition:
        trusted_builder = getattr(manager, "build_trusted_task_context", None)
        if callable(trusted_builder):
            trusted_context = str(trusted_builder() or "")
            if trusted_context:
                system_content = system_content + "\n\n" + trusted_context
    manager.session.messages[0]["content"] = system_content
    manager.session.add_user_message(prompt)
    config_max = manager.session.config.get("agent_max_tokens")
    budget = config_max if config_max is not None else min(
        step_budgets.get(step_type, default_max_tokens),
        manager.hardware_profile.default_output_tokens,
    )
    grammar_for_request = _supported_grammar(manager, effective_grammar)
    base_messages = [message.copy() for message in manager.session.messages]
    optional_builder = getattr(manager, "build_auxiliary_records", None)
    optional_records = (
        tuple(optional_builder(prompt))
        if callable(optional_builder)
        else ()
    )
    return int(budget), grammar_for_request, base_messages, optional_records


def _resolve_fitted_request(
    manager: Any,
    request: Any,
    *,
    prepare_attempt: Callable[[int], Any],
    effective_grammar: str | None,
    grammar_for_request: str | None,
    default_max_tokens: int,
    exact_contract: ModelRequestContract | None,
    step_type: str,
    request_contract: ModelRequestContract | str | None,
    typed: bool,
) -> Dict[str, Any] | ModelDecisionValue:
    return resolve_model_decision(
        request,
        complete=manager.session.complete_request,
        retry_request=lambda: _retry_fit_request(
            prepare_attempt,
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
        budget, grammar_for_request, base_messages, optional_records = _prepare_model_state(
            manager,
            prompt,
            base_prompt,
            include_task_definition,
            step_type,
            effective_grammar,
            exact_contract,
            step_budgets,
            default_max_tokens,
        )

        def prepare_attempt(max_output_tokens: int) -> Any:
            return _prepare_attempt(
                manager,
                max_output_tokens,
                base_messages,
                optional_records,
                effective_grammar,
                exact_contract,
            )
        fit = prepare_attempt(int(budget))
        if fit.mandatory_overflow:
            return {
                "action": "error",
                "error_code": ContextFitError.code,
                "message": "O conteúdo obrigatório excede o limite de contexto; nenhuma chamada foi enviada.",
            }
        request = fit.request
        if manager.verbose:
            print(
                f"â³ Consultando o modelo (step={step_type}, budget={budget})...",
                end="",
                flush=True,
            )
        return _resolve_fitted_request(
            manager,
            request,
            prepare_attempt=prepare_attempt,
            effective_grammar=effective_grammar,
            grammar_for_request=grammar_for_request,
            default_max_tokens=default_max_tokens,
            exact_contract=exact_contract,
            step_type=step_type,
            request_contract=request_contract,
            typed=typed,
        )
    except ContextFitError as exc:
        return {
            "action": "error",
            "error_code": exc.code,
            "message": str(exc),
        }
    finally:
        manager.session.messages = original_messages
        if manager.session.messages:
            manager.session.messages[0]["content"] = original_system_content
