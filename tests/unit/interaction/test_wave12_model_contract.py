from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent.interaction.model_contract import (
    INTERACTION_RESOLUTION_GBNF,
    parse_interaction_resolution,
    validate_interaction_resolution,
    verify_interaction_request_contract,
)
from agent.llm.contracts import ProviderCapabilities, StructuredOutputMode
from agent.llm.decision_contract import ModelRequestContract
from agent.runtime.task_directives import TaskDirective

from ._helpers import decision, session


@pytest.mark.parametrize(
    "fields",
    [
        {},
        {"action": "clarify", "ambiguity": "effect", "grounding": "current_turn", "operation_requested": True, "evidence": "delete parser.py"},
        {"action": "clarify", "ambiguity": "continuation", "grounding": "current_turn", "resume_requested": True, "evidence": "resume the previous task"},
        {"action": "clarify", "ambiguity": "grounding", "grounding": "contextual", "evidence": ""},
        {"action": "clarify", "ambiguity": "conflict", "grounding": "current_turn", "operation_requested": True, "evidence": "conflicting effects"},
        {"action": "run", "directive": "read", "grounding": "current_turn", "evidence": "analyze parser.py"},
        {"action": "run", "directive": "plan", "grounding": "current_turn", "proposal_only": True, "evidence": "propose a plan"},
        {"action": "run", "directive": "do", "grounding": "current_turn", "operation_requested": True, "evidence": "delete parser.py"},
        {"action": "continue", "grounding": "current_turn", "resume_requested": True, "evidence": "resume the previous task"},
    ],
)
def test_contract_accepts_each_closed_valid_cell(fields: dict[str, object]) -> None:
    raw = {
        "action": "respond",
        "directive": "none",
        "ambiguity": "none",
        "grounding": "none",
        "operation_requested": False,
        "proposal_only": False,
        "resume_requested": False,
        "evidence": "",
    }
    raw.update(fields)
    parsed = validate_interaction_resolution(raw)
    assert parsed.to_dict()["action"] == raw["action"]


@pytest.mark.parametrize(
    "raw",
    [
        {"action": "respond"},
        {"action": "respond", "directive": "none", "ambiguity": "none", "grounding": "none", "operation_requested": False, "proposal_only": False, "resume_requested": False, "evidence": "", "extra": 1},
        {"action": "respond", "directive": "none", "ambiguity": "none", "grounding": "none", "operation_requested": 0, "proposal_only": False, "resume_requested": False, "evidence": ""},
        {"action": "run", "directive": "auto", "ambiguity": "none", "grounding": "current_turn", "operation_requested": False, "proposal_only": False, "resume_requested": False, "evidence": "x"},
        {"action": "explode", "directive": "none", "ambiguity": "none", "grounding": "none", "operation_requested": False, "proposal_only": False, "resume_requested": False, "evidence": ""},
        {"action": "run", "directive": "do", "ambiguity": "none", "grounding": "current_turn", "operation_requested": False, "proposal_only": False, "resume_requested": False, "evidence": "x"},
        {"action": "clarify", "directive": "none", "ambiguity": "effect", "grounding": "current_turn", "operation_requested": False, "proposal_only": False, "resume_requested": False, "evidence": "x"},
        {"action": "run", "directive": "read", "ambiguity": "none", "grounding": "current_turn", "operation_requested": False, "proposal_only": False, "resume_requested": False, "evidence": ""},
    ],
)
def test_contract_rejects_invalid_cells(raw: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        validate_interaction_resolution(raw)


def test_request_contract_mismatch_is_explicit() -> None:
    request = SimpleNamespace(request_contract=ModelRequestContract.TASK_CONTRACT)
    with pytest.raises(ValueError, match="INTERACTION_REQUEST_CONTRACT_MISMATCH"):
        verify_interaction_request_contract(request)


def test_resolver_grammar_is_authoritative_and_not_regenerated() -> None:
    capabilities = ProviderCapabilities(
        streaming=False,
        structured_output_modes=(StructuredOutputMode.GBNF,),
        reasoning=False,
    )
    current_session, _gateway = session(capabilities=capabilities)
    from agent.interaction.resolver import build_resolver_request

    request = build_resolver_request(current_session, boundary="natural", subject="hello")
    assert request.request_contract is ModelRequestContract.INTERACTION_RESOLUTION
    assert request.structured_output is not None
    assert request.structured_output.mode is StructuredOutputMode.GBNF
    assert request.structured_output.grammar == INTERACTION_RESOLUTION_GBNF


def test_parser_returns_typed_directive_without_auto() -> None:
    parsed = parse_interaction_resolution(decision(action="run", directive="do", grounding="current_turn", operation_requested=True, evidence="delete parser.py"))
    assert parsed.directive is TaskDirective.DO
