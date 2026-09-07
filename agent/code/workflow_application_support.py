"""Approval and artifact projections for transactional code application."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any

from agent.approval import ApprovalDecision
from agent.code.diagnostics import _failure_result
from agent.runtime.context import Artifact, TaskResult, TaskStatus

DEFAULT_ALLOW_UNVERIFIED_APPROVED = True

@dataclass(frozen=True)
class _ApprovalAuthority:
    """Approval/autonomy facts bound to one immutable preview identity."""

    mode: str
    decision: str | None
    explicit: bool
    change_set_id: str
    preview_sha256: str

    @property
    def metadata(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "decision": self.decision,
            "explicit": self.explicit,
            "change_set_id": self.change_set_id,
            "preview_sha256": self.preview_sha256,
        }


def _preview_sha256(preview: Any) -> str:
    material = json.dumps(
        {
            "change_set_id": str(getattr(preview, "change_set_id", "")),
            "affected_files": list(getattr(preview, "affected_files", ()) or ()),
            "diff": str(getattr(preview, "diff", "")),
            "mutation_occurred": bool(getattr(preview, "mutation_occurred", False)),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _approval_authority(
    preview: Any,
    assessment: Any,
    approver: Any,
) -> _ApprovalAuthority:
    explicit_required = (
        approver is not None
        and getattr(approver, "requires_explicit_approval", False) is True
    )
    requires_confirmation = bool(
        getattr(assessment, "requires_confirmation", False) or explicit_required
    )
    decision: str | None = None
    if not requires_confirmation:
        mode = "approval_not_required" if approver is not None else "autonomous"
        explicit = False
    elif approver is None:
        mode = "approval_pending"
        explicit = False
        decision = ApprovalDecision.REQUIRED.value
    else:
        raw_decision = approver.approve(preview, assessment)
        if raw_decision is True or raw_decision is ApprovalDecision.APPROVED:
            mode = "explicit_approved"
            explicit = True
            decision = ApprovalDecision.APPROVED.value
        elif raw_decision is ApprovalDecision.REQUIRED:
            mode = "approval_pending"
            explicit = False
            decision = ApprovalDecision.REQUIRED.value
        else:
            mode = "approval_rejected"
            explicit = False
            decision = ApprovalDecision.REJECTED.value
    return _ApprovalAuthority(
        mode=mode,
        decision=decision,
        explicit=explicit,
        change_set_id=str(getattr(preview, "change_set_id", "")),
        preview_sha256=_preview_sha256(preview),
    )
def _allow_unverified_approved(service: Any) -> bool:
    """Read the narrowly-scoped product policy for approved unverified writes."""

    config = getattr(service, "validation_config", None)
    if not isinstance(config, Mapping):
        return DEFAULT_ALLOW_UNVERIFIED_APPROVED
    value = config.get(
        "allow_unverified_approved",
        DEFAULT_ALLOW_UNVERIFIED_APPROVED,
    )
    return value if isinstance(value, bool) else DEFAULT_ALLOW_UNVERIFIED_APPROVED


def _noop_artifact(preview: Any) -> Artifact:
    return Artifact(
        "changeset",
        content="",
        metadata={
            "change_set_id": preview.change_set_id,
            "affected_files": preview.affected_files,
            "mutation_attempted": True,
            "mutation_occurred": False,
            "persisted_mutation": False,
            "surviving_mutation": False,
            "final_state": "no_change",
        },
    )
def _rollback_transaction(transaction: Any) -> tuple[bool, str | None]:
    try:
        success = transaction.rollback()
    except Exception as exc:
        return False, f"rollback:{exc}"
    if success is False:
        return False, "rollback:incomplete"
    return True, None


def _register_transaction(service: Any, transaction: Any) -> None:
    task_workspace = getattr(service.context, "metadata", {}).get("workspace_manager")
    register_transaction = getattr(task_workspace, "register_transaction", None)
    if callable(register_transaction):
        register_transaction(transaction)


def _artifact(
    preview: Any,
    assessment: Any,
    *,
    applied: bool,
    validation: str | None = None,
    validation_invocation_id: str | None = None,
    rollback_occurred: bool = False,
    final_state: str | None = None,
    approval: _ApprovalAuthority | None = None,
    validation_metadata: Mapping[str, object] | None = None,
) -> Artifact:
    approval_metadata = approval.metadata if approval is not None else None
    metadata = {
        "change_set_id": preview.change_set_id,
        "affected_files": preview.affected_files,
        "confidence": assessment.confidence,
        "confidence_reasons": assessment.reasons,
        "requires_confirmation": assessment.requires_confirmation,
        "mutation_occurred": preview.mutation_occurred,
        "applied": applied,
        "rollback_occurred": rollback_occurred,
        "persisted_mutation": (
            applied
            and preview.mutation_occurred
            and not rollback_occurred
            and final_state == "applied"
        ),
        "surviving_mutation": (
            applied
            and preview.mutation_occurred
            and (
                final_state == "applied"
                or (rollback_occurred and final_state != "restored")
            )
        ),
    }
    if approval_metadata is not None:
        metadata["approval"] = approval_metadata
        metadata["approval_mode"] = approval_metadata["mode"]
        metadata["approval_decision"] = approval_metadata["decision"]
        metadata["approval_change_set_id"] = approval_metadata["change_set_id"]
        metadata["approval_preview_sha256"] = approval_metadata["preview_sha256"]
    if final_state is not None:
        metadata["final_state"] = final_state
    if validation is not None:
        metadata["validation"] = validation
    if validation_invocation_id is not None:
        metadata["validation_invocation_id"] = validation_invocation_id
    if validation_metadata is not None:
        metadata["validation_metadata"] = dict(validation_metadata)
    return Artifact("changeset", content=preview.diff, metadata=metadata)


def _approval_result(
    preview: Any,
    assessment: Any,
    artifact: Artifact,
    approver: Any,
    *,
    approval: _ApprovalAuthority | None = None,
) -> TaskResult | None:
    return _approval_result_v2(
        preview,
        assessment,
        artifact,
        approver,
        approval=approval,
    )


def _approval_result_v2(
    preview: Any,
    assessment: Any,
    artifact: Artifact,
    approver: Any,
    *,
    approval: _ApprovalAuthority | None = None,
) -> TaskResult | None:
    authority = approval or _approval_authority(preview, assessment, approver)
    if authority.mode in {"autonomous", "approval_not_required", "explicit_approved"}:
        return None
    if authority.mode == "approval_pending":
        return _failure_result(
            status=TaskStatus.BLOCKED,
            code="APPROVAL_REQUIRED",
            summary="ChangeSet aguarda confirma\u00e7\u00e3o expl\u00edcita.",
            artifacts=(artifact,),
            error="confirmation_required",
            metadata={"assessment": asdict(assessment), "approval": authority.metadata},
        )
    return _failure_result(
        status=TaskStatus.CANCELLED,
        code="CANCELLED",
        summary="ChangeSet rejeitado pelo usu\u00e1rio.",
        artifacts=(artifact,),
        error="approval_rejected",
        metadata={"assessment": asdict(assessment), "approval": authority.metadata},
    )
