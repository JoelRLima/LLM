"""Deterministic projection of final operational truth for one linear task."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

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

    Failure/cancellation flags are lifecycle facts and therefore outrank an
    earlier completion disposition.  Otherwise the terminal tool result keeps
    its existing precedence over the task disposition.
    """

    disposition = _DISPOSITION_TO_STATUS.get(str(terminal_disposition or ""))
    if explicit_status is not None:
        status = str(explicit_status or "")
        if status in PUBLIC_TERMINAL_STATUSES:
            return status
    status = str(last_result_status or "")
    if status in PUBLIC_TERMINAL_STATUSES and status != "succeeded":
        return status
    if disposition in {"blocked", "failed"}:
        return disposition
    if cancelled:
        return "cancelled"
    if task_failed:
        return "failed"
    if status == "succeeded":
        return status
    if disposition is not None:
        return disposition
    return "succeeded"


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


def artifact_metadata(result: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(result, dict):
        return ()
    data = result.get("data")
    artifacts = data.get("artifacts") if isinstance(data, dict) else None
    if not isinstance(artifacts, (list, tuple)):
        return ()
    return tuple(
        item["metadata"]
        for item in artifacts
        if isinstance(item, dict) and isinstance(item.get("metadata"), dict)
    )


def metadata_is_persisted_mutation(metadata: dict[str, Any]) -> bool:
    return (
        metadata.get("applied") is True
        and metadata.get("mutation_occurred") is True
        and metadata.get("rollback_occurred") is not True
        and metadata.get("final_state") == "applied"
    )


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
        for metadata in artifact_metadata(result):
            if metadata.get("validation") is not None:
                validation_status = str(metadata["validation"])
            if metadata.get("rollback_occurred") is True:
                rollback_occurred = True
            if (
                metadata.get("applied") is True
                and metadata.get("mutation_occurred") is True
            ):
                mutation_occurred = True
                affected = metadata.get("affected_files")
                if isinstance(affected, (list, tuple)):
                    files.update(str(path) for path in affected)

    last = getattr(state, "last_result", None)
    last_result = last if isinstance(last, dict) else {}
    normalized_status = normalize_terminal_status(
        explicit_status=terminal_status,
        last_result_status=last_result.get("status"),
        terminal_disposition=getattr(state, "terminal_disposition", None),
        task_failed=task_failed,
        cancelled=cancelled,
    )
    reason = last_result.get("error")
    reason_text = str(reason) if reason else None
    blocked_reason = reason_text if normalized_status == "blocked" else None
    failure_reason = reason_text if normalized_status == "failed" else None
    return OperationalOutcome(
        terminal_status=normalized_status,
        requested_effects=tuple(getattr(state, "requested_effects", ()) or ()),
        executed_effects=tuple(getattr(state, "executed_effects", ()) or ()),
        waived_effects=tuple(getattr(state, "waived_effects", ()) or ()),
        pending_effects=tuple(getattr(state, "pending_effects", lambda: ())()),
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
