"""Fail-closed revalidation of restored task-semantic terminal evidence."""

from __future__ import annotations

from collections.abc import Mapping

from agent.planning.task_semantics import (
    ObligationStatus,
    TaskSemantics,
    TaskSemanticsError,
)


def _require_restored_observation(
    catalog: dict[object, dict[str, object]], evidence_ref: int | str
) -> None:
    observation = catalog.get(evidence_ref)
    if (
        not isinstance(observation, Mapping)
        or not str(observation.get("tool") or "").strip()
        or not isinstance(observation.get("result"), Mapping)
    ):
        raise TaskSemanticsError(
            "evidencia terminal restaurada nao pertence ao historico canonico"
        )


def _replay_terminal_transition(
    semantics: TaskSemantics,
    obligation_id: str,
    status: ObligationStatus,
    evidence_ref: int | str,
) -> None:
    if status is ObligationStatus.SATISFIED:
        semantics.satisfy(obligation_id, evidence_ref=evidence_ref)
        return
    if status is ObligationStatus.WAIVED:
        semantics.waive(obligation_id, evidence_ref=evidence_ref)
        return
    if status is ObligationStatus.BLOCKED:
        semantics.block(obligation_id, evidence_ref=evidence_ref)
        return
    raise TaskSemanticsError("status terminal restaurado invalido")


def _revalidate_obligation(
    semantics: TaskSemantics,
    catalog: dict[object, dict[str, object]],
    obligation_id: str,
) -> None:
    status = semantics.obligation_status(obligation_id)
    if status is ObligationStatus.PENDING:
        return
    refs = semantics.obligation_evidence(obligation_id)
    if not refs:
        raise TaskSemanticsError("obrigacao terminal restaurada sem evidencia")
    for ref in refs:
        _require_restored_observation(catalog, ref)
        _replay_terminal_transition(semantics, obligation_id, status, ref)


def revalidate_restored_terminal_evidence(semantics: TaskSemantics) -> None:
    """Re-prove restored terminal obligations against canonical history.

    ``TaskSemantics.from_checkpoint_dict`` restores semantic projection before
    ``AgentState`` restores tool history. Once that history has rebuilt the
    canonical evidence catalog, replay the normal requirement-specific
    transition validator for every terminal obligation. Missing, stale, or
    mismatched references therefore fail closed before resume.
    """

    if not isinstance(semantics, TaskSemantics):
        raise TaskSemanticsError("task semantics restaurada invalida")
    catalog = getattr(semantics, "_evidence_catalog", None)
    if not isinstance(catalog, dict):
        raise TaskSemanticsError("catalogo canonico de evidencia ausente no restore")
    for obligation in semantics.obligations:
        _revalidate_obligation(semantics, catalog, obligation.id)


__all__ = ["revalidate_restored_terminal_evidence"]
