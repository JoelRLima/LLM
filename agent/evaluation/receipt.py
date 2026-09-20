"""Compact, content-addressed W19 evaluation receipts."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from agent.evaluation.receipt_validators import (
    composition_from_dict as _composition_from_dict_impl,
)
from agent.evaluation.receipt_validators import (
    validate_changed_files as _validate_changed_files_impl,
)
from agent.variants.models import VariantComposition

EVALUATION_RECEIPT_SCHEMA_VERSION = 1
EVALUATION_RECEIPT_INVALID = "EVALUATION_RECEIPT_INVALID"
EVALUATION_RECEIPT_ID_MISMATCH = "EVALUATION_RECEIPT_ID_MISMATCH"
EVALUATION_RECEIPT_VARIANT_MISMATCH = "EVALUATION_RECEIPT_VARIANT_MISMATCH"


_RECEIPT_ID = re.compile(r"^evalr-[0-9a-f]{64}$")
_MAX_ID_CHARS = 128
_NON_NEGATIVE_FIELDS = (
    "duration_ms",
    "model_calls",
    "tool_calls",
    "tool_history_count",
    "accounted_tokens",
    "reported_input_tokens",
    "reported_output_tokens",
    "reported_total_tokens",
    "output_chars",
    "replan_count",
)



class EvaluationReceiptError(ValueError):
    """Fail-closed receipt validation error with a stable reason code."""

    def __init__(self, reason_code: str, message: str) -> None:
        self.reason_code = reason_code
        super().__init__(message)


def _invalid(message: str) -> EvaluationReceiptError:
    return EvaluationReceiptError(EVALUATION_RECEIPT_INVALID, message)


def _required_text(value: object, label: str, *, maximum: int = _MAX_ID_CHARS) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise _invalid(f"{label} must be a bounded non-empty string")
    return value


def _non_negative(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _invalid(f"{label} must be a non-negative integer")
    return value


def _optional_non_negative(value: object, label: str) -> int:
    if value is None:
        return 0
    return _non_negative(value, label)


def _canonical_json(value: Mapping[str, object]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")



def _composition_from_dict(value: object) -> VariantComposition:
    return _composition_from_dict_impl(value, _invalid)


def _validate_changed_files(value: object) -> tuple[str, ...]:
    return _validate_changed_files_impl(value, _invalid)



@dataclass(frozen=True, slots=True)
class EvaluationRunIdentity:
    run_id: str
    root_task_id: str
    task_id: str | None
    terminal_status: str

    def __post_init__(self) -> None:
        _required_text(self.run_id, "run_id")
        _required_text(self.root_task_id, "root_task_id")
        if self.task_id is not None:
            _required_text(self.task_id, "task_id")
        _required_text(self.terminal_status, "terminal_status")


@dataclass(frozen=True, slots=True)
class EvaluationVariantIdentity:
    profile_id: str
    fingerprint: str
    composition: Mapping[str, object]

    def __post_init__(self) -> None:
        _required_text(self.profile_id, "profile_id", maximum=64)
        if not isinstance(self.fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", self.fingerprint):
            raise _invalid("variant fingerprint is invalid")
        composition = _composition_from_dict(self.composition)
        if composition.fingerprint != self.fingerprint:
            raise EvaluationReceiptError(
                EVALUATION_RECEIPT_VARIANT_MISMATCH,
                "variant fingerprint does not match composition",
            )
        object.__setattr__(self, "composition", composition.normalized_dict())


@dataclass(frozen=True, slots=True)
class EvaluationPrimitiveMeasurements:
    duration_ms: int
    model_calls: int
    tool_calls: int
    tool_history_count: int
    accounted_tokens: int
    reported_input_tokens: int
    reported_output_tokens: int
    reported_total_tokens: int
    token_usage_complete: bool
    output_chars: int
    output_truncated: bool
    changed_files: tuple[str, ...]
    validation_status: str | None
    rollback_occurred: bool
    replan_count: int

    def __post_init__(self) -> None:
        for name in _NON_NEGATIVE_FIELDS:
            _non_negative(getattr(self, name), name)
        if not isinstance(self.token_usage_complete, bool):
            raise _invalid("token_usage_complete must be boolean")
        if not isinstance(self.output_truncated, bool):
            raise _invalid("output_truncated must be boolean")
        if not isinstance(self.rollback_occurred, bool):
            raise _invalid("rollback_occurred must be boolean")
        if self.validation_status is not None:
            _required_text(self.validation_status, "validation_status")
        object.__setattr__(self, "changed_files", _validate_changed_files(self.changed_files))


@dataclass(frozen=True, slots=True)
class EvaluationTechnicalOutcome:
    runtime_success: bool
    evaluator_passed: bool
    evaluator_failure_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.runtime_success, bool) or not isinstance(self.evaluator_passed, bool):
            raise _invalid("technical outcome booleans are invalid")
        if not isinstance(self.evaluator_failure_codes, tuple):
            object.__setattr__(self, "evaluator_failure_codes", tuple(self.evaluator_failure_codes))
        if any(not isinstance(code, str) or not code.strip() for code in self.evaluator_failure_codes):
            raise _invalid("evaluator failure codes are invalid")
        normalized = tuple(sorted(set(self.evaluator_failure_codes)))
        if normalized != self.evaluator_failure_codes:
            raise _invalid("evaluator failure codes must be sorted and unique")


def _receipt_payload_without_id(receipt: "EvaluationReceiptV1") -> dict[str, object]:
    from agent.evaluation.receipt_projection import receipt_payload_without_id as _impl  # noqa: PLC0415

    return _impl(receipt)




def receipt_id_for_payload(payload: Mapping[str, object]) -> str:
    return "evalr-" + hashlib.sha256(_canonical_json(payload)).hexdigest()


@dataclass(frozen=True, slots=True)
class EvaluationReceiptV1:
    schema_version: int
    receipt_id: str
    experiment_id: str
    trial_id: str
    scenario_id: str
    scenario_arm_id: str
    repetition: int
    attempt: int
    evidence_level: str
    run: EvaluationRunIdentity
    variant: EvaluationVariantIdentity
    measurements: EvaluationPrimitiveMeasurements
    technical: EvaluationTechnicalOutcome

    def __post_init__(self) -> None:
        if isinstance(self.schema_version, bool) or self.schema_version != EVALUATION_RECEIPT_SCHEMA_VERSION:
            raise _invalid("unsupported receipt schema")
        _required_text(self.experiment_id, "experiment_id")
        _required_text(self.trial_id, "trial_id")
        _required_text(self.scenario_id, "scenario_id")
        _required_text(self.scenario_arm_id, "scenario_arm_id")
        _non_negative(self.repetition, "repetition")
        _non_negative(self.attempt, "attempt")
        if self.repetition < 1 or self.attempt < 1:
            raise _invalid("repetition and attempt must be positive")
        _required_text(self.evidence_level, "evidence_level")
        if not isinstance(self.run, EvaluationRunIdentity):
            raise _invalid("run identity is invalid")
        if not isinstance(self.variant, EvaluationVariantIdentity):
            raise _invalid("variant identity is invalid")
        if not isinstance(self.measurements, EvaluationPrimitiveMeasurements):
            raise _invalid("measurements are invalid")
        if not isinstance(self.technical, EvaluationTechnicalOutcome):
            raise _invalid("technical outcome is invalid")
        if not isinstance(self.receipt_id, str) or _RECEIPT_ID.fullmatch(self.receipt_id) is None:
            raise _invalid("receipt_id is invalid")
        expected = receipt_id_for_payload(_receipt_payload_without_id(self))
        if self.receipt_id != expected:
            raise EvaluationReceiptError(
                EVALUATION_RECEIPT_ID_MISMATCH,
                "receipt_id does not match canonical payload",
            )

    def to_dict(self) -> dict[str, object]:
        payload = _receipt_payload_without_id(self)
        payload["receipt_id"] = self.receipt_id
        return payload

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "EvaluationReceiptV1":
        from agent.evaluation.receipt_projection import receipt_from_raw_dict  # noqa: PLC0415

        return receipt_from_raw_dict(cls, value)



def build_evaluation_receipt(
    report: "Any",
    *,
    experiment: "Any",
    scenario_arm_id: str,
    repetition: int,
    attempt: int,
    evidence_level: str,
    evaluator_failure_codes: "Any" = (),
) -> "EvaluationReceiptV1":
    """Project only canonical bounded evaluation facts into a receipt."""
    from agent.evaluation.receipt_projection import build_evaluation_receipt_impl  # noqa: PLC0415

    return build_evaluation_receipt_impl(
        report,
        experiment=experiment,
        scenario_arm_id=scenario_arm_id,
        repetition=repetition,
        attempt=attempt,
        evidence_level=evidence_level,
        evaluator_failure_codes=evaluator_failure_codes,
    )


def validate_evaluation_receipt(value: EvaluationReceiptV1 | Mapping[str, object]) -> EvaluationReceiptV1:
    return value if isinstance(value, EvaluationReceiptV1) else EvaluationReceiptV1.from_dict(value)


__all__ = [
    "EVALUATION_RECEIPT_ID_MISMATCH",
    "EVALUATION_RECEIPT_INVALID",
    "EVALUATION_RECEIPT_SCHEMA_VERSION",
    "EVALUATION_RECEIPT_VARIANT_MISMATCH",
    "EvaluationPrimitiveMeasurements",
    "EvaluationReceiptError",
    "EvaluationReceiptV1",
    "EvaluationRunIdentity",
    "EvaluationTechnicalOutcome",
    "EvaluationVariantIdentity",
    "build_evaluation_receipt",
    "receipt_id_for_payload",
    "validate_evaluation_receipt",
]
