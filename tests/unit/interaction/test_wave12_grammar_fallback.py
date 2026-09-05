from __future__ import annotations

from agent.interaction.resolver import InteractionResolver, select_interaction_structured_output
from agent.llm.contracts import ProviderCapabilities, StructuredOutputMode
from agent.llm.errors import UnsupportedModelCapability

from ._helpers import session


def test_unsupported_gbnf_is_cached_without_a_same_turn_retry() -> None:
    current_session, gateway = session(
        [],
        capabilities=ProviderCapabilities(
            streaming=False,
            structured_output_modes=(StructuredOutputMode.GBNF,),
        ),
    )

    attempts = []

    def fail(_request):
        attempts.append(1)
        raise UnsupportedModelCapability("grammar unsupported")

    gateway.complete = fail
    resolver = InteractionResolver(current_session)
    try:
        resolver.resolve(boundary="natural", subject="hello", snapshot=current_session.messages)
    except RuntimeError:
        pass
    else:
        raise AssertionError("unsupported grammar did not fail closed")
    assert attempts == [1]
    assert current_session._grammar_supports_grammar is False
    assert select_interaction_structured_output(current_session).mode is StructuredOutputMode.JSON_PROMPT
