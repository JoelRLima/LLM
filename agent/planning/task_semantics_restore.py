"""Fail-closed revalidation of restored task-semantic terminal evidence."""

from __future__ import annotations

from collections.abc import Mapping

from agent.planning.task_semantics import (
    ObligationStatus,
    TaskSemantics,
    TaskSemanticsError,
)


def revalidate_restored_terminal_evidence(semantics: TaskSemantics) -> None:
    """Re-prove every restored terminal obligation against canonical history.

    ``TaskSemantics.from_checkpoint_dict`` restores the semantic projection
    before ``AgentState`` restores tool history. During that first phase,
    evidence references are identities only. Once history has rebuilt the
    canonical evidence catalog, this function re-runs the normal transition
    validator for every terminal obligation. Missing, stale, or
    requirement-mismatched references therefore fail closed before resume.
    """

    if not isinstance(semantics, TaskSemantics):
        raise TaskSemanticsError("task semantics restaurada invalida")

    catalog = getattr(semantics, "_evidence_catalog", None)
    if not isinstance(catalog, dict):
        raise TaskSemanticsError("catalogo canonico de evidencia ausente no restore")

    for obligation in semantics.obligations:
        status = semantics.obligation_status(obligation.id)
        if status is ObligationStatus.PENDING:
            continue

        refs = semantics.obligation_evidence(obligation.id)
        if not refs:
            raise TaskSemanticsError("obrigacao terminal restaurada sem evidencia")

        for ref in refs:
            observation = catalog.get(ref)
            if (
                not isinstance(observation, Mapping)
                or not str(observation.get("tool") or "").strip()
                or not isinstance(observation.get("result"), Mapping)
            ):
                raise TaskSemanticsError(
                    "evidencia terminal restaurada nao pertence ao historico canonico"
                )

            if status is ObligationStatus.SATISFIED:
                semantics.satisfy(obligation.id, evidence_ref=ref)
            elif status is ObligationStatus.WAIVED:
                semantics.waive(obligation.id, evidence_ref=ref)
            elif status is ObligationStatus.BLOCKED:
                semantics.block(obligation.id, evidence_ref=ref)
            else:  # pragma: no cover - enum closure is defensive here.
                raise TaskSemanticsError("status terminal restaurado invalido")


__all__ = ["revalidate_restored_terminal_evidence"]
