from __future__ import annotations

import pytest

from agent.interaction.model_contract import parse_interaction_resolution

from ._helpers import decision


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_nonstandard_json_constants_are_rejected(constant: str) -> None:
    raw = decision()[:-1] + f',"evidence":{constant}}}'
    with pytest.raises(ValueError):
        parse_interaction_resolution(raw)


@pytest.mark.parametrize("key", ["action", "directive", "evidence"])
def test_duplicate_keys_are_rejected_at_parse_boundary(key: str) -> None:
    raw = decision()[:-1] + f',"{key}":"none"}}'
    with pytest.raises(ValueError):
        parse_interaction_resolution(raw)


@pytest.mark.parametrize(
    "raw",
    [
        "```json\n" + decision() + "\n```",
        "prefix " + decision(),
        decision() + "\n" + decision(),
        "[" + decision() + "]",
    ],
)
def test_parser_requires_one_whole_json_object(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_interaction_resolution(raw)


def test_escaped_text_and_unicode_evidence_reach_typed_validation() -> None:
    raw = decision(
        action="clarify",
        ambiguity="effect",
        grounding="current_turn",
        operation_requested=True,
        evidence='delete "parser.py" \\ now',
    )
    parsed = parse_interaction_resolution(raw)
    assert parsed.evidence == 'delete "parser.py" \\ now'


def test_unicode_surrogate_is_rejected_recursively() -> None:
    raw = r'{"action":"clarify","directive":"none","ambiguity":"grounding","grounding":"contextual","operation_requested":false,"proposal_only":false,"resume_requested":false,"evidence":"\ud800"}'
    with pytest.raises(ValueError):
        parse_interaction_resolution(raw)
