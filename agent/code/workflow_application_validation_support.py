"""Validation projection helpers for transactional code application."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from agent.code.discovery import ProjectDiscovery
from agent.code.outcome_verifier import CodeOutcomeVerdict, CodeProposalKind
from agent.code.validation import ValidationStatus
from agent.runtime.context import TaskResult, TaskStatus
from agent.runtime.correlation import new_runtime_id

from .workflow_application_support import (
    _allow_unverified_approved,
    _artifact,
    _failure_result,
    _rollback_transaction,
)


def _validation_metadata(report: Any, include_tests: bool) -> tuple[Any, str, dict[str, Any]]:
    execution_status = report.status
    effective_status = getattr(report, "effective_status", execution_status)
    validation_metadata = getattr(
        report,
        "metadata",
        {
            "execution_status": execution_status.value,
            "effective_status": effective_status.value,
            "tests_requested": bool(include_tests),
            "test_coverage": "not_requested" if not include_tests else "unavailable",
            "plan_fingerprint": None,
            "selections": [],
        },
    )
    return effective_status, new_runtime_id(), validation_metadata


def _unavailable_validation_result(
    service: Any,
    transaction: Any,
    preview: Any,
    assessment: Any,
    approval: Any,
    artifact: Any,
    diagnostics: tuple[dict[str, Any], ...],
    validation_metadata: dict[str, Any],
    validation_invocation_id: str,
) -> TaskResult:
    if approval.mode != "explicit_approved" or not _allow_unverified_approved(service):
        rollback_ok, rollback_error = _rollback_transaction(transaction)
        artifact = _artifact(
            preview,
            assessment,
            applied=True,
            validation=ValidationStatus.UNAVAILABLE.value,
            validation_invocation_id=validation_invocation_id,
            rollback_occurred=True,
            final_state="restored" if rollback_ok else "unknown",
            approval=approval,
            validation_metadata=validation_metadata,
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
            metadata={"approval": approval.metadata, "validation": validation_metadata},
        )
    return TaskResult(
        TaskStatus.UNVERIFIED,
        summary="ChangeSet aplicado com aprovação explícita, mas não há validação disponível.",
        artifacts=(artifact,),
        diagnostics=diagnostics,
        metadata={"approval": approval.metadata, "validation": validation_metadata},
    )

def _validation_code(status: Any) -> str:
    if status is ValidationStatus.UNAVAILABLE:
        return "TOOL_UNAVAILABLE"
    if status is ValidationStatus.TIMED_OUT:
        return "TIMEOUT"
    if status is ValidationStatus.CANCELLED:
        return "CANCELLED"
    return "TOOL_ERROR"


def _failed_validation_result(
    transaction: Any,
    preview: Any,
    assessment: Any,
    approval: Any,
    diagnostics: tuple[dict[str, Any], ...],
    validation_metadata: dict[str, Any],
    validation_invocation_id: str,
    report: Any,
    effective_status: Any,
) -> TaskResult:
    rollback_ok, rollback_error = _rollback_transaction(transaction)
    artifact = _artifact(
        preview,
        assessment,
        applied=True,
        validation=effective_status.value,
        validation_invocation_id=validation_invocation_id,
        rollback_occurred=True,
        final_state="restored" if rollback_ok else "unknown",
        approval=approval,
        validation_metadata=validation_metadata,
    )
    return _failure_result(
        status=TaskStatus.CANCELLED if effective_status == ValidationStatus.CANCELLED else TaskStatus.FAILED,
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
        metadata={"approval": approval.metadata, "validation": validation_metadata},
        code=_validation_code(effective_status),
    )


def _pre_apply_verification(
    service: Any,
    change_set: Any,
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


def _validate_and_project(
    service: Any,
    transaction: Any,
    preview: Any,
    assessment: Any,
    approval: Any,
    *,
    include_tests: bool,
    validate_model_code_task: Any,
) -> TaskResult:
    validation_invocation_id = new_runtime_id()
    report = validate_model_code_task(
        service.validator,
        ProjectDiscovery(service.root).discover(),
        preview.affected_files,
        include_tests=include_tests,
    )
    diagnostics = tuple(service._diagnostic_dict(item) for item in report.diagnostics)
    effective_status, generated_id, validation_metadata = _validation_metadata(
        report,
        include_tests,
    )
    validation_invocation_id = generated_id
    artifact = _artifact(
        preview,
        assessment,
        applied=True,
        validation=effective_status.value,
        validation_invocation_id=validation_invocation_id,
        rollback_occurred=False,
        final_state="applied",
        approval=approval,
        validation_metadata=validation_metadata,
    )
    if effective_status is ValidationStatus.PASSED:
        transaction.mark_validated()
        return TaskResult(
            TaskStatus.SUCCEEDED,
            summary=f"ChangeSet aplicado e validado em {len(preview.affected_files)} arquivo(s).",
            artifacts=(artifact,),
            diagnostics=diagnostics,
            metadata={"approval": approval.metadata, "validation": validation_metadata},
        )
    if effective_status is ValidationStatus.UNAVAILABLE:
        return _unavailable_validation_result(
            service,
            transaction,
            preview,
            assessment,
            approval,
            artifact,
            diagnostics,
            validation_metadata,
            validation_invocation_id,
        )
    return _failed_validation_result(
        transaction,
        preview,
        assessment,
        approval,
        diagnostics,
        validation_metadata,
        validation_invocation_id,
        report,
        effective_status,
    )
