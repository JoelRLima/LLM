"""Strict, closed W12 resolver response contract."""

from __future__ import annotations

import json
from typing import Any

from agent.llm.decision_contract import ModelRequestContract, coerce_request_contract
from agent.runtime.task_directives import TaskDirective

from .errors import (
    INTERACTION_REQUEST_CONTRACT_MISMATCH,
    InteractionResolutionParseError,
)
from .types import (
    ActionGrounding,
    InteractionAction,
    InteractionAmbiguity,
    InteractionModelDecision,
)

INTERACTION_RESOLUTION_GBNF = r'''root ::= ws object ws
object ::= "{" ws "\"action\"" ws ":" ws action ws "," ws "\"directive\"" ws ":" ws directive ws "," ws "\"ambiguity\"" ws ":" ws ambiguity ws "," ws "\"grounding\"" ws ":" ws grounding ws "," ws "\"operation_requested\"" ws ":" ws boolean ws "," ws "\"proposal_only\"" ws ":" ws boolean ws "," ws "\"resume_requested\"" ws ":" ws boolean ws "," ws "\"evidence\"" ws ":" ws string ws "}"
action ::= "\"respond\"" | "\"clarify\"" | "\"run\"" | "\"continue\""
directive ::= "\"none\"" | "\"read\"" | "\"plan\"" | "\"do\""
ambiguity ::= "\"none\"" | "\"effect\"" | "\"continuation\"" | "\"grounding\"" | "\"conflict\""
grounding ::= "\"none\"" | "\"current_turn\"" | "\"contextual\""
boolean ::= "true" | "false"
string ::= "\"" chars "\""
chars ::= char*
char ::= [^"\\\x00-\x1F] | escape
escape ::= "\\" (["\\/bfnrt] | "u" hex hex hex hex)
hex ::= [0-9a-fA-F]
ws ::= [ \t\n\r]*'''

INTERACTION_RESOLUTION_KEYS = frozenset(
    {
        "action",
        "directive",
        "ambiguity",
        "grounding",
        "operation_requested",
        "proposal_only",
        "resume_requested",
        "evidence",
    }
)

INTERACTION_RESOLUTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "action",
        "directive",
        "ambiguity",
        "grounding",
        "operation_requested",
        "proposal_only",
        "resume_requested",
        "evidence",
    ],
    "properties": {
        "action": {"type": "string", "enum": [item.value for item in InteractionAction]},
        "directive": {
            "type": "string",
            "enum": ["none", TaskDirective.READ.value, TaskDirective.PLAN.value, TaskDirective.DO.value],
        },
        "ambiguity": {"type": "string", "enum": [item.value for item in InteractionAmbiguity]},
        "grounding": {"type": "string", "enum": [item.value for item in ActionGrounding]},
        "operation_requested": {"type": "boolean"},
        "proposal_only": {"type": "boolean"},
        "resume_requested": {"type": "boolean"},
        "evidence": {"type": "string"},
    },
}


def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise InteractionResolutionParseError("duplicate JSON key")
        result[key] = value
    return result


def reject_nonstandard_constant(raw: str) -> Any:
    del raw
    raise InteractionResolutionParseError("non-standard JSON number")


def reject_unicode_surrogates(value: Any) -> None:
    if isinstance(value, str):
        if any(0xD800 <= ord(char) <= 0xDFFF for char in value):
            raise InteractionResolutionParseError("Unicode surrogate is not a scalar value")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            reject_unicode_surrogates(key)
            reject_unicode_surrogates(item)
        return
    if isinstance(value, list):
        for item in value:
            reject_unicode_surrogates(item)


def _invalid(detail: str = "invalid interaction resolution") -> InteractionResolutionParseError:
    return InteractionResolutionParseError(detail)


def validate_interaction_resolution(value: Any) -> InteractionModelDecision:
    """Validate in the frozen P5.4 order and return the typed eight-field object."""

    if not isinstance(value, dict):
        raise _invalid("interaction resolution must be an object")
    reject_unicode_surrogates(value)
    if set(value) != INTERACTION_RESOLUTION_KEYS:
        raise _invalid("interaction resolution keys are not exact")
    for key in ("operation_requested", "proposal_only", "resume_requested"):
        if type(value[key]) is not bool:
            raise _invalid(f"{key} must be a boolean")
    string_keys = ("action", "directive", "ambiguity", "grounding", "evidence")
    if any(type(value[key]) is not str for key in string_keys):
        raise _invalid("interaction resolution primitive type is invalid")
    try:
        action = InteractionAction(value["action"])
        ambiguity = InteractionAmbiguity(value["ambiguity"])
        grounding = ActionGrounding(value["grounding"])
    except ValueError as exc:
        raise _invalid("interaction resolution enum is invalid") from exc
    directive: TaskDirective | None
    if value["directive"] == "none":
        directive = None
    else:
        try:
            directive = TaskDirective(value["directive"])
        except ValueError as exc:
            raise _invalid("interaction resolution directive is invalid") from exc
        if directive is TaskDirective.AUTO:
            raise _invalid("AUTO is not valid in interaction resolution")
    evidence = value["evidence"]
    if len(evidence) > 512:
        raise _invalid("evidence is too long")
    if not _valid_combination(
        action=action,
        directive=directive,
        ambiguity=ambiguity,
        grounding=grounding,
        operation_requested=value["operation_requested"],
        proposal_only=value["proposal_only"],
        resume_requested=value["resume_requested"],
        evidence=evidence,
    ):
        raise _invalid("interaction resolution combination is invalid")
    return InteractionModelDecision(
        action=action,
        directive=directive,
        ambiguity=ambiguity,
        grounding=grounding,
        operation_requested=value["operation_requested"],
        proposal_only=value["proposal_only"],
        resume_requested=value["resume_requested"],
        evidence=evidence,
    )


def _valid_combination(
    *,
    action: InteractionAction,
    directive: TaskDirective | None,
    ambiguity: InteractionAmbiguity,
    grounding: ActionGrounding,
    operation_requested: bool,
    proposal_only: bool,
    resume_requested: bool,
    evidence: str,
) -> bool:
    bounded_evidence = 1 <= len(evidence) <= 512
    if action is InteractionAction.RESPOND:
        return (
            directive is None
            and ambiguity is InteractionAmbiguity.NONE
            and grounding is ActionGrounding.NONE
            and not operation_requested
            and not proposal_only
            and not resume_requested
            and evidence == ""
        )
    if action is InteractionAction.CONTINUE:
        return (
            directive is None
            and ambiguity is InteractionAmbiguity.NONE
            and grounding is ActionGrounding.CURRENT_TURN
            and not operation_requested
            and not proposal_only
            and resume_requested
            and bounded_evidence
        )
    if action is InteractionAction.RUN:
        if directive not in {TaskDirective.READ, TaskDirective.PLAN, TaskDirective.DO}:
            return False
        return (
            ambiguity is InteractionAmbiguity.NONE
            and grounding is ActionGrounding.CURRENT_TURN
            and operation_requested is (directive is TaskDirective.DO)
            and proposal_only is (directive is TaskDirective.PLAN)
            and not resume_requested
            and bounded_evidence
        )
    if action is InteractionAction.CLARIFY:
        if directive is not None or not bounded_evidence and ambiguity in {
            InteractionAmbiguity.EFFECT,
            InteractionAmbiguity.CONTINUATION,
            InteractionAmbiguity.CONFLICT,
        }:
            return False
        if ambiguity is InteractionAmbiguity.EFFECT:
            return (
                grounding is ActionGrounding.CURRENT_TURN
                and operation_requested
                and not proposal_only
                and not resume_requested
                and bounded_evidence
            )
        if ambiguity is InteractionAmbiguity.CONTINUATION:
            return (
                grounding is ActionGrounding.CURRENT_TURN
                and not operation_requested
                and not proposal_only
                and resume_requested
                and bounded_evidence
            )
        if ambiguity is InteractionAmbiguity.GROUNDING:
            return (
                grounding is ActionGrounding.CONTEXTUAL
                and not operation_requested
                and not proposal_only
                and not resume_requested
                and (evidence == "" or bounded_evidence)
            )
        if ambiguity is InteractionAmbiguity.CONFLICT:
            return (
                grounding is ActionGrounding.CURRENT_TURN
                and operation_requested
                and not proposal_only
                and not resume_requested
                and bounded_evidence
            )
    return False


def parse_interaction_resolution(raw: str) -> InteractionModelDecision:
    if type(raw) is not str:
        raise InteractionResolutionParseError("resolver output is not text")
    try:
        decoded = json.loads(
            raw.strip(),
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=reject_nonstandard_constant,
        )
    except InteractionResolutionParseError:
        raise
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise InteractionResolutionParseError("resolver output is not strict JSON") from exc
    return validate_interaction_resolution(decoded)


parse_strict_interaction_resolution = parse_interaction_resolution
admit_interaction_resolution = validate_interaction_resolution


def verify_interaction_request_contract(request: Any) -> None:
    carried = coerce_request_contract(getattr(request, "request_contract", None))
    if carried is not ModelRequestContract.INTERACTION_RESOLUTION:
        raise ValueError(INTERACTION_REQUEST_CONTRACT_MISMATCH)


__all__ = [
    "INTERACTION_RESOLUTION_GBNF",
    "INTERACTION_RESOLUTION_KEYS",
    "INTERACTION_RESOLUTION_SCHEMA",
    "admit_interaction_resolution",
    "parse_interaction_resolution",
    "parse_strict_interaction_resolution",
    "reject_duplicate_keys",
    "reject_nonstandard_constant",
    "reject_unicode_surrogates",
    "validate_interaction_resolution",
    "verify_interaction_request_contract",
]
