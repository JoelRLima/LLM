"""Request-system and stage-context preparation for model calls."""

from __future__ import annotations

from typing import Any

from llm_agent.agent.llm.context_model_request_support import _supported_grammar
from llm_agent.agent.llm.context_projection import fixed_untrusted_data_policy
from llm_agent.agent.llm.decision_contract import ModelRequestContract
from llm_agent.agent.llm.stage_context import emit_stage_context_selection, project_stage_records


def _prepare_model_state(
    manager: Any,
    prompt: str,
    base_prompt: str | None,
    include_task_definition: bool,
    effective_grammar: str | None,
    exact_contract: ModelRequestContract | None,
    selected_step_budget: int,
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
        selected_step_budget,
        manager.hardware_profile.default_output_tokens,
    )
    grammar_for_request = _supported_grammar(manager, effective_grammar)
    base_messages = [message.copy() for message in manager.session.messages]
    optional_builder = getattr(manager, "build_auxiliary_records", None)
    canonical_optional_records = (
        tuple(optional_builder(prompt))
        if callable(optional_builder)
        else ()
    )
    selection = project_stage_records(
        manager.session.config,
        exact_contract,
        canonical_optional_records,
    )
    optional_records = selection.records
    emit_stage_context_selection(manager, selection, contract=exact_contract)
    return int(budget), grammar_for_request, base_messages, optional_records
