from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent.interaction.response import ResponseContextTooLarge, build_response_request_plan, complete_response
from agent.llm.contracts import ProviderCapabilities, StreamEvent, StreamEventType, StructuredOutputMode
from agent.runtime.request_measurement import PROVIDER_CHAT_INPUT_TOKENS, RequestInputMeasurement
from agent.runtime.task_directives import DeliberationProfile

from ._helpers import session


def _context(current_session):
    from agent.interaction.resolver import build_interaction_context

    return build_interaction_context(current_session)


def test_response_fitting_uses_bounded_prior_pairs_and_at_most_two_exact_probes() -> None:
    current_session, gateway = session(
        [],
        config={"max_tokens": 16},
    )
    current_session.messages = [{"role": "system", "content": "system"}]
    for index in range(10):
        current_session.messages.extend(
            [
                {"role": "user", "content": f"user {index}"},
                {"role": "assistant", "content": f"assistant {index}"},
            ]
        )
    current_session.hardware_profile = SimpleNamespace(context_limit=100)
    gateway.measurements = [
        RequestInputMeasurement(90, PROVIDER_CHAT_INPUT_TOKENS, exact=True, available=True),
        RequestInputMeasurement(10, PROVIDER_CHAT_INPUT_TOKENS, exact=True, available=True),
    ]
    before = [dict(item) for item in current_session.messages]
    plan = build_response_request_plan(
        current_session,
        _context(current_session),
        before,
        "current question",
        profile=DeliberationProfile.NORMAL,
    )
    assert plan.context_compacted is True
    assert plan.exact_probes == 2
    assert gateway.measure_calls == 2
    assert plan.request.messages[-1].content == "current question"
    assert current_session.messages == before


def test_response_context_that_cannot_fit_fails_without_truncating_current_text() -> None:
    current_session, gateway = session([], config={"max_tokens": 16})
    current_session.hardware_profile = SimpleNamespace(context_limit=100)
    gateway.measurements = [
        RequestInputMeasurement(100, PROVIDER_CHAT_INPUT_TOKENS, exact=True, available=True),
        RequestInputMeasurement(100, PROVIDER_CHAT_INPUT_TOKENS, exact=True, available=True),
    ]
    with pytest.raises(ResponseContextTooLarge):
        build_response_request_plan(
            current_session,
            _context(current_session),
            current_session.messages,
            "a very important current question",
            profile=DeliberationProfile.NORMAL,
        )
    assert gateway.measure_calls == 2


def test_streamed_response_preserves_exact_visible_whitespace() -> None:
    current_session, gateway = session(
        [],
        capabilities=ProviderCapabilities(
            streaming=True,
            structured_output_modes=(StructuredOutputMode.JSON_PROMPT,),
            reasoning=False,
        ),
    )
    context = _context(current_session)
    request = current_session.build_request(stream=True, max_output_tokens=8)
    gateway.stream = lambda _request: iter(
        [
            StreamEvent(StreamEventType.CONTENT, "  leading"),
            StreamEvent(StreamEventType.CONTENT, " and trailing  "),
        ]
    )
    chunks: list[str] = []
    assert complete_response(context, request, callback=chunks.append) == "  leading and trailing  "
    assert chunks == ["  leading", " and trailing  "]
