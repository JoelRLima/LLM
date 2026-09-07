from __future__ import annotations

import json

import pytest

from agent.interaction.resolver import InteractionResolver, ResolverInvalid
from agent.interaction.semantic_contract import (
    SemanticInteractionParseError,
    parse_semantic_interaction_resolution,
)

from ._helpers import decision, session


def _semantic_response(subject: str = "Could you adjust DEFAULT_TIMEOUT gently?") -> str:
    value = "DEFAULT_TIMEOUT"
    start = subject.index(value)
    claim = {
        "schema_version": "intent-claim-v1",
        "operation": "do",
        "ambiguity": "none",
        "effects": [
            {
                "effect": "write",
                "polarity": "requested",
                "selector_ids": ["s1"],
                "evidence_span_ids": ["e1"],
            }
        ],
        "selectors": [
            {
                "selector_id": "s1",
                "kind": "symbol",
                "value": value,
                "role": "mutation_target",
                "evidence_span_ids": ["e1"],
            }
        ],
        "constraints": [],
        "evidence_spans": [
            {"span_id": "e1", "start": start, "end": start + len(value), "text": value}
        ],
    }
    return json.dumps(
        {
            "action": "run",
            "directive": "do",
            "ambiguity": "none",
            "grounding": "current_turn",
            "operation_requested": True,
            "proposal_only": False,
            "resume_requested": False,
            "evidence": value,
            "intent_claim": claim,
        }
    )


def test_semantic_run_without_claim_is_rejected_instead_of_legacy_projected() -> None:
    with pytest.raises(SemanticInteractionParseError):
        parse_semantic_interaction_resolution(
            decision(
                action="run",
                directive="do",
                grounding="current_turn",
                operation_requested=True,
                evidence="change DEFAULT_TIMEOUT",
            )
        )


@pytest.mark.parametrize("raw", ["change DEFAULT_TIMEOUT", "{\"action\":\"run\"}"])
def test_invalid_semantic_response_has_no_legacy_authority_retry(raw: str) -> None:
    current_session, gateway = session([raw])
    resolver = InteractionResolver(current_session)

    with pytest.raises(ResolverInvalid):
        resolver.resolve(
            boundary="natural",
            subject="Could you adjust DEFAULT_TIMEOUT?",
            snapshot=current_session.messages,
            semantic=True,
        )

    assert len(gateway.calls) == 1
    assert all(request.request_contract_id == "semantic_intent_v1" for request in gateway.calls)


def test_unseen_valid_paraphrase_uses_semantic_claim_without_verb_table() -> None:
    subject = "Could you adjust DEFAULT_TIMEOUT gently?"
    current_session, gateway = session([_semantic_response(subject)])
    outcome = InteractionResolver(current_session).resolve(
        boundary="natural",
        subject=subject,
        snapshot=current_session.messages,
        semantic=True,
    )

    assert gateway.calls[0].request_contract_id == "semantic_intent_v1"
    assert outcome.decision.intent_claim is not None
    assert outcome.decision.intent_claim.selectors[0].value == "DEFAULT_TIMEOUT"


def test_explicit_legacy_command_surface_remains_available() -> None:
    current_session, gateway = session(
        [
            decision(
                action="run",
                directive="do",
                grounding="current_turn",
                operation_requested=True,
                evidence="change DEFAULT_TIMEOUT",
            )
        ]
    )
    outcome = InteractionResolver(current_session).resolve(
        boundary="task",
        subject="change DEFAULT_TIMEOUT",
        snapshot=current_session.messages,
        semantic=False,
    )

    assert outcome.decision.action.value == "run"
    assert gateway.calls[0].request_contract_id == "interaction_resolution"
