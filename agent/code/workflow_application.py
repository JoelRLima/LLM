from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any, Optional, Sequence
from uuid import uuid4

from agent.approval import ApprovalDecision
from agent.code.changes import ChangeConflictError, ChangeSet, ChangeSetError, ChangeSetTransaction
from agent.code.diagnostics import _failure_result
from agent.code.discovery import ProjectDiscovery
from agent.code.policy import ChangeApprover
from agent.code.validation import ValidationStatus
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

def apply_changes(
    service: Any, change_set: ChangeSet, *, include_tests: bool = False,
    requested_targets: Sequence[str] = (), approver: Optional[ChangeApprover] = None,
) -> TaskResult:
    """Apply one ChangeSet and project structured failure facts.
    Validation and approval outcomes retain their canonical failure codes."""
    transaction = ChangeSetTransaction(service.root, change_set)
    try:
        preview = transaction.prepare()
    except ChangeSetError as exc:
        return _failure_result(code="TOOL_ERROR", diagnostics=({"code": "CHANGESET_CONFLICT" if isinstance(exc, ChangeConflictError) else "CHANGESET_ERROR", "message": str(exc)},), error=str(exc))
    assessment = service.approval_policy.assess(service.root, change_set, requested_targets)
    approval = _approval_authority(preview, assessment, approver)
    artifact = _artifact(preview, assessment, applied=False, approval=approval)
    approval_result = _approval_result(
        preview,
        assessment,
        artifact,
        approver,
        approval=approval,
    )
    if approval_result is not None:
        return approval_result
    try:
        transaction.commit()
    except ChangeSetError as exc:
        rolled_back = transaction.change_set.state.value == "rolled_back"
        artifact = _artifact(
            preview,
            assessment,
            applied=rolled_back,
            rollback_occurred=rolled_back,
            final_state="restored" if rolled_back else "unknown",
            approval=approval,
        )
        return _failure_result(code="TOOL_ERROR", artifacts=(artifact,), diagnostics=({"code": "CHANGESET_CONFLICT" if isinstance(exc, ChangeConflictError) else "CHANGESET_ERROR", "message": str(exc)},), error=str(exc), metadata={"approval": approval.metadata})
    task_workspace = getattr(service.context, "metadata", {}).get("workspace_manager")
    register_transaction = getattr(task_workspace, "register_transaction", None)
    if callable(register_transaction):
        register_transaction(transaction)
    validation_invocation_id = str(uuid4())
    report = service.validator.validate(
        ProjectDiscovery(service.root).discover(), preview.affected_files, include_tests=include_tests
    )
    diagnostics = tuple(service._diagnostic_dict(item) for item in report.diagnostics)
    artifact = _artifact(
        preview,
        assessment,
        applied=True,
        validation=report.status.value,
        validation_invocation_id=validation_invocation_id,
        rollback_occurred=False,
        final_state="applied",
        approval=approval,
    )
    if report.status == ValidationStatus.PASSED:
        transaction.mark_validated()
        return TaskResult(
            TaskStatus.SUCCEEDED,
            summary=f"ChangeSet aplicado e validado em {len(preview.affected_files)} arquivo(s).",
            artifacts=(artifact,),
            diagnostics=diagnostics,
            metadata={"approval": approval.metadata},
        )
    if report.status == ValidationStatus.UNAVAILABLE:
        if approval.mode != "explicit_approved" or not _allow_unverified_approved(service):
            rollback_ok, rollback_error = _rollback_transaction(transaction)
            artifact = _artifact(
                preview,
                assessment,
                applied=True,
                validation=report.status.value,
                validation_invocation_id=validation_invocation_id,
                rollback_occurred=True,
                final_state="restored" if rollback_ok else "unknown",
                approval=approval,
            )
            return _failure_result(
                code="TOOL_UNAVAILABLE",
                summary=(
                    "Validação indisponível; alterações revertidas."
                    if rollback_ok
                    else "Validação indisponível; a restauração das alterações não foi confirmada."
                ),
                artifacts=(artifact,),
                diagnostics=diagnostics,
                error=rollback_error or "validation:unavailable",
                metadata={"approval": approval.metadata},
            )
        return TaskResult(
            TaskStatus.UNVERIFIED,
            summary="ChangeSet aplicado com aprovação explícita, mas não há validação disponível.",
            artifacts=(artifact,),
            diagnostics=diagnostics,
            metadata={"approval": approval.metadata},
        )
    rollback_ok, rollback_error = _rollback_transaction(transaction)
    artifact = _artifact(
        preview,
        assessment,
        applied=True,
        validation=report.status.value,
        validation_invocation_id=validation_invocation_id,
        rollback_occurred=True,
        final_state="restored" if rollback_ok else "unknown",
        approval=approval,
    )
    status = TaskStatus.CANCELLED if report.status == ValidationStatus.CANCELLED else TaskStatus.FAILED
    return _failure_result(
        status=status,
        summary=(
            "Validação falhou; mudanças revertidas."
            if rollback_ok
            else "Validação falhou; a restauração das alterações não foi confirmada."
        ),
        artifacts=(artifact,),
        diagnostics=diagnostics,
        error=(rollback_error or f"validation:{report.status.value}")
        if not rollback_ok
        else f"validation:{report.status.value}",
        metadata={"approval": approval.metadata},
        code="TOOL_UNAVAILABLE" if report.status == ValidationStatus.UNAVAILABLE else "TIMEOUT" if report.status == ValidationStatus.TIMED_OUT else "CANCELLED" if report.status == ValidationStatus.CANCELLED else "TOOL_ERROR",
    )


def _rollback_transaction(transaction: Any) -> tuple[bool, str | None]:
    try:
        success = transaction.rollback()
    except Exception as exc:
        return False, f"rollback:{exc}"
    if success is False:
        return False, "rollback:incomplete"
    return True, None


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
