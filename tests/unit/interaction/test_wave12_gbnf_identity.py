from __future__ import annotations

from agent.interaction.model_contract import INTERACTION_RESOLUTION_GBNF
from agent.interaction.resolver import build_resolver_request
from agent.llm.contracts import ProviderCapabilities, StructuredOutputMode

from ._helpers import session


def test_resolver_uses_the_single_authoritative_gbnf_constant() -> None:
    current_session, _gateway = session(
        [],
        capabilities=ProviderCapabilities(
            streaming=False,
            structured_output_modes=(StructuredOutputMode.GBNF,),
        ),
    )
    request = build_resolver_request(current_session, boundary="natural", subject="hello")
    assert request.structured_output is not None
    assert request.structured_output.grammar is INTERACTION_RESOLUTION_GBNF
