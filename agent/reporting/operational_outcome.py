"""Deterministic projection of final operational truth for one linear task."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


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
            "status": self.terminal_status,
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


def project_operational_outcome(state: Any) -> OperationalOutcome:
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
    disposition = getattr(state, "terminal_disposition", None)
    terminal_status = str(disposition or last_result.get("status") or "unknown")
    reason = last_result.get("error")
    reason_text = str(reason) if reason else None
    blocked_reason = reason_text if terminal_status == "block" else None
    failure_reason = reason_text if terminal_status == "fail" else None
    return OperationalOutcome(
        terminal_status=terminal_status,
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
    "project_operational_outcome",
]
