"""Fail-closed revalidation of restored task-semantic terminal evidence."""

from __future__ import annotations

from typing import Any

from agent.planning.task_semantics import ObligationStatus, TaskSemantics, TaskSemanticsError
from agent.planning.task_semantics_terminal import validate_terminal_evidence


def revalidate_restored_terminal_evidence(
    semantics: TaskSemantics,
    *,
    effect_authority: Any = None,
) -> None:
    """Re-prove terminal obligations against the complete canonical history.

    The same aggregate transition validator used by live semantic transitions
    is used here.  A composite obligation is therefore not revalidated by
    replaying each reference as an independent proof.
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
        validate_terminal_evidence(
            semantics,
            obligation.id,
            status,
            semantics.obligation_evidence(obligation.id),
            effect_authority=effect_authority,
        )


__all__ = ["revalidate_restored_terminal_evidence"]
