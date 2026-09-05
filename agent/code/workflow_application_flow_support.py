"""Control flow for transactional code application."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from agent.code.changes import ChangeConflictError, ChangeSet, ChangeSetError
from agent.code.diagnostics import _failure_result
from agent.code.outcome_verifier import CodeOutcomeVerdict, CodeProposalKind
from agent.runtime.context import TaskResult, TaskStatus

from .workflow_application_support import (
    _approval_authority,
    _approval_result,
    _artifact,
    _noop_artifact,
)
from .workflow_application_validation_support import _validate_and_project


def _change_error_code(error: BaseException) -> str:
    return "CHANGESET_CONFLICT" if isinstance(error, ChangeConflictError) else "CHANGESET_ERROR"


def _prepare_change_set(
    service: Any,
    change_set: ChangeSet,
    transaction_factory: Any,
) -> tuple[Any, Any] | TaskResult:
    transaction = transaction_factory(service.root, change_set)
    try:
        preview = transaction.prepare()
    except ChangeSetError as exc:
        return _failure_result(
            code="TOOL_ERROR",
            diagnostics=({"code": _change_error_code(exc), "message": str(exc)},),
            error=str(exc),
        )
    if not preview.mutation_occurred:
        return _failure_result(
            code="CODE_CHANGE_NOOP",
            summary="A proposta não produziria mudança; ela não foi aprovada nem aplicada.",
            diagnostics=({"code": "CODE_CHANGE_NOOP"},),
            artifacts=(_noop_artifact(preview),),
            error="CODE_CHANGE_NOOP",
        )
    return transaction, preview


def _pre_apply_verification(
    service: Any,
    change_set: ChangeSet,
    preview: Any,
    assessment: Any,
    requested_targets: Sequence[str],
    evidence_manifest: Any,
    repair_attempt: bool,
    *,
    outcome_verifier_factory: Any,
    prepared_change_evidence_factory: Any,
    requires_selective_verification: Any,
) -> TaskResult | None:
    verification_targets = tuple(requested_targets) or tuple(
        getattr(preview, "affected_files", ()) or ()
    )
    if not requires_selective_verification(
        change_set,
        preview,
        verification_targets,
        assessment,
        repair_attempt,
    ):
        return None
    if evidence_manifest is None:
        return _failure_result(
            status=TaskStatus.BLOCKED,
            code="CODE_VERIFICATION_INSUFFICIENT",
            summary="A verificaÃ§Ã£o seletiva exige manifesto de evidÃªncia runtime.",
            artifacts=(_artifact(preview, assessment, applied=False),),
            error="CODE_VERIFICATION_INSUFFICIENT",
        )
    cancellation = getattr(getattr(service, "context", None), "cancellation", None)
    if getattr(cancellation, "cancelled", False):
        return _failure_result(
            status=TaskStatus.CANCELLED,
            code="CANCELLED",
            summary="A operação foi cancelada antes da verificação.",
            artifacts=(_artifact(preview, assessment, applied=False),),
            error="cancelled",
        )
    verification = outcome_verifier_factory(service.context, service.root).verify(
        change_set.objective,
        CodeProposalKind.CHANGE,
        evidence_manifest,
        target_paths=requested_targets,
        prepared_records=prepared_change_evidence_factory(
            change_set.changes,
            preview,
        ),
    )
    if verification.verdict is CodeOutcomeVerdict.SUPPORTED:
        return None
    code = (
        "CODE_OUTCOME_CONTRADICTED"
        if verification.verdict is CodeOutcomeVerdict.CONTRADICTED
        else verification.failure_code or "CODE_VERIFICATION_INSUFFICIENT"
    )
    status = (
        TaskStatus.FAILED
        if code in {"CODE_OUTCOME_CONTRADICTED", "CODE_EVIDENCE_STALE"}
        else TaskStatus.BLOCKED
    )
    return _failure_result(
        status=status,
        code=code,
        summary=verification.reason,
        artifacts=(_artifact(preview, assessment, applied=False),),
        error=code,
        metadata={"cited_evidence_ids": list(verification.evidence_ids)},
    )


def _approval_and_commit(
    transaction: Any,
    preview: Any,
    assessment: Any,
    approver: Any,
) -> tuple[Any, TaskResult | None]:
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
        return approval, approval_result
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
        return approval, _failure_result(
            code="TOOL_ERROR",
            artifacts=(artifact,),
            diagnostics=({"code": _change_error_code(exc), "message": str(exc)},),
            error=str(exc),
            metadata={"approval": approval.metadata},
        )
    return approval, None


def run_apply_changes(
    service: Any,
    change_set: ChangeSet,
    *,
    include_tests: bool = False,
    requested_targets: Sequence[str] = (),
    approver: Any = None,
    evidence_manifest: Any = None,
    repair_attempt: bool = False,
    transaction_factory: Any,
    outcome_verifier_factory: Any,
    prepared_change_evidence_factory: Any,
    validate_model_code_task: Any,
    requires_selective_verification: Any,
) -> TaskResult:
    prepared = _prepare_change_set(service, change_set, transaction_factory)
    if isinstance(prepared, TaskResult):
        return prepared
    transaction, preview = prepared
    assessment = service.approval_policy.assess(service.root, change_set, requested_targets)
    verification_result = _pre_apply_verification(
        service,
        change_set,
        preview,
        assessment,
        requested_targets,
        evidence_manifest,
        repair_attempt,
        outcome_verifier_factory=outcome_verifier_factory,
        prepared_change_evidence_factory=prepared_change_evidence_factory,
        requires_selective_verification=requires_selective_verification,
    )
    if verification_result is not None:
        return verification_result
    approval, commit_result = _approval_and_commit(
        transaction,
        preview,
        assessment,
        approver,
    )
    if commit_result is not None:
        return commit_result
    task_workspace = getattr(service.context, "metadata", {}).get("workspace_manager")
    register_transaction = getattr(task_workspace, "register_transaction", None)
    if callable(register_transaction):
        register_transaction(transaction)
    return _validate_and_project(
        service,
        transaction,
        preview,
        assessment,
        approval,
        include_tests=include_tests,
        validate_model_code_task=validate_model_code_task,
    )
