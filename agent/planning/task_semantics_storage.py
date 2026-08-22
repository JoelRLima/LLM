"""Private state initialization for the canonical task semantic owner."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from agent.planning.task_semantics_types import (
    MAX_OBLIGATIONS,
    ObligationStatus,
    TaskIntent,
    TaskObligation,
    TaskSemanticsError,
    _eligible_evidence_ref,
    _normalize_effect,
)


def initialize_semantics(
    owner: Any,
    intent: TaskIntent,
    obligations: Sequence[TaskObligation],
    *,
    statuses: Mapping[str, str | ObligationStatus] | None,
    evidence: Mapping[str, Sequence[int | str]] | None,
    executed_effects: Sequence[str],
    waived_effects: Sequence[str],
) -> None:
    if not isinstance(intent, TaskIntent):
        raise TaskSemanticsError("TaskIntent invalido")
    if len(obligations) > MAX_OBLIGATIONS or any(not isinstance(item, TaskObligation) for item in obligations):
        raise TaskSemanticsError("obrigacoes invalidas")
    ids = [item.id for item in obligations]
    if len(set(ids)) != len(ids):
        raise TaskSemanticsError("ids de obrigacoes duplicados")
    if any(item.kind == "effect" and item.effect not in intent.requested_effects for item in obligations):
        raise TaskSemanticsError("obrigacao de efeito nao solicitada")
    owner._intent = intent
    owner._obligations = tuple(obligations)
    owner._statuses = {item.id: ObligationStatus.PENDING for item in owner._obligations}
    owner._evidence = {item.id: [] for item in owner._obligations}
    owner._status_claims = {}
    owner._evidence_claims = {}
    owner._evidence_catalog = {}
    owner._executed_effects = (
        []
        if owner._strict_evidence
        else list(dict.fromkeys(_normalize_effect(item) for item in executed_effects))
    )
    owner._waived_effects = (
        []
        if owner._strict_evidence
        else list(dict.fromkeys(_normalize_effect(item) for item in waived_effects))
    )
    _restore_statuses(owner, statuses or {}, evidence or {})
    _project_terminal_effects(owner)


def _restore_statuses(
    owner: Any,
    statuses: Mapping[str, str | ObligationStatus],
    evidence: Mapping[str, Sequence[int | str]],
) -> None:
    if not isinstance(statuses, Mapping) or not isinstance(evidence, Mapping):
        raise TaskSemanticsError("status ou evidencia de obrigacao invalido")

    normalized_evidence: dict[str, list[int | str]] = {}
    for obligation_id, refs in evidence.items():
        if obligation_id not in owner._evidence or not isinstance(refs, (list, tuple)):
            raise TaskSemanticsError("evidencia de obrigacao invalida")
        normalized_evidence[obligation_id] = [_eligible_evidence_ref(ref) for ref in refs]

    for obligation_id, raw_status in statuses.items():
        if obligation_id not in owner._statuses:
            raise TaskSemanticsError("status referencia obrigacao desconhecida")
        try:
            status = raw_status if isinstance(raw_status, ObligationStatus) else ObligationStatus(str(raw_status))
        except ValueError as exc:
            raise TaskSemanticsError("status de obrigacao invalido") from exc
        obligation = next(item for item in owner._obligations if item.id == obligation_id)
        if status is not ObligationStatus.PENDING and not evidence.get(obligation_id):
            raise TaskSemanticsError("status terminal requer transicao com evidencia")
        if (
            getattr(owner, "_strict_evidence", False)
            and obligation.kind == "effect"
            and status is not ObligationStatus.PENDING
            and any(
                isinstance(ref, str) and ref.startswith("legacy:")
                for ref in evidence.get(obligation_id, ())
            )
        ):
            raise TaskSemanticsError("evidencia sintetica nao pode provar efeito operacional")
        if status is not ObligationStatus.PENDING:
            owner._status_claims[obligation_id] = status
    owner._evidence_claims = normalized_evidence


def _project_terminal_effects(owner: Any) -> None:
    for item in owner._obligations:
        if item.effect is None:
            continue
        status = owner._statuses[item.id]
        target = owner._executed_effects if status is ObligationStatus.SATISFIED else owner._waived_effects
        if status in {ObligationStatus.SATISFIED, ObligationStatus.WAIVED} and item.effect not in target:
            target.append(item.effect)
