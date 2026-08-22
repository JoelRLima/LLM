"""Evidence-backed transitions for the canonical task semantic owner."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from agent.planning.failure_policy import FailureClass, classify_failure
from agent.planning.task_semantics_evidence import (
    _READ_TOOLS,
    arg_path,
    complete_observation,
    matches_fallback,
    matches_requirement,
    result_is_successful,
    same_identity,
)
from agent.planning.task_semantics_terminal import validate_terminal_evidence
from agent.planning.task_semantics_types import (
    ObligationStatus,
    TaskIntent,
    TaskObligation,
    TaskSemanticsError,
    _eligible_evidence_ref,
    _normalize_effect,
)


def transition(
    owner: Any,
    obligation_id: str,
    status: ObligationStatus,
    *,
    evidence_ref: int | str | None,
    allow_legacy: bool = False,
    effect_authority: Any = None,
) -> None:
    refs = () if evidence_ref is None else (_eligible_evidence_ref(evidence_ref),)
    transition_with_evidence(
        owner,
        obligation_id,
        status,
        evidence_refs=refs,
        allow_legacy=allow_legacy,
        effect_authority=effect_authority,
    )


def transition_with_evidence(
    owner: Any,
    obligation_id: str,
    status: ObligationStatus,
    *,
    evidence_refs: Sequence[int | str],
    allow_legacy: bool = False,
    effect_authority: Any = None,
) -> None:
    if obligation_id not in owner._statuses:
        raise TaskSemanticsError("obrigacao desconhecida")
    refs = tuple(_eligible_evidence_ref(ref) for ref in evidence_refs)
    current = owner._statuses[obligation_id]
    existing = tuple(owner._evidence[obligation_id])
    if current is not ObligationStatus.PENDING:
        if current is not status:
            raise TaskSemanticsError("transicao de obrigacao terminal invalida")
        new_refs = tuple(ref for ref in refs if ref not in existing)
        if not new_refs:
            return
        candidate = existing + new_refs
    else:
        candidate = refs
    if not candidate:
        if not allow_legacy:
            raise TaskSemanticsError("transicao operacional requer evidencia")
        candidate = (f"legacy:{obligation_id}",)
    if getattr(owner, "_strict_evidence", False) and not allow_legacy:
        validate_terminal_evidence(
            owner,
            obligation_id,
            status,
            candidate,
            effect_authority=effect_authority,
        )
    owner._statuses[obligation_id] = status
    owner._evidence[obligation_id] = list(candidate)


def satisfy(
    owner: Any,
    obligation_id: str,
    evidence_ref: int | str,
    *,
    effect_authority: Any = None,
) -> None:
    transition(
        owner,
        obligation_id,
        ObligationStatus.SATISFIED,
        evidence_ref=evidence_ref,
        effect_authority=effect_authority,
    )


def waive(
    owner: Any,
    obligation_id: str,
    evidence_ref: int | str,
    *,
    effect_authority: Any = None,
) -> None:
    transition(
        owner,
        obligation_id,
        ObligationStatus.WAIVED,
        evidence_ref=evidence_ref,
        effect_authority=effect_authority,
    )


def block(
    owner: Any,
    obligation_id: str,
    evidence_ref: int | str,
    *,
    effect_authority: Any = None,
) -> None:
    transition(
        owner,
        obligation_id,
        ObligationStatus.BLOCKED,
        evidence_ref=evidence_ref,
        effect_authority=effect_authority,
    )


def register_observation(
    owner: Any,
    tool_name: str,
    result: Mapping[str, Any],
    evidence_ref: int | str,
    *,
    args: Mapping[str, Any] | None = None,
) -> None:
    ref = _eligible_evidence_ref(evidence_ref)
    if not isinstance(result, Mapping):
        raise TaskSemanticsError("observacao canonica invalida")
    previous = getattr(owner, "_evidence_catalog", {}).get(ref)
    observation = {
        "tool": str(tool_name).strip().casefold(),
        "args": dict(args) if isinstance(args, Mapping) else {},
        "result": dict(result),
    }
    if previous is not None and previous.get("tool") and previous != observation:
        raise TaskSemanticsError("referencia de evidencia reutilizada para observacoes distintas")
    owner._evidence_catalog[ref] = observation


def observe_tool(
    owner: Any,
    tool_name: str,
    result: Mapping[str, Any],
    evidence_ref: int | str,
    *,
    args: Mapping[str, Any] | None = None,
) -> tuple[str, ...]:
    register_observation(owner, tool_name, result, evidence_ref, args=args)
    ref = _eligible_evidence_ref(evidence_ref)
    if not result_is_successful(result):
        if classify_failure(result) is not FailureClass.LOCAL:
            return ()
        satisfied_ids: list[str] = []
        for item in owner.pending_obligations():
            if item.kind == "fallback" and matches_fallback(item, tool_name, result, args):
                satisfy(owner, item.id, ref)
                satisfied_ids.append(item.id)
                for required_read in owner.pending_obligations():
                    if required_read.kind == "read" and same_identity(
                        required_read.target,
                        item.fallback_target,
                    ):
                        waive(owner, required_read.id, ref)
                        satisfied_ids.append(required_read.id)
        return tuple(satisfied_ids)
    tool = str(tool_name).strip().casefold()
    satisfied: list[str] = []
    for item in owner.pending_obligations():
        if item.kind == "effect" or not matches_requirement(owner, item, tool, result, args):
            continue
        satisfy(owner, item.id, ref)
        satisfied.append(item.id)
    for obligation_id in _satisfy_comparisons_from_reads(owner):
        satisfied.append(obligation_id)
    return tuple(satisfied)


def _satisfy_comparisons_from_reads(owner: Any) -> list[str]:
    reads: dict[str, tuple[int | str, Mapping[str, Any]]] = {}
    for ref, item in getattr(owner, "_evidence_catalog", {}).items():
        result = item.get("result")
        path = arg_path(item.get("args"))
        if item.get("tool") in _READ_TOOLS and isinstance(result, Mapping) and path is not None:
            if complete_observation(result):
                reads[path.casefold()] = (ref, result)
    satisfied: list[str] = []
    for item in owner.pending_obligations():
        if item.kind != "compare" or len(item.operands) != 2:
            continue
        left = reads.get(item.operands[0].casefold())
        right = reads.get(item.operands[1].casefold())
        if left is None or right is None:
            continue
        transition_with_evidence(
            owner,
            item.id,
            ObligationStatus.SATISFIED,
            evidence_refs=(left[0], right[0]),
        )
        satisfied.append(item.id)
    return satisfied


def replace_effects(owner: Any, requested_effects: Sequence[str], prohibited_effects: Sequence[str]) -> None:
    requested = tuple(dict.fromkeys(_normalize_effect(item) for item in requested_effects))
    prohibited = tuple(dict.fromkeys(_normalize_effect(item) for item in prohibited_effects))
    non_effect = tuple(item for item in owner._obligations if item.kind != "effect")
    owner._intent = TaskIntent(owner.objective, requested, prohibited)
    owner._obligations = non_effect + tuple(
        TaskObligation(
            id=f"effect:{effect}",
            kind="effect",
            effect=effect,
            description=f"Produzir o efeito operacional solicitado: {effect}.",
        )
        for effect in requested
    )
    previous = owner._statuses
    previous_evidence = owner._evidence
    owner._statuses = {item.id: previous.get(item.id, ObligationStatus.PENDING) for item in owner._obligations}
    owner._evidence = {item.id: list(previous_evidence.get(item.id, ())) for item in owner._obligations}


def reset_progress(owner: Any) -> None:
    owner._statuses = {item.id: ObligationStatus.PENDING for item in owner._obligations}
    owner._evidence = {item.id: [] for item in owner._obligations}
    owner._executed_effects = []
    owner._waived_effects = []
    owner._evidence_catalog = {}
