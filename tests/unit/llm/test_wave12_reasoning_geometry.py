from __future__ import annotations

from agent.llm.contracts import ProviderCapabilities
from agent.llm.session import ChatSession
from agent.llm.session_requests import (
    build_effective_system_prompt_for_budget,
    build_model_request,
    resolve_effective_reasoning_budget,
)
from tests.unit.interaction._helpers import FakeGateway


def test_shared_geometry_matches_frozen_examples() -> None:
    assert resolve_effective_reasoning_budget(2048, 2048, True) == 1792
    assert resolve_effective_reasoning_budget(2048, 1024, True) == 768
    assert resolve_effective_reasoning_budget(1024, 512, True) == 384
    assert resolve_effective_reasoning_budget(2048, 2048, False) == 0


def test_session_request_prompt_and_field_share_effective_integer() -> None:
    capabilities = ProviderCapabilities(reasoning=True, streaming=False)
    gateway = FakeGateway([], capabilities=capabilities)
    session = ChatSession("base", {"max_tokens": 1024}, gateway=gateway)
    session.thinking_budget = 2048
    request = build_model_request(session, stream=False)
    assert request.reasoning_budget == 768
    assert build_effective_system_prompt_for_budget("base", 768) == request.messages[0].content
    assert request.reasoning_budget < request.max_output_tokens
