"""Deterministic projection of final operational truth for one linear task."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent.reporting.observation_evidence import (
    artifact_metadata,
    metadata_is_persisted_mutation,
    project_artifact_evidence,
)

PUBLIC_TERMINAL_STATUSES = frozenset(
    {
        "succeeded",
        "blocked",
        "cancelled",
        "failed",
        "timed_out",
        "permission_denied",
        "protocol_error",
        "unavailable",
        "unverified",
    }
)
_DISPOSITION_TO_STATUS = {
    "complete": "succeeded",
    "block": "blocked",
    "fail": "failed",
}
_DISPOSITION_TO_STATUS.update({
    status: status
    for status in PUBLIC_TERMINAL_STATUSES
    if status != "succeeded"
})
_STATUS_TO_DISPOSITION = {
    "succeeded": "complete",
    "blocked": "block",
    "failed": "fail",
}


def normalize_terminal_status(
    *,
    last_result_status: Any = None,
    terminal_disposition: Any = None,
    task_failed: bool = False,
    cancelled: bool = False,
    explicit_status: Any = None,
) -> str:
    """Reduce established run facts to one public terminal status.

    Success is intentionally asymmetric: only the canonical ``complete``
    disposition can establish it. Explicit or tool-reported success remains
    evidence until completion has been established.
    """

    disposition = _DISPOSITION_TO_STATUS.get(str(terminal_disposition or ""))
    if cancelled:
        return "cancelled"

    explicit = str(explicit_status or "")
    observed = str(last_result_status or "")
    non_success = PUBLIC_TERMINAL_STATUSES - {"succeeded"}

    # Preserve established non-success boundaries, but never manufacture
    # success merely because a caller supplied explicit_status="succeeded".
    if explicit in non_success:
        return explicit
    if observed in non_success:
        return observed
    if disposition in non_success:
        return disposition
    if task_failed:
        return "failed"
    if disposition == "succeeded":
        return "succeeded"
    return "unverified"


@dataclass(frozen=True, slots=True)
class OperationalOutcome:
    terminal_status: str
    requested_effects: tuple[str, ...]
    executed_effects: tuple[str, ...]
    waived_effects: tuple[str, ...]
    pending_effects: tuple[str, ...]
    mutation_occurred: bool
    validation_status: str | None
    rollback_occurred: bool
    blocked_reason: str | None
    failure_reason: str | None
    files_affected: tuple[str, ...]
    evidence_invocation_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "terminal_status": self.terminal_status,
            "requested_effects": list(self.requested_effects),
            "executed_effects": list(self.executed_effects),
            "waived_effects": list(self.waived_effects),
            "pending_effects": list(self.pending_effects),
            "mutation_occurred": self.mutation_occurred,
            "validation_status": self.validation_status,
            "rollback_occurred": self.rollback_occurred,
            "blocked_reason": self.blocked_reason,
            "failure_reason": self.failure_reason,
            "files_affected": list(self.files_affected),
            "evidence_invocation_ids": list(self.evidence_invocation_ids),
        }

    def debug_projection(self) -> dict[str, Any]:
        return {
            # Keep the established task_outcome event vocabulary while using
            # the normalized status as its sole source.
            "status": _STATUS_TO_DISPOSITION.get(self.terminal_status, self.terminal_status),
            "requested_effects": list(self.requested_effects),
            "executed_effects": list(self.executed_effects),
            "waived_effects": list(self.waived_effects),
            "pending_effects": list(self.pending_effects),
            "mutation_occurred": self.mutation_occurred,
            "validation_status": self.validation_status,
            "rollback_occurred": self.rollback_occurred,
        }


def project_operational_outcome(
    state: Any,
    *,
    terminal_status: str | None = None,
    task_failed: bool = False,
    cancelled: bool = False,
) -> OperationalOutcome:
    files: set[str] = set()
    evidence: list[str] = []
    validation_status: str | None = None
    rollback_occurred = False
    mutation_occurred = False
    for entry in getattr(state, "tool_history", ()) or ():
        if not isinstance(entry, dict):
            continue
        result = entry.get("result")
        if not isinstance(result, dict):
            continue
        invocation_id = entry.get("invocation_id") or result.get("invocation_id")
        if invocation_id is not None:
            evidence.append(str(invocation_id))
        artifact = project_artifact_evidence(result)
        files.update(artifact.mutated_files)
        validation_status = artifact.validation_status or validation_status
        rollback_occurred = rollback_occurred or artifact.rollback_occurred
        mutation_occurred = mutation_occurred or artifact.mutation_occurred

    last = getattr(state, "last_result", None)
    last_result = last if isinstance(last, dict) else {}
    normalized_status = normalize_terminal_status(
        explicit_status=terminal_status,
        last_result_status=last_result.get("status"),
        terminal_disposition=getattr(state, "terminal_disposition", None),
        task_failed=task_failed or bool(getattr(state, "_task_failed", False)),
        cancelled=cancelled or bool(getattr(state, "_cancelled", False)),
    )
    reason = last_result.get("error")
    reason_text = str(reason) if reason else None
    blocked_reason = reason_text if normalized_status == "blocked" else None
    failure_reason = reason_text if normalized_status == "failed" else None
    pending_value = getattr(state, "pending_effects", ())
    if callable(pending_value):
        pending_value = pending_value()
    return OperationalOutcome(
        terminal_status=normalized_status,
        requested_effects=tuple(getattr(state, "requested_effects", ()) or ()),
        executed_effects=tuple(getattr(state, "executed_effects", ()) or ()),
        waived_effects=tuple(getattr(state, "waived_effects", ()) or ()),
        pending_effects=tuple(pending_value or ()),
        mutation_occurred=mutation_occurred,
        validation_status=validation_status,
        rollback_occurred=rollback_occurred,
        blocked_reason=blocked_reason,
        failure_reason=failure_reason,
        files_affected=tuple(sorted(files)),
        evidence_invocation_ids=tuple(dict.fromkeys(evidence)),
    )


__all__ = [
    "OperationalOutcome",
    "artifact_metadata",
    "metadata_is_persisted_mutation",
    "normalize_terminal_status",
    "project_operational_outcome",
]
