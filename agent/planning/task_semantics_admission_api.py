"""Public admission methods mixed into the canonical semantics owner."""

from __future__ import annotations

from typing import Any

from agent.planning.task_semantics_admission import validate_admission_provenance
from agent.planning.task_semantics_review import (
    ObligationReviewResult,
    review_and_add,
)
from agent.planning.task_semantics_types import (
    AdmissionSource,
    TaskObligation,
    TaskSemanticsError,
)


class TaskSemanticsAdmissionMixin:
    def review_and_add_obligations(
        self: Any,
        raw: Any,
        *,
        source: str,
        collect_rejections: bool = False,
    ) -> tuple[TaskObligation, ...] | ObligationReviewResult:
        return review_and_add(
            self,
            raw,
            source=source,
            collect_rejections=collect_rejections,
        )

    def review_obligations(
        self: Any,
        raw: Any,
        *,
        source: str,
    ) -> ObligationReviewResult:
        result = review_and_add(self, raw, source=source, collect_rejections=True)
        if not isinstance(result, ObligationReviewResult):
            raise TaskSemanticsError("resultado de revisao de obrigacoes invalido")
        return result

    def admit_safety_required(
        self: Any,
        raw: Any,
        *,
        reason: str,
    ) -> tuple[TaskObligation, ...]:
        if not isinstance(reason, str) or not reason.strip():
            raise TaskSemanticsError("requisito de seguranca requer justificativa")
        result = review_and_add(
            self,
            raw,
            source="trusted_runtime",
            trusted_admission=AdmissionSource.SAFETY_REQUIRED,
            trusted_authorization=reason,
        )
        return tuple(result)

    def admit_externally_authorized(
        self: Any,
        raw: Any,
        *,
        authorization: str,
    ) -> tuple[TaskObligation, ...]:
        if not isinstance(authorization, str) or not authorization.strip():
            raise TaskSemanticsError("admissao externa requer autorizacao")
        result = review_and_add(
            self,
            raw,
            source="trusted_external",
            trusted_admission=AdmissionSource.EXTERNALLY_AUTHORIZED,
            trusted_authorization=authorization,
        )
        return tuple(result)

    def validate_admission_provenance(self: Any) -> None:
        validate_admission_provenance(self)


__all__ = ["TaskSemanticsAdmissionMixin"]
