"""Deterministic projection of final operational truth for one linear task."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent.planning.failure_policy import (
    FailureClass,
    classify_failure,
    local_failure_permitted,
)
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


def _status_is_local_failure(status: Any) -> bool:
    return classify_failure({"status": status}) is FailureClass.LOCAL


def normalize_terminal_status(
    *,
    last_result_status: Any = None,
    terminal_disposition: Any = None,
    task_failed: bool = False,
    cancelled: bool = False,
    explicit_status: Any = None,
    local_failure_permitted: bool = False,
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
    if explicit in non_success and not (
        local_failure_permitted and _status_is_local_failure(explicit)
    ):
        return explicit
    if observed in non_success and not (
        local_failure_permitted and _status_is_local_failure(observed)
    ):
        return observed
    if disposition in non_success:
        return disposition
    if task_failed and not local_failure_permitted:
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
    failed_invocation_ids: tuple[str, ...] = ()
    failed_invocation_statuses: tuple[str, ...] = ()
    unexpected_effects: tuple[str, ...] = ()
    recovered_invocation_ids: tuple[str, ...] = ()
    recovered_local_failure: bool = False
    unrecovered_failure: bool = False
    fallback_resolved: bool = False

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
            "failed_invocation_ids": list(self.failed_invocation_ids),
            "failed_invocation_statuses": list(self.failed_invocation_statuses),
            "unexpected_effects": list(self.unexpected_effects),
            "recovered_invocation_ids": list(self.recovered_invocation_ids),
            "recovered_local_failure": self.recovered_local_failure,
            "unrecovered_failure": self.unrecovered_failure,
            "fallback_resolved": self.fallback_resolved,
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
            "failed_invocation_ids": list(self.failed_invocation_ids),
            "failed_invocation_statuses": list(self.failed_invocation_statuses),
            "unexpected_effects": list(self.unexpected_effects),
            "recovered_invocation_ids": list(self.recovered_invocation_ids),
            "recovered_local_failure": self.recovered_local_failure,
            "unrecovered_failure": self.unrecovered_failure,
            "fallback_resolved": self.fallback_resolved,
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
    failed_invocations: list[str] = []
    failed_invocation_statuses: list[str] = []
    recovered_invocations: list[str] = []
    unrecovered_invocations: list[str] = []
    unrecovered_hard_invocations: list[str] = []
    validation_status: str | None = None
    rollback_occurred = False
    mutation_occurred = False
    for history_index, entry in enumerate(getattr(state, "tool_history", ()) or (), start=1):
        if not isinstance(entry, dict):
            continue
        result = entry.get("result")
        if not isinstance(result, dict):
            continue
        invocation_id = entry.get("invocation_id") or result.get("invocation_id")
        if invocation_id is not None:
            evidence.append(str(invocation_id))
        failure_class = classify_failure(result)
        if failure_class in {FailureClass.LOCAL, FailureClass.HARD}:
            failure_ref = str(invocation_id) if invocation_id is not None else f"history:{history_index}"
            failed_invocations.append(failure_ref)
            failed_invocation_statuses.append(str(result.get("status") or "failed"))
            if callable(getattr(state, "_later_recovery", None)) and state._later_recovery(history_index - 1, entry):
                recovered_invocations.append(failure_ref)
            else:
                unrecovered_invocations.append(failure_ref)
                if failure_class is FailureClass.HARD:
                    unrecovered_hard_invocations.append(failure_ref)
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
        local_failure_permitted=local_failure_permitted(state),
    )
    reason = last_result.get("error")
    reason_text = str(reason) if reason else None
    blocked_reason = reason_text if normalized_status == "blocked" else None
    failure_reason = reason_text if normalized_status == "failed" else None
    pending_value = getattr(state, "pending_effects", ())
    if callable(pending_value):
        pending_value = pending_value()
    recovered_local_failure = bool(recovered_invocations)
    failure_permitted = local_failure_permitted(state)
    unrecovered_failure = bool(unrecovered_hard_invocations) or (
        bool(unrecovered_invocations)
        and not (normalized_status == "succeeded" and failure_permitted)
    ) or (
        normalized_status != "succeeded" and bool(failed_invocations)
    )
    fallback_resolved = normalized_status == "succeeded" and (
        recovered_local_failure
        or bool(getattr(state, "waived_effects", ()) or ())
        or failure_permitted
    )
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
        failed_invocation_ids=tuple(dict.fromkeys(failed_invocations)),
        failed_invocation_statuses=tuple(failed_invocation_statuses),
        unexpected_effects=tuple(
            getattr(state, "unrequested_effects", lambda: ())() or ()
        ),
        recovered_invocation_ids=tuple(dict.fromkeys(recovered_invocations)),
        recovered_local_failure=recovered_local_failure,
        unrecovered_failure=unrecovered_failure,
        fallback_resolved=fallback_resolved,
    )


__all__ = [
    "OperationalOutcome",
    "artifact_metadata",
    "local_failure_permitted",
    "metadata_is_persisted_mutation",
    "normalize_terminal_status",
    "project_operational_outcome",
]
