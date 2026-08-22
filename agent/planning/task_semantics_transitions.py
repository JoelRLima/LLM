"""Evidence-backed transitions for the canonical task semantic owner."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

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
) -> None:
    if obligation_id not in owner._statuses:
        raise TaskSemanticsError("obrigacao desconhecida")
    current = owner._statuses[obligation_id]
    if current is not ObligationStatus.PENDING:
        if current is status and evidence_ref is not None:
            ref = _eligible_evidence_ref(evidence_ref)
            if ref not in owner._evidence[obligation_id]:
                owner._evidence[obligation_id].append(ref)
            return
        if current is status:
            return
        raise TaskSemanticsError("transicao de obrigacao terminal invalida")
    if evidence_ref is None:
        if not allow_legacy:
            raise TaskSemanticsError("transicao operacional requer evidencia")
        evidence_ref = f"legacy:{obligation_id}"
    owner._statuses[obligation_id] = status
    owner._evidence[obligation_id].append(_eligible_evidence_ref(evidence_ref))


def satisfy(owner: Any, obligation_id: str, evidence_ref: int | str) -> None:
    transition(owner, obligation_id, ObligationStatus.SATISFIED, evidence_ref=evidence_ref)


def waive(owner: Any, obligation_id: str, evidence_ref: int | str) -> None:
    transition(owner, obligation_id, ObligationStatus.WAIVED, evidence_ref=evidence_ref)


def block(owner: Any, obligation_id: str, evidence_ref: int | str) -> None:
    transition(owner, obligation_id, ObligationStatus.BLOCKED, evidence_ref=evidence_ref)


def record_effect(owner: Any, effect: str, *, evidence_ref: int | str | None, allow_legacy: bool) -> None:
    normalized = _normalize_effect(effect)
    match = next(
        (item for item in owner._obligations if item.kind == "effect" and item.effect == normalized),
        None,
    )
    if match is None:
        if normalized not in owner._executed_effects:
            owner._executed_effects.append(normalized)
        return
    transition(
        owner,
        match.id,
        ObligationStatus.SATISFIED,
        evidence_ref=evidence_ref,
        allow_legacy=allow_legacy,
    )
    if normalized not in owner._executed_effects:
        owner._executed_effects.append(normalized)


def waive_effect(owner: Any, effect: str, *, evidence_ref: int | str | None, allow_legacy: bool) -> None:
    normalized = _normalize_effect(effect)
    match = next(
        (item for item in owner._obligations if item.kind == "effect" and item.effect == normalized),
        None,
    )
    if match is None:
        if normalized not in owner.requested_effects:
            raise TaskSemanticsError("efeito nao solicitado")
        if normalized not in owner._waived_effects:
            owner._waived_effects.append(normalized)
        return
    transition(
        owner,
        match.id,
        ObligationStatus.WAIVED,
        evidence_ref=evidence_ref,
        allow_legacy=allow_legacy,
    )
    if normalized not in owner._waived_effects:
        owner._waived_effects.append(normalized)


def observe_tool(owner: Any, tool_name: str, result: Mapping[str, Any], evidence_ref: int | str) -> tuple[str, ...]:
    if not _result_is_successful(result):
        return ()
    ref = _eligible_evidence_ref(evidence_ref)
    tool = str(tool_name).strip().casefold()
    tools = {
        "read": frozenset({"file_reader", "code_analyzer", "directory_lister"}),
        "search": frozenset({"grep", "search"}),
        "compare": frozenset({"compare", "diff", "code_analyzer"}),
        "analyze": frozenset({"code_analyzer", "analyze"}),
    }
    satisfied: list[str] = []
    for item in owner.pending_obligations():
        if item.kind != "effect" and tool in tools.get(item.kind, frozenset()):
            satisfy(owner, item.id, ref)
            satisfied.append(item.id)
    return tuple(satisfied)


def _result_is_successful(result: Mapping[str, Any]) -> bool:
    if not isinstance(result, Mapping):
        return False
    if result.get("status") in {"failed", "blocked", "unverified", "permission_denied", "cancelled"}:
        return False
    return result.get("ok") is True and (
        result.get("status") == "succeeded" or result.get("done") is True or result.get("executed") is True
    )


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
