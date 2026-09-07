"""Control flow for transactional code application."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Callable

from agent.code.changes import ChangeConflictError, ChangeSet, ChangeSetError
from agent.code.diagnostics import _failure_result
from agent.runtime.context import TaskResult, TaskStatus

from .mutation_binding import (
    MutationBindingError,
    assert_changeset_admitted,
    assert_result_mutation_admitted,
)
from .workflow_application_support import (
    _approval_authority,
    _approval_result,
    _artifact,
    _noop_artifact,
    _register_transaction,
    _rollback_transaction,
)
from .workflow_application_validation_support import (
    _pre_apply_verification,
    _validate_and_project,
)


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


def _approval_and_commit(
    transaction: Any,
    preview: Any,
    assessment: Any,
    approver: Any,
    *,
    pre_commit_guard: Callable[[], None] | None = None,
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
        if pre_commit_guard is not None:
            pre_commit_guard()
        transaction.commit()
    except MutationBindingError as exc:
        return approval, _failure_result(
            status=TaskStatus.BLOCKED,
            code="AUTHORITY_DENIED",
            artifacts=(_artifact(preview, assessment, applied=False, approval=approval),),
            diagnostics=(
                {
                    "code": exc.code,
                    "message": str(exc),
                },
            ),
            error=str(exc),
            metadata={"approval": approval.metadata},
        )
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
    # Authority is checked against the proposed physical paths before the
    # transaction is even staged.  Approval is intentionally downstream of
    # this boundary and cannot enlarge the admitted target set.
    try:
        assert_changeset_admitted(service, change_set)
    except MutationBindingError as exc:
        return _failure_result(
            status=TaskStatus.BLOCKED,
            code="AUTHORITY_DENIED",
            diagnostics=({"code": exc.code, "message": str(exc)},),
            error=str(exc),
        )
    prepared = _prepare_change_set(service, change_set, transaction_factory)
    if isinstance(prepared, TaskResult):
        return prepared
    transaction, preview = prepared
    try:
        assert_changeset_admitted(service, change_set, preview=preview)
    except MutationBindingError as exc:
        return _failure_result(
            status=TaskStatus.BLOCKED,
            code="AUTHORITY_DENIED",
            diagnostics=({"code": exc.code, "message": str(exc)},),
            error=str(exc),
        )
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
    try:
        # Revalidation happens immediately before the approval/commit boundary;
        # stale grounding is never converted into a user approval question.
        assert_changeset_admitted(
            service,
            change_set,
            preview=preview,
            revalidate=True,
        )
    except MutationBindingError as exc:
        return _failure_result(
            status=TaskStatus.BLOCKED,
            code="AUTHORITY_DENIED",
            diagnostics=({"code": exc.code, "message": str(exc)},),
            artifacts=(_artifact(preview, assessment, applied=False),),
            error=str(exc),
        )
    context_metadata = getattr(getattr(service, "context", None), "metadata", {})
    admitted_intent = (
        context_metadata.get("admitted_intent")
        if isinstance(context_metadata, Mapping)
        else None
    )
    if bool(getattr(admitted_intent, "proposal_only", False)):
        # The preview above is safe staging only.  This trusted owner blocks
        # the durable boundary itself, so an approving UI or custom approver
        # cannot widen proposal-only intent into commit authority.
        return _failure_result(
            status=TaskStatus.BLOCKED,
            code="PROPOSAL_ONLY_MUTATION_FORBIDDEN",
            summary="A intent proposal_only permite a proposta, mas proibe a aplicacao duravel.",
            artifacts=(_artifact(preview, assessment, applied=False, final_state="proposed"),),
            error="PROPOSAL_ONLY_MUTATION_FORBIDDEN",
        )

    def post_approval_guard() -> None:
        """Revalidate W14 authority after approval and before commit."""

        assert_changeset_admitted(
            service,
            change_set,
            preview=preview,
            revalidate=True,
        )

    approval, commit_result = _approval_and_commit(
        transaction,
        preview,
        assessment,
        approver,
        pre_commit_guard=post_approval_guard,
    )
    if commit_result is not None:
        return commit_result
    _register_transaction(service, transaction)
    result = _validate_and_project(
        service,
        transaction,
        preview,
        assessment,
        approval,
        include_tests=include_tests,
        validate_model_code_task=validate_model_code_task,
    )
    try:
        assert_result_mutation_admitted(service, result)
    except MutationBindingError as exc:
        rollback_ok, rollback_error = _rollback_transaction(transaction)
        return _failure_result(
            status=TaskStatus.BLOCKED,
            code="AUTHORITY_DENIED",
            diagnostics=({"code": exc.code, "message": str(exc)},),
            artifacts=(
                _artifact(
                    preview,
                    assessment,
                    applied=True,
                    rollback_occurred=True,
                    final_state="restored" if rollback_ok else "unknown",
                    approval=approval,
                ),
            ),
            error=rollback_error or str(exc),
        )
    return result
