"""Canonical terminal/run outcome projection.

Execution and planning consume this runtime owner. Reporting modules expose a
compatibility import, but do not define terminal truth.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from agent.runtime.failure_policy import (
    FailureClass,
    classify_failure,
    local_failure_permitted,
)
from agent.runtime.mutation_evidence import (
    artifact_metadata,
    metadata_is_persisted_mutation,
)
from agent.runtime.operational_outcome_evidence import (
    collect_operational_evidence,
    has_canonical_commit_incident,
)
from agent.runtime.outcome_taxonomy import (
    NON_SUCCESS_STATUSES,
    operational_status_for,
)

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
    """Reduce established run facts to one public terminal status."""

    disposition = operational_status_for(terminal_disposition)
    if cancelled:
        return "cancelled"

    explicit = operational_status_for(explicit_status) or ""
    observed = operational_status_for(last_result_status) or ""
    non_success = NON_SUCCESS_STATUSES
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
    physical_effect_unknown: bool = False
    surviving_files: tuple[str, ...] = ()
    rollback_succeeded: bool | None = None

    @property
    def final_state(self) -> str | None:
        if self.rollback_occurred:
            return "restored" if self.rollback_succeeded is True else "unknown"
        return "applied" if self.mutation_occurred else None

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
            "physical_effect_unknown": self.physical_effect_unknown,
            "surviving_files": list(self.surviving_files),
            "rollback_succeeded": self.rollback_succeeded,
            "final_state": self.final_state,
        }

    def debug_projection(self) -> dict[str, Any]:
        return {
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
            "physical_effect_unknown": self.physical_effect_unknown,
            "surviving_files": list(self.surviving_files),
            "rollback_succeeded": self.rollback_succeeded,
            "final_state": self.final_state,
        }


def project_operational_outcome(
    state: Any,
    *,
    terminal_status: str | None = None,
    task_failed: bool = False,
    cancelled: bool = False,
) -> OperationalOutcome:
    facts = collect_operational_evidence(state)
    last = getattr(state, "last_result", None)
    last_result = last if isinstance(last, Mapping) else {}
    normalized_status = normalize_terminal_status(
        explicit_status=terminal_status,
        last_result_status=last_result.get("status"),
        terminal_disposition=getattr(state, "terminal_disposition", None),
        task_failed=task_failed or bool(getattr(state, "_task_failed", False)),
        cancelled=cancelled or bool(getattr(state, "_cancelled", False)),
        local_failure_permitted=local_failure_permitted(state),
    )
    if facts.incident_present and normalized_status == "succeeded":
        normalized_status = "unverified"
    reason = last_result.get("error")
    reason_text = str(reason) if reason else None
    blocked_reason = reason_text if normalized_status == "blocked" else None
    failure_reason = reason_text if normalized_status == "failed" else None
    pending_value = getattr(state, "pending_effects", ())
    if callable(pending_value):
        pending_value = pending_value()
    recovered_local_failure = bool(facts.recovered_invocations)
    failure_permitted = local_failure_permitted(state)
    unrecovered_failure = bool(facts.unrecovered_hard_invocations) or (
        bool(facts.unrecovered_invocations)
        and not (normalized_status == "succeeded" and failure_permitted)
    ) or (
        normalized_status != "succeeded" and bool(facts.failed_invocations)
    ) or facts.incident_present
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
        mutation_occurred=facts.mutation_occurred,
        validation_status=facts.validation_status,
        rollback_occurred=facts.rollback_occurred,
        blocked_reason=blocked_reason,
        failure_reason=failure_reason,
        files_affected=tuple(sorted(facts.files)),
        evidence_invocation_ids=tuple(dict.fromkeys(facts.invocation_ids)),
        failed_invocation_ids=tuple(dict.fromkeys(facts.failed_invocations)),
        failed_invocation_statuses=tuple(facts.failed_statuses),
        unexpected_effects=tuple(
            getattr(state, "unrequested_effects", lambda: ())() or ()
        ),
        recovered_invocation_ids=tuple(dict.fromkeys(facts.recovered_invocations)),
        recovered_local_failure=recovered_local_failure,
        unrecovered_failure=unrecovered_failure,
        fallback_resolved=fallback_resolved,
        physical_effect_unknown=facts.physical_effect_unknown,
        surviving_files=tuple(sorted(facts.surviving_files)),
        rollback_succeeded=facts.rollback_succeeded,
    )


__all__ = [
    "OperationalOutcome",
    "artifact_metadata",
    "local_failure_permitted",
    "has_canonical_commit_incident",
    "metadata_is_persisted_mutation",
    "normalize_terminal_status",
    "project_operational_outcome",
]
