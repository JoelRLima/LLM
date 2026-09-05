from __future__ import annotations

from agent.interaction.resolver import build_interaction_context, build_resolver_request
from agent.llm.decision_contract import ModelRequestContract

from ._helpers import session


def test_resolver_context_is_fresh_and_does_not_alias_session_owners() -> None:
    current_session, gateway = session([])
    before_ledger = current_session.budget_ledger
    context = build_interaction_context(current_session)
    assert context.model_gateway is gateway
    assert context.model_profile is current_session.model_profile
    assert context.budget_ledger is not before_ledger
    assert context.cancellation is not current_session.cancellation_token
    assert context.policy_state is not None
    assert context.task_policy is not None
    assert context.task_policy is not current_session.task_policy
    assert context.correlation is not None
    assert context.task_id is not None
    assert context.budget_ledger.snapshot().model_calls == 0
    assert context.budget_ledger.snapshot().tool_calls == 0


def test_resolver_request_uses_exact_configured_gateway_profile_and_contract() -> None:
    current_session, _gateway = session([])
    request = build_resolver_request(
        current_session,
        boundary="natural",
        subject="What is an AST?",
    )
    assert request.model == current_session.model_profile.model
    assert request.temperature == 0
    assert request.stream is False
    assert request.request_contract is ModelRequestContract.INTERACTION_RESOLUTION
    assert request.provider_options == {}
    assert request.messages[0].role == "system"
    assert request.messages[1].role == "user"
