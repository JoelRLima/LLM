from __future__ import annotations

import json

import pytest

from agent.interaction.intent_claim import (
    IntentClaimError,
    IntentClaimV1,
    bind_current_subject_evidence,
    parse_intent_claim,
)


def _claim(subject: str = "change DEFAULT_TIMEOUT") -> dict[str, object]:
    start = subject.index("DEFAULT_TIMEOUT")
    end = start + len("DEFAULT_TIMEOUT")
    return {
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
                "value": "DEFAULT_TIMEOUT",
                "role": "mutation_target",
                "evidence_span_ids": ["e1"],
            }
        ],
        "constraints": [],
        "evidence_spans": [
            {"span_id": "e1", "start": start, "end": end, "text": subject[start:end]}
        ],
    }


def test_valid_claim_is_strict_and_explicitly_untrusted() -> None:
    claim = parse_intent_claim(json.dumps(_claim()), subject="change DEFAULT_TIMEOUT")
    assert isinstance(claim, IntentClaimV1)
    assert claim.untrusted is True
    assert claim.effects[0].effect == "write"
    assert claim.selectors[0].kind == "symbol"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update(extra=True),
        lambda value: value["effects"][0].update(extra=True),
        lambda value: value["selectors"].append(value["selectors"][0]),
        lambda value: value["evidence_spans"][0].update(text="wrong"),
    ],
)
def test_claim_rejects_unexpected_or_inconsistent_fields(mutate) -> None:
    value = _claim()
    mutate(value)
    with pytest.raises(IntentClaimError):
        parse_intent_claim(json.dumps(value), subject="change DEFAULT_TIMEOUT")


def test_duplicate_keys_nonstandard_numbers_and_surrogates_fail_closed() -> None:
    value = json.dumps(_claim())[:-1] + ',"operation":"read"}'
    with pytest.raises(IntentClaimError):
        parse_intent_claim(value)
    with pytest.raises(IntentClaimError):
        parse_intent_claim(json.dumps(_claim()).replace('"effects": [', '"effects": [NaN,'))
    with pytest.raises(IntentClaimError):
        parse_intent_claim(json.dumps(_claim()).replace("DEFAULT_TIMEOUT", "\\ud800"))


def test_evidence_is_bound_to_exact_current_subject_without_fuzzy_matching() -> None:
    claim = parse_intent_claim(json.dumps(_claim()))
    with pytest.raises(IntentClaimError):
        bind_current_subject_evidence(claim, "please change DEFAULT_TIMEOUT")
