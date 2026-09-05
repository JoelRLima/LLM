"""Closed proposal decision contracts and parsing."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

from agent.code.change_models import FileChange
from agent.llm.structured_output import StructuredOutputError


class CodeProposalKind(str, Enum):
    CHANGE = "CHANGE"
    NO_CHANGE = "NO_CHANGE"
    NEEDS_INPUT = "NEEDS_INPUT"


class CodeProposalReasonCode(str, Enum):
    NONE = "NONE"
    TARGET_CHOICE_REQUIRED = "TARGET_CHOICE_REQUIRED"
    EXPECTED_BEHAVIOR_MISSING = "EXPECTED_BEHAVIOR_MISSING"
    CONSTRAINT_MISSING = "CONSTRAINT_MISSING"
    MULTIPLE_VALID_INTERPRETATIONS = "MULTIPLE_VALID_INTERPRETATIONS"
    USER_CHOICE_REQUIRED = "USER_CHOICE_REQUIRED"


def _validate_decision_fields(decision: Any) -> None:
    if not isinstance(decision.kind, CodeProposalKind):
        raise TypeError("kind must be CodeProposalKind")
    if not isinstance(decision.reason_code, CodeProposalReasonCode):
        raise TypeError("reason_code must be CodeProposalReasonCode")
    if not isinstance(decision.rationale, str) or len(decision.rationale) > 2_000:
        raise ValueError("rationale excede 2000 caracteres")
    if not isinstance(decision.question, str) or len(decision.question) > 512:
        raise ValueError("question excede 512 caracteres")
    if not isinstance(decision.changes, tuple) or not all(
        isinstance(change, FileChange) for change in decision.changes
    ):
        raise TypeError("changes must be a tuple of FileChange")


def _validate_change_decision(decision: Any) -> None:
    if decision.reason_code is not CodeProposalReasonCode.NONE:
        raise ValueError("CHANGE exige reason_code NONE")
    if decision.question != "" or not decision.changes:
        raise ValueError("CHANGE exige question vazia e changes nao vazias")


def _validate_no_change_decision(decision: Any) -> None:
    if (
        decision.reason_code is not CodeProposalReasonCode.NONE
        or decision.question != ""
        or decision.changes
        or not decision.rationale.strip()
    ):
        raise ValueError("NO_CHANGE inconsistente")


def _validate_input_decision(decision: Any) -> None:
    if (
        decision.reason_code is CodeProposalReasonCode.NONE
        or not decision.question.strip()
        or not decision.rationale.strip()
        or decision.changes
    ):
        raise ValueError("NEEDS_INPUT inconsistente")


@dataclass(frozen=True, slots=True)
class CodeProposalDecision:
    kind: CodeProposalKind
    rationale: str
    reason_code: CodeProposalReasonCode
    question: str
    changes: tuple[FileChange, ...] = ()

    def __post_init__(self) -> None:
        _validate_decision_fields(self)
        if self.kind is CodeProposalKind.CHANGE:
            _validate_change_decision(self)
        elif self.kind is CodeProposalKind.NO_CHANGE:
            _validate_no_change_decision(self)
        else:
            _validate_input_decision(self)

    @property
    def decision(self) -> str:
        return self.kind.value


PROPOSAL_DECISION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["decision", "rationale", "reason_code", "question", "changes"],
    "additionalProperties": False,
    "properties": {
        "decision": {
            "type": "string",
            "enum": [item.value for item in CodeProposalKind],
        },
        "rationale": {"type": "string"},
        "reason_code": {
            "type": "string",
            "enum": [item.value for item in CodeProposalReasonCode],
        },
        "question": {"type": "string"},
        "changes": {"type": "array"},
    },
}


def _strict_change_fields(raw: Mapping[str, Any]) -> None:
    allowed = {"path", "kind", "content", "base_hash", "destination_path", "edits"}
    extra = set(raw) - allowed
    if extra:
        raise StructuredOutputError(
            f"MudanÃ§a contÃ©m campos nÃ£o permitidos: {', '.join(sorted(extra))}."
        )
    edits = raw.get("edits")
    if isinstance(edits, list):
        for edit in edits:
            if not isinstance(edit, Mapping):
                raise StructuredOutputError("Edit de mudanÃ§a deve ser objeto.")
            edit_extra = set(edit) - {
                "operation",
                "start_line",
                "end_line",
                "content",
                "expected_text",
            }
            if edit_extra:
                raise StructuredOutputError(
                    f"Edit contÃ©m campos nÃ£o permitidos: {', '.join(sorted(edit_extra))}."
                )


def _proposal_header(
    value: Mapping[str, Any],
) -> tuple[CodeProposalKind, CodeProposalReasonCode, str, str, list[Any]]:
    expected = {"decision", "rationale", "reason_code", "question", "changes"}
    if set(value) != expected:
        raise StructuredOutputError("INVALID PROPOSAL DECISION: campos fechados invÃ¡lidos.")
    try:
        kind = CodeProposalKind(value["decision"])
        reason = CodeProposalReasonCode(value["reason_code"])
    except (TypeError, ValueError) as exc:
        raise StructuredOutputError("INVALID PROPOSAL DECISION: enum invÃ¡lido.") from exc
    rationale = value["rationale"]
    question = value["question"]
    raw_changes = value["changes"]
    if not isinstance(rationale, str) or len(rationale) > 2_000:
        raise StructuredOutputError("INVALID PROPOSAL DECISION: rationale invÃ¡lido.")
    if not isinstance(question, str) or len(question) > 512:
        raise StructuredOutputError("INVALID PROPOSAL DECISION: question invÃ¡lida.")
    if not isinstance(raw_changes, list):
        raise StructuredOutputError("INVALID PROPOSAL DECISION: changes invÃ¡lido.")
    return kind, reason, rationale, question, raw_changes


def _proposal_changes(raw_changes: list[Any], objective: str) -> tuple[FileChange, ...]:
    for raw_change in raw_changes:
        if not isinstance(raw_change, Mapping):
            raise StructuredOutputError("INVALID PROPOSAL DECISION: mudanÃ§a invÃ¡lida.")
        _strict_change_fields(raw_change)
    try:
        from agent.code.change_parsing import changeset_from_dict

        return (
            changeset_from_dict(
                {"changes": list(raw_changes)}, objective=objective
            ).changes
            if raw_changes
            else ()
        )
    except (TypeError, ValueError, RuntimeError) as exc:
        if isinstance(exc, StructuredOutputError):
            raise
        raise StructuredOutputError(f"INVALID PROPOSAL DECISION: {exc}") from exc


def parse_code_proposal(value: Any, objective: str = "") -> CodeProposalDecision:
    """Parse exactly the W13 tri-state proposal envelope."""

    if not isinstance(value, Mapping):
        raise StructuredOutputError("INVALID PROPOSAL DECISION: objeto esperado.")
    kind, reason, rationale, question, raw_changes = _proposal_header(value)
    try:
        parsed_changes = _proposal_changes(raw_changes, objective)
        try:
            return CodeProposalDecision(kind, rationale, reason, question, parsed_changes)
        except (TypeError, ValueError) as exc:
            raise StructuredOutputError(f"INVALID PROPOSAL DECISION: {exc}") from exc
    except (TypeError, ValueError, RuntimeError) as exc:
        if isinstance(exc, StructuredOutputError):
            raise
        raise StructuredOutputError(f"INVALID PROPOSAL DECISION: {exc}") from exc


__all__ = [
    "CodeProposalDecision",
    "CodeProposalKind",
    "CodeProposalReasonCode",
    "PROPOSAL_DECISION_SCHEMA",
    "parse_code_proposal",
]
