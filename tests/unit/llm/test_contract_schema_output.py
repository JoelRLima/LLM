from __future__ import annotations

import pytest

from llm_agent.agent.llm.contracts import (
    ProviderCapabilities,
    StructuredOutputMode,
)
from llm_agent.agent.llm.decision_contract import (
    ModelRequestContract,
    admit_model_decision_value,
)
from llm_agent.agent.llm.providers.openai_compatible import OpenAICompatibleGateway
from llm_agent.agent.llm.session import ChatSession
from llm_agent.agent.llm.session_requests import build_model_request
from llm_agent.agent.llm.structured_output import validate_json_schema
from tests.unit.interaction._helpers import FakeGateway


@pytest.mark.parametrize(
    ("contract", "accepted", "rejected"),
    [
        (ModelRequestContract.FINAL_GENERATION, {"answer": "ok"}, {"answer": 3}),
        (ModelRequestContract.SUMMARIZATION, {"summary": "ok"}, {"summary": "ok", "extra": True}),
    ],
)
def test_native_contract_schema_matches_admission(
    contract: ModelRequestContract,
    accepted: dict[str, object],
    rejected: dict[str, object],
) -> None:
    capabilities = ProviderCapabilities(
        structured_output_modes=(StructuredOutputMode.JSON_SCHEMA,)
    )
    session = ChatSession("base", {"max_tokens": 177}, gateway=FakeGateway([], capabilities=capabilities))
    request = build_model_request(session, stream=False, request_contract=contract)
    assert request.structured_output is not None
    schema = request.structured_output.schema
    assert schema is not None
    payload = OpenAICompatibleGateway(session.model_profile).build_payload(request)
    assert payload["response_format"]["type"] == "json_schema"
    assert payload["response_format"]["json_schema"]["schema"] == schema
    assert payload["response_format"]["json_schema"]["strict"] is True
    validate_json_schema(accepted, schema)
    assert admit_model_decision_value(accepted, request_contract=contract) == accepted
    with pytest.raises(ValueError):
        validate_json_schema(rejected, schema)
    assert admit_model_decision_value(rejected, request_contract=contract) is None


def test_grammar_keeps_precedence_and_budget_geometry() -> None:
    capabilities = ProviderCapabilities(
        structured_output_modes=(StructuredOutputMode.GBNF, StructuredOutputMode.JSON_SCHEMA)
    )
    session = ChatSession("base", {"max_tokens": 177}, gateway=FakeGateway([], capabilities=capabilities))
    request = build_model_request(
        session,
        grammar='root ::= "{}"',
        stream=False,
        max_output_tokens=123,
        request_contract=ModelRequestContract.FINAL_GENERATION,
    )
    assert request.structured_output is not None
    assert request.structured_output.mode is StructuredOutputMode.GBNF
    assert request.max_output_tokens == 123


def test_cached_grammar_rejection_uses_declared_schema_for_mapped_contract() -> None:
    capabilities = ProviderCapabilities(
        structured_output_modes=(StructuredOutputMode.GBNF, StructuredOutputMode.JSON_SCHEMA)
    )
    session = ChatSession("base", {"max_tokens": 177}, gateway=FakeGateway([], capabilities=capabilities))
    session._grammar_supports_grammar = False
    request = build_model_request(
        session,
        grammar='root ::= "{}"',
        stream=False,
        request_contract=ModelRequestContract.FINAL_GENERATION,
    )
    assert request.structured_output is not None
    assert request.structured_output.mode is StructuredOutputMode.JSON_SCHEMA


def test_cached_grammar_rejection_stays_disabled_for_unmapped_contract() -> None:
    capabilities = ProviderCapabilities(
        structured_output_modes=(StructuredOutputMode.GBNF, StructuredOutputMode.JSON_PROMPT)
    )
    session = ChatSession("base", {"max_tokens": 177}, gateway=FakeGateway([], capabilities=capabilities))
    session._grammar_supports_grammar = False
    request = build_model_request(
        session,
        grammar='root ::= "{}"',
        stream=False,
        request_contract=ModelRequestContract.REACTIVE_TOOL_DECISION,
    )
    assert request.structured_output is None


def test_non_schema_capability_falls_back_without_contract_schema() -> None:
    capabilities = ProviderCapabilities(
        structured_output_modes=(StructuredOutputMode.JSON_PROMPT,)
    )
    session = ChatSession("base", {"max_tokens": 177}, gateway=FakeGateway([], capabilities=capabilities))
    request = build_model_request(
        session,
        grammar='root ::= "{}"',
        stream=False,
        request_contract=ModelRequestContract.REACTIVE_TOOL_DECISION,
    )
    assert request.structured_output is None
    assert request.request_contract is ModelRequestContract.REACTIVE_TOOL_DECISION


def test_ephemeral_request_preserves_messages_and_schema_selection() -> None:
    capabilities = ProviderCapabilities(
        structured_output_modes=(StructuredOutputMode.JSON_SCHEMA,)
    )
    session = ChatSession("base", {"max_tokens": 177}, gateway=FakeGateway([], capabilities=capabilities))
    from llm_agent.agent.llm.contracts import ModelMessage

    messages = (ModelMessage("system", "ephemeral"), ModelMessage("user", "only here"))
    request = session.build_ephemeral_request(
        messages,
        stream=False,
        max_output_tokens=99,
        request_contract=ModelRequestContract.FINAL_GENERATION,
    )
    assert request.structured_output is not None
    assert request.structured_output.mode is StructuredOutputMode.JSON_SCHEMA
    assert request.max_output_tokens == 99
    assert tuple(message.content for message in request.messages) == ("ephemeral", "only here")
    assert session.messages == [{"role": "system", "content": "base"}]
