"""Fail-closed revalidation of restored task-semantic terminal evidence."""

from __future__ import annotations

import copy
from typing import Any

from agent.planning.task_semantics import ObligationStatus, TaskSemantics, TaskSemanticsError
from agent.planning.task_semantics_storage import _project_terminal_effects
from agent.planning.task_semantics_terminal import validate_terminal_evidence


def revalidate_restored_terminal_evidence(
    semantics: TaskSemantics,
    *,
    effect_authority: Any = None,
) -> None:
    """Re-prove terminal claims against the complete canonical history.

    The same aggregate transition validator used by live semantic transitions
    is used here.  A composite obligation is therefore not revalidated by
    replaying each reference as an independent proof.  Claims are promoted
    from an isolated staged map only after the complete claim set validates.
    """

    if not isinstance(semantics, TaskSemantics):
        raise TaskSemanticsError("task semantics restaurada invalida")
    catalog = getattr(semantics, "_evidence_catalog", None)
    if not isinstance(catalog, dict):
        raise TaskSemanticsError("catalogo canonico de evidencia ausente no restore")
    status_claims = dict(getattr(semantics, "_status_claims", {}))
    evidence_claims = getattr(semantics, "_evidence_claims", {})
    staged = copy.copy(semantics)
    staged._statuses = dict(semantics._statuses)
    staged._evidence = {
        obligation_id: list(refs)
        for obligation_id, refs in semantics._evidence.items()
    }
    staged._status_claims = {}
    staged._evidence_claims = {}

    claims: list[tuple[str, ObligationStatus, tuple[int | str, ...]]] = [
        (
            obligation.id,
            status_claims[obligation.id],
            tuple(evidence_claims.get(obligation.id, ())),
        )
        for obligation in semantics.obligations
        if obligation.id in status_claims
    ]
    claims.extend(
        (
            obligation.id,
            semantics._statuses[obligation.id],
            tuple(semantics._evidence[obligation.id]),
        )
        for obligation in semantics.obligations
        if obligation.id not in status_claims
        and semantics._statuses[obligation.id] is not ObligationStatus.PENDING
    )
    # A recovered read claim depends on its matching fallback claim.  Promote
    # fallback claims first while retaining the staged, all-or-nothing passes;
    # this makes status-specific recovery checks independent of obligation
    # declaration order.
    obligation_kinds = {obligation.id: obligation.kind for obligation in semantics.obligations}
    claims.sort(key=lambda claim: obligation_kinds[claim[0]] != "fallback")

    remaining = list(claims)
    first_error: TaskSemanticsError | None = None
    while remaining:
        progressed = False
        for claim in tuple(remaining):
            obligation_id, status, refs = claim
            try:
                validate_terminal_evidence(
                    staged,
                    obligation_id,
                    status,
                    refs,
                    effect_authority=effect_authority,
                )
            except TaskSemanticsError as exc:
                first_error = first_error or exc
                continue
            staged._statuses[obligation_id] = status
            staged._evidence[obligation_id] = list(refs)
            remaining.remove(claim)
            progressed = True
        if not progressed:
            raise first_error or TaskSemanticsError(
                "evidencia terminal restaurada invalida"
            )

    semantics._statuses = staged._statuses
    semantics._evidence = staged._evidence
    semantics._status_claims = {}
    semantics._evidence_claims = {
        obligation_id: list(refs)
        for obligation_id, refs in evidence_claims.items()
        if obligation_id not in status_claims
    }
    _project_terminal_effects(semantics)


__all__ = ["revalidate_restored_terminal_evidence"]
