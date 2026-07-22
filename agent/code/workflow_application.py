from __future__ import annotations

from dataclasses import asdict
from typing import Any, Optional, Sequence

from agent.code.changes import ChangeSet, ChangeSetError, ChangeSetTransaction
from agent.code.discovery import ProjectDiscovery
from agent.code.policy import ChangeApprover
from agent.code.validation import ValidationStatus
from agent.runtime.context import Artifact, TaskResult, TaskStatus


def apply_changes(
    service: Any, change_set: ChangeSet, *, include_tests: bool = False,
    requested_targets: Sequence[str] = (), approver: Optional[ChangeApprover] = None,
) -> TaskResult:
    transaction = ChangeSetTransaction(service.root, change_set)
    try:
        preview = transaction.prepare()
    except ChangeSetError as exc:
        return TaskResult(TaskStatus.FAILED, error=str(exc))
    assessment = service.approval_policy.assess(service.root, change_set, requested_targets)
    artifact = _artifact(preview, assessment, applied=False)
    approval_result = _approval_result(preview, assessment, artifact, approver)
    if approval_result is not None:
        return approval_result
    try:
        transaction.commit()
    except ChangeSetError as exc:
        return TaskResult(TaskStatus.FAILED, artifacts=(artifact,), error=str(exc))
    report = service.validator.validate(
        ProjectDiscovery(service.root).discover(), preview.affected_files, include_tests=include_tests
    )
    diagnostics = tuple(service._diagnostic_dict(item) for item in report.diagnostics)
    artifact = _artifact(preview, assessment, applied=True, validation=report.status.value)
    if report.status == ValidationStatus.PASSED:
        transaction.mark_validated()
        return TaskResult(TaskStatus.SUCCEEDED, summary=f"ChangeSet aplicado e validado em {len(preview.affected_files)} arquivo(s).", artifacts=(artifact,), diagnostics=diagnostics)
    if report.status == ValidationStatus.UNAVAILABLE:
        return TaskResult(TaskStatus.UNVERIFIED, summary="ChangeSet aplicado, mas não há validação disponível.", artifacts=(artifact,), diagnostics=diagnostics)
    transaction.rollback()
    status = TaskStatus.CANCELLED if report.status == ValidationStatus.CANCELLED else TaskStatus.FAILED
    return TaskResult(status, summary="Validação falhou; alterações revertidas.", artifacts=(artifact,), diagnostics=diagnostics, error=f"validation:{report.status.value}")


def _artifact(preview: Any, assessment: Any, *, applied: bool, validation: str | None = None) -> Artifact:
    metadata = {
        "change_set_id": preview.change_set_id,
        "affected_files": preview.affected_files,
        "confidence": assessment.confidence,
        "confidence_reasons": assessment.reasons,
        "requires_confirmation": assessment.requires_confirmation,
        "applied": applied,
    }
    if validation is not None:
        metadata["validation"] = validation
    return Artifact("changeset", content=preview.diff, metadata=metadata)


def _approval_result(preview: Any, assessment: Any, artifact: Artifact, approver: Any) -> TaskResult | None:
    if not assessment.requires_confirmation:
        return None
    if approver is None:
        return TaskResult(
            TaskStatus.BLOCKED, summary="ChangeSet de baixa confiança aguarda confirmação.",
            artifacts=(artifact,), error="confirmation_required", metadata={"assessment": asdict(assessment)},
        )
    if approver.approve(preview, assessment):
        return None
    return TaskResult(
        TaskStatus.CANCELLED, summary="ChangeSet rejeitado pelo usuário.",
        artifacts=(artifact,), error="approval_rejected", metadata={"assessment": asdict(assessment)},
    )
