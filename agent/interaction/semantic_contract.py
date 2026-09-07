"""W14 semantic interaction contract layered on the W12 resolver owner."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from agent.llm.decision_contract import ModelRequestContract, coerce_request_contract

from .intent_claim import (
    INTENT_CLAIM_SCHEMA,
    IntentClaimError,
    IntentClaimV1,
    parse_intent_claim,
)
from .model_contract import (
    INTERACTION_RESOLUTION_KEYS,
    INTERACTION_RESOLUTION_SCHEMA,
    reject_nonstandard_constant,
    reject_unicode_surrogates,
    validate_interaction_resolution,
)
from .types import InteractionModelDecision

SEMANTIC_INTERACTION_KEYS = frozenset((*INTERACTION_RESOLUTION_KEYS, "intent_claim"))
SEMANTIC_INTERACTION_SCHEMA: dict[str, Any] = deepcopy(INTERACTION_RESOLUTION_SCHEMA)
SEMANTIC_INTERACTION_SCHEMA["required"] = [*INTERACTION_RESOLUTION_SCHEMA["required"], "intent_claim"]
SEMANTIC_INTERACTION_SCHEMA["properties"]["intent_claim"] = {
    "anyOf": [deepcopy(INTENT_CLAIM_SCHEMA), {"type": "null"}]
}

# The JSON prompt remains deliberately explicit.  The strict Python parser is
# the authority in JSON Schema, GBNF, and JSON-prompt modes alike.
SEMANTIC_RESOLVER_JSON_INSTRUCTION = (
    "Return exactly one JSON object with the eight W12 routing keys plus "
    "intent_claim. intent_claim is null only for respond/continue; for a fresh "
    "run it must be a complete intent-claim-v1 object. Do not add keys."
)
# The outer response contains W12 routing fields plus a nested claim.  The
# strict Python parser remains authoritative in every structured-output mode.
SEMANTIC_RESOLVER_GBNF = r'''root ::= ws object ws
object ::= "{" ws (members)? ws "}"
members ::= string ws ":" ws value (ws "," ws string ws ":" ws value)*
value ::= string | number | array | object | "true" | "false" | "null"
array ::= "[" ws (value (ws "," ws value)*)? ws "]"
number ::= "0" | [1-9][0-9]*
string ::= "\"" chars "\""
chars ::= char*
char ::= [^"\\\\\x00-\x1F] | escape
escape ::= "\\\\" (["\\\\/bfnrt] | "u" hex hex hex hex)
hex ::= [0-9a-fA-F]
ws ::= [ \\t\\n\\r]*'''


class SemanticInteractionParseError(ValueError):
    """Strict semantic response failure."""


def _duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SemanticInteractionParseError("duplicate JSON key")
        result[key] = value
    return result


def _decode_semantic_json(raw: str) -> dict[str, Any]:
    if type(raw) is not str:
        raise SemanticInteractionParseError("semantic response is not text")
    try:
        value = json.loads(
            raw.strip(),
            object_pairs_hook=_duplicate_keys,
            parse_constant=reject_nonstandard_constant,
        )
    except SemanticInteractionParseError:
        raise
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SemanticInteractionParseError("semantic response is not strict JSON") from exc
    if not isinstance(value, dict):
        raise SemanticInteractionParseError("semantic response must be an object")
    reject_unicode_surrogates(value)
    return value


def _validate_outer(value: dict[str, Any]) -> InteractionModelDecision:
    try:
        return validate_interaction_resolution(value)
    except ValueError as exc:
        raise SemanticInteractionParseError(str(exc)) from exc


def _parse_claim(value: Any) -> IntentClaimV1 | None:
    if value is None:
        return None
    try:
        return parse_intent_claim(value)
    except IntentClaimError as exc:
        raise SemanticInteractionParseError(str(exc)) from exc


def _validate_claim_route(
    decision: InteractionModelDecision,
    claim: IntentClaimV1 | None,
) -> None:
    action = decision.action.value
    if action in {"run", "clarify"} and claim is None:
        raise SemanticInteractionParseError("semantic run/clarify response requires a claim")
    if action in {"respond", "continue"} and claim is not None:
        raise SemanticInteractionParseError("respond/continue cannot carry an intent claim")
    if claim is None:
        return
    if action == "run" and (
        decision.directive is None or decision.directive.value != claim.operation
    ):
        raise SemanticInteractionParseError("routing directive and claim operation disagree")
    if action == "run" and claim.ambiguity != "none":
        raise SemanticInteractionParseError("ambiguous claim must be routed to clarify")
    if action == "clarify" and claim.ambiguity == "none":
        raise SemanticInteractionParseError("clarify claim must identify ambiguity")


def parse_semantic_interaction_resolution(raw: str) -> InteractionModelDecision:
    """Parse one W14 response without repair or partial-field salvage."""

    value = _decode_semantic_json(raw)
    if set(value) != set(SEMANTIC_INTERACTION_KEYS):
        raise SemanticInteractionParseError("semantic response keys are not exact")
    claim_value = value.pop("intent_claim")
    decision = _validate_outer(value)
    claim = _parse_claim(claim_value)
    _validate_claim_route(decision, claim)
    return InteractionModelDecision(
        action=decision.action,
        directive=decision.directive,
        ambiguity=decision.ambiguity,
        grounding=decision.grounding,
        operation_requested=decision.operation_requested,
        proposal_only=decision.proposal_only,
        resume_requested=decision.resume_requested,
        evidence=decision.evidence,
        intent_claim=claim,
    )


def verify_semantic_request_contract(request: Any) -> None:
    carried = coerce_request_contract(getattr(request, "request_contract", None))
    if carried is not ModelRequestContract.SEMANTIC_INTENT:
        raise ValueError("semantic request contract mismatch")


__all__ = [
    "SEMANTIC_INTERACTION_KEYS",
    "SEMANTIC_INTERACTION_SCHEMA",
    "SEMANTIC_RESOLVER_GBNF",
    "SEMANTIC_RESOLVER_JSON_INSTRUCTION",
    "SemanticInteractionParseError",
    "parse_semantic_interaction_resolution",
    "verify_semantic_request_contract",
]
