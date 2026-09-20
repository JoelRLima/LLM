"""Projection helpers for building EvaluationReceiptV1 from scenario reports."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any

from agent.evaluation.contracts import ScenarioReport
from agent.evaluation.experiment import EvaluationExperimentContext
from agent.evaluation.receipt import (
    EVALUATION_RECEIPT_INVALID,
    EvaluationPrimitiveMeasurements,
    EvaluationReceiptError,
    EvaluationRunIdentity,
    EvaluationTechnicalOutcome,
    EvaluationVariantIdentity,
    _non_negative,
    _optional_non_negative,
)
from agent.evaluation.receipt_serialization import receipt_payload_without_id
from agent.evaluation.receipt_validators import _narrow_raw_receipt

if TYPE_CHECKING:
    from agent.evaluation.receipt import EvaluationReceiptV1


def _invalid(message: str) -> EvaluationReceiptError:
    return EvaluationReceiptError(EVALUATION_RECEIPT_INVALID, message)


def _measurement_int(measurement: Mapping[str, Any], key: str) -> int:
    return _optional_non_negative(measurement.get(key), key)


def _extract_run_identity(measurement: Mapping[str, Any]) -> tuple[str, str, str | None, str]:
    """Return (run_id, root_task_id, task_id, terminal_status) from measurement."""
    run_id = measurement.get("run_id")
    root_task_id = measurement.get("root_task_id")
    if not isinstance(run_id, str) or not isinstance(root_task_id, str):
        raise _invalid("canonical run identity is missing from evaluation measurement")
    task_id = measurement.get("runtime_task_id")
    if task_id is not None and not isinstance(task_id, str):
        raise _invalid("runtime task identity is invalid")
    terminal_status = measurement.get("terminal_outcome", measurement.get("status"))
    if not isinstance(terminal_status, str):
        raise _invalid("canonical terminal status is missing")
    return run_id, root_task_id, task_id, terminal_status


def _extract_variant_identity(
    measurement: Mapping[str, Any],
) -> tuple[str, Mapping[str, Any]]:
    """Return (fingerprint, composition) from measurement."""
    fingerprint = measurement.get("variant_fingerprint")
    composition = measurement.get("variant_composition")
    if not isinstance(fingerprint, str) or not isinstance(composition, Mapping):
        raise _invalid("canonical variant identity is missing from evaluation measurement")
    return fingerprint, composition


def _extract_validation_status(
    measurement: Mapping[str, Any], evidence: Mapping[str, Any]
) -> str | None:
    """Resolve validation_status from measurement or evidence detail."""
    status = measurement.get("validation_status")
    if status is None:
        detail = evidence.get("validation_detail")
        if isinstance(detail, Mapping):
            candidate = detail.get("status")
            if isinstance(candidate, str):
                status = candidate
    return status if isinstance(status, str) else None


def _extract_rollback(measurement: Mapping[str, Any], evidence: Mapping[str, Any]) -> bool:
    rollback = measurement.get("rollback_occurred", evidence.get("rollback_occurred", False))
    if not isinstance(rollback, bool):
        raise _invalid("rollback measurement is invalid")
    return rollback


def _extract_output_truncated(measurement: Mapping[str, Any]) -> bool:
    output_truncated = measurement.get("output_truncated", measurement.get("truncated", False))
    if not isinstance(output_truncated, bool):
        raise _invalid("output truncation measurement is invalid")
    return output_truncated


def build_technical_outcome(
    report: ScenarioReport,
    evaluator_failure_codes: Iterable[str],
) -> EvaluationTechnicalOutcome:
    """Assemble EvaluationTechnicalOutcome from a report and extra failure codes."""
    codes = {str(c) for c in evaluator_failure_codes if isinstance(c, str) and c.strip()}
    codes.update(f"evaluator:{failure.code}" for failure in report.failures)
    return EvaluationTechnicalOutcome(
        runtime_success=bool(report.observation.success),
        evaluator_passed=bool(report.passed),
        evaluator_failure_codes=tuple(sorted(codes)),
    )


def build_measurements(
    report: ScenarioReport,
    measurement: Mapping[str, Any],
    evidence: Mapping[str, Any],
) -> EvaluationPrimitiveMeasurements:
    """Build EvaluationPrimitiveMeasurements from a scenario report and raw dicts."""
    return EvaluationPrimitiveMeasurements(
        duration_ms=_measurement_int(measurement, "duration_ms"),
        model_calls=_measurement_int(measurement, "model_calls"),
        tool_calls=_measurement_int(measurement, "tool_calls"),
        tool_history_count=_measurement_int(measurement, "tool_history_count"),
        accounted_tokens=_measurement_int(measurement, "accounted_tokens"),
        reported_input_tokens=_measurement_int(measurement, "reported_input_tokens"),
        reported_output_tokens=_measurement_int(measurement, "reported_output_tokens"),
        reported_total_tokens=_measurement_int(measurement, "reported_total_tokens"),
        token_usage_complete=bool(measurement.get("token_usage_complete", False)),
        output_chars=_measurement_int(measurement, "output_chars"),
        output_truncated=_extract_output_truncated(measurement),
        changed_files=tuple(sorted(report.changed_files)),
        validation_status=_extract_validation_status(measurement, evidence),
        rollback_occurred=_extract_rollback(measurement, evidence),
        replan_count=_measurement_int(measurement, "replan_count"),
    )


def assemble_receipt_fields(
    report: ScenarioReport,
    *,
    experiment: EvaluationExperimentContext,
    scenario_arm_id: str,
    repetition: int,
    attempt: int,
    evidence_level: str,
    evaluator_failure_codes: Iterable[str] = (),
) -> dict[str, Any]:
    """Return the validated field dict needed to construct EvaluationReceiptV1."""
    if not isinstance(report, ScenarioReport):
        raise _invalid("report must be ScenarioReport")
    if not isinstance(experiment, EvaluationExperimentContext):
        raise _invalid("experiment must be EvaluationExperimentContext")
    measurement = report.observation.measurement
    evidence = report.observation.evidence
    fingerprint, composition = _extract_variant_identity(measurement)
    run_id, root_task_id, task_id, terminal_status = _extract_run_identity(measurement)
    _non_negative(repetition, "repetition")
    _non_negative(attempt, "attempt")
    return {
        "schema_version": 1,
        "experiment_id": experiment.experiment_id,
        "trial_id": experiment.trial_id,
        "scenario_id": report.scenario_id,
        "scenario_arm_id": scenario_arm_id,
        "repetition": repetition,
        "attempt": attempt,
        "evidence_level": evidence_level,
        "run": EvaluationRunIdentity(run_id, root_task_id, task_id, terminal_status),
        "variant": EvaluationVariantIdentity(
            profile_id=experiment.profile.profile_id,
            fingerprint=fingerprint,
            composition=composition,
        ),
        "measurements": build_measurements(report, measurement, evidence),
        "technical": build_technical_outcome(report, evaluator_failure_codes),
    }


def receipt_from_raw_dict(
    cls: "type[EvaluationReceiptV1]",
    value: Mapping[str, object],
) -> "EvaluationReceiptV1":
    """Deserialize an EvaluationReceiptV1 from a raw mapping."""
    if not isinstance(value, Mapping):
        raise _invalid("receipt must be an object")
    try:
        raw = _narrow_raw_receipt(value)
        return cls(
            schema_version=raw["schema_version"],
            receipt_id=raw["receipt_id"],
            experiment_id=raw["experiment_id"],
            trial_id=raw["trial_id"],
            scenario_id=raw["scenario_id"],
            scenario_arm_id=raw["scenario_arm_id"],
            repetition=raw["repetition"],
            attempt=raw["attempt"],
            evidence_level=raw["evidence_level"],
            run=EvaluationRunIdentity(
                run_id=raw["run"]["run_id"],
                root_task_id=raw["run"]["root_task_id"],
                task_id=raw["run"]["task_id"],
                terminal_status=raw["run"]["terminal_status"],
            ),
            variant=EvaluationVariantIdentity(
                profile_id=raw["variant"]["profile_id"],
                fingerprint=raw["variant"]["fingerprint"],
                composition=raw["variant"]["composition"],
            ),
            measurements=EvaluationPrimitiveMeasurements(
                duration_ms=raw["measurements"]["duration_ms"],
                model_calls=raw["measurements"]["model_calls"],
                tool_calls=raw["measurements"]["tool_calls"],
                tool_history_count=raw["measurements"]["tool_history_count"],
                accounted_tokens=raw["measurements"]["accounted_tokens"],
                reported_input_tokens=raw["measurements"]["reported_input_tokens"],
                reported_output_tokens=raw["measurements"]["reported_output_tokens"],
                reported_total_tokens=raw["measurements"]["reported_total_tokens"],
                token_usage_complete=raw["measurements"]["token_usage_complete"],
                output_chars=raw["measurements"]["output_chars"],
                output_truncated=raw["measurements"]["output_truncated"],
                changed_files=raw["measurements"]["changed_files"],
                validation_status=raw["measurements"]["validation_status"],
                rollback_occurred=raw["measurements"]["rollback_occurred"],
                replan_count=raw["measurements"]["replan_count"],
            ),
            technical=EvaluationTechnicalOutcome(
                runtime_success=raw["technical"]["runtime_success"],
                evaluator_passed=raw["technical"]["evaluator_passed"],
                evaluator_failure_codes=raw["technical"]["evaluator_failure_codes"],
            ),
        )
    except EvaluationReceiptError:
        raise
    except Exception as exc:
        raise _invalid("receipt fields are invalid") from exc


def build_evaluation_receipt_impl(
    report: ScenarioReport,
    *,
    experiment: EvaluationExperimentContext,
    scenario_arm_id: str,
    repetition: int,
    attempt: int,
    evidence_level: str,
    evaluator_failure_codes: Iterable[str] = (),
) -> "EvaluationReceiptV1":
    """Build an EvaluationReceiptV1 from a scenario report (implementation)."""
    from agent.evaluation.receipt import EvaluationReceiptV1, receipt_id_for_payload  # noqa: PLC0415

    fields = assemble_receipt_fields(
        report,
        experiment=experiment,
        scenario_arm_id=scenario_arm_id,
        repetition=repetition,
        attempt=attempt,
        evidence_level=evidence_level,
        evaluator_failure_codes=evaluator_failure_codes,
    )
    from typing import cast  # noqa: PLC0415

    provisional = cast(EvaluationReceiptV1, object.__new__(EvaluationReceiptV1))
    for key, val in fields.items():
        object.__setattr__(provisional, key, val)
    object.__setattr__(provisional, "receipt_id", receipt_id_for_payload(receipt_payload_without_id(provisional)))
    return EvaluationReceiptV1(
        schema_version=provisional.schema_version,
        receipt_id=provisional.receipt_id,
        experiment_id=provisional.experiment_id,
        trial_id=provisional.trial_id,
        scenario_id=provisional.scenario_id,
        scenario_arm_id=provisional.scenario_arm_id,
        repetition=provisional.repetition,
        attempt=provisional.attempt,
        evidence_level=provisional.evidence_level,
        run=provisional.run,
        variant=provisional.variant,
        measurements=provisional.measurements,
        technical=provisional.technical,
    )


__all__ = [
    "assemble_receipt_fields",
    "build_evaluation_receipt_impl",
    "build_measurements",
    "build_technical_outcome",
    "receipt_from_raw_dict",
    "receipt_payload_without_id",
]
