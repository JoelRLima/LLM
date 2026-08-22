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
        if _is_historical_compatibility_effect(
            semantics,
            obligation.id,
            status,
            semantics.obligation_evidence(obligation.id),
            effect_authority=effect_authority,
        ):
            continue
        validate_terminal_evidence(
            semantics,
            obligation.id,
            status,
            semantics.obligation_evidence(obligation.id),
            effect_authority=effect_authority,
        )


def _is_historical_compatibility_effect(
    semantics: TaskSemantics,
    obligation_id: str,
    status: ObligationStatus,
    refs: tuple[int | str, ...],
    *,
    effect_authority: Any,
) -> bool:
    """Keep the pre-checkpoint operational setter round-trip compatible.

    This narrow compatibility applies only to checkpoints emitted by the old
    in-memory setter path, whose synthetic ref is explicit and whose semantics
    object is an effect-only compatibility projection.  Legacy checkpoint
    lists still go through ``_restore_legacy_semantics`` and never reach this
    branch.
    """

    if (
        effect_authority is not None
        or semantics.objective != ""
        or status not in {
            ObligationStatus.SATISFIED,
            ObligationStatus.WAIVED,
        }
    ):
        return False
    obligation = next(
        (item for item in semantics.obligations if item.id == obligation_id),
        None,
    )
    if obligation is None or obligation.kind != "effect" or len(refs) != 1:
        return False
    if any(item.kind != "effect" for item in semantics.obligations):
        return False
    if any(
        isinstance(item, dict) and str(item.get("tool") or "").strip()
        for item in semantics._evidence_catalog.values()
    ):
        return False
    ref = refs[0]
    return ref in {
        f"legacy:{obligation_id}",
        f"legacy:executed:{obligation.effect}",
        f"legacy:waived:{obligation.effect}",
    }


__all__ = ["revalidate_restored_terminal_evidence"]
