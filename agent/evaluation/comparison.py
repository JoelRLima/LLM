"""Deterministic aggregate and comparison projections for W19 receipts."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from statistics import median

from agent.evaluation.receipt import (
    EVALUATION_RECEIPT_INVALID,
    EvaluationReceiptError,
    EvaluationReceiptV1,
    validate_evaluation_receipt,
)

EVALUATION_COMPARISON_INCOMPATIBLE = "EVALUATION_COMPARISON_INCOMPATIBLE"


class EvaluationComparisonError(ValueError):
    """Raised when receipt groups are not semantically comparable."""

    def __init__(self, reason_code: str, message: str) -> None:
        self.reason_code = reason_code
        super().__init__(message)


def _receipts(values: Iterable[EvaluationReceiptV1 | Mapping[str, object]]) -> tuple[EvaluationReceiptV1, ...]:
    result: list[EvaluationReceiptV1] = []
    for value in values:
        try:
            result.append(validate_evaluation_receipt(value))
        except (ValueError, TypeError) as exc:
            if isinstance(exc, EvaluationReceiptError):
                raise
            raise EvaluationComparisonError(EVALUATION_RECEIPT_INVALID, "invalid evaluation receipt") from exc
    return tuple(result)


def _verdict_value(value: object) -> str:
    raw = getattr(value, "value", value)
    if raw not in {"correct", "partial", "incorrect", "not_rated"}:
        raise EvaluationComparisonError(EVALUATION_COMPARISON_INCOMPATIBLE, "invalid human verdict")
    return str(raw)


@dataclass(frozen=True, slots=True)
class EvaluationAggregate:
    profile_id: str
    variant_fingerprint: str
    run_count: int
    passed_count: int
    pass_rate: float
    median_duration_ms: float | None
    median_model_calls: float | None
    median_tool_calls: float | None
    median_accounted_tokens: float | None
    failure_code_counts: Mapping[str, int]
    human_verdict_counts: Mapping[str, int]

    def to_dict(self) -> dict[str, object]:
        return {
            "profile_id": self.profile_id,
            "variant_fingerprint": self.variant_fingerprint,
            "run_count": self.run_count,
            "passed_count": self.passed_count,
            "pass_rate": self.pass_rate,
            "median_duration_ms": self.median_duration_ms,
            "median_model_calls": self.median_model_calls,
            "median_tool_calls": self.median_tool_calls,
            "median_accounted_tokens": self.median_accounted_tokens,
            "failure_code_counts": dict(self.failure_code_counts),
            "human_verdict_counts": dict(self.human_verdict_counts),
        }


@dataclass(frozen=True, slots=True)
class EvaluationComparison:
    experiment_id: str
    scenario_set_identity: str
    aggregates: tuple[EvaluationAggregate, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "experiment_id": self.experiment_id,
            "scenario_set_identity": self.scenario_set_identity,
            "aggregates": [aggregate.to_dict() for aggregate in self.aggregates],
        }


def aggregate_receipts(
    receipts: Iterable[EvaluationReceiptV1 | Mapping[str, object]],
    *,
    human_verdicts: Mapping[str, object] | None = None,
) -> EvaluationAggregate:
    values = _receipts(receipts)
    if not values:
        raise EvaluationComparisonError(EVALUATION_COMPARISON_INCOMPATIBLE, "cannot aggregate an empty receipt group")
    identity = {(item.variant.profile_id, item.variant.fingerprint) for item in values}
    if len(identity) != 1:
        raise EvaluationComparisonError(
            EVALUATION_COMPARISON_INCOMPATIBLE,
            "one aggregate cannot combine profile or variant identities",
        )
    experiment_ids = {item.experiment_id for item in values}
    trial_ids = {item.trial_id for item in values}
    if len(experiment_ids) != 1 or len(trial_ids) != 1:
        raise EvaluationComparisonError(
            EVALUATION_COMPARISON_INCOMPATIBLE,
            "one aggregate cannot combine experiment or trial identities",
        )
    profile_id, fingerprint = next(iter(identity))
    human_counts: Counter[str] = Counter()
    if human_verdicts is not None:
        for item in values:
            if item.run.run_id in human_verdicts:
                human_counts[_verdict_value(human_verdicts[item.run.run_id])] += 1
    failures: Counter[str] = Counter(
        code
        for item in values
        for code in item.technical.evaluator_failure_codes
    )

    def _median(name: str) -> float | None:
        data = [getattr(item.measurements, name) for item in values]
        return float(median(data)) if data else None

    return EvaluationAggregate(
        profile_id=profile_id,
        variant_fingerprint=fingerprint,
        run_count=len(values),
        passed_count=sum(item.technical.evaluator_passed for item in values),
        pass_rate=sum(item.technical.evaluator_passed for item in values) / len(values),
        median_duration_ms=_median("duration_ms"),
        median_model_calls=_median("model_calls"),
        median_tool_calls=_median("tool_calls"),
        median_accounted_tokens=_median("accounted_tokens"),
        failure_code_counts=dict(sorted(failures.items())),
        human_verdict_counts=dict(sorted(human_counts.items())),
    )


def _scenario_set_identity(values: Sequence[EvaluationReceiptV1]) -> str:
    return "|".join(sorted({f"{item.scenario_id}:{item.scenario_arm_id}" for item in values}))


def _collect_groups(
    receipts: "Iterable[EvaluationReceiptV1 | Mapping[str, object]] | Mapping[str, Iterable[EvaluationReceiptV1 | Mapping[str, object]]]",
    identity_values: list[tuple[object | None, object | None]],
) -> list[tuple[str | None, tuple["EvaluationReceiptV1", ...]]]:
    """Convert receipts (mapping or iterable) to validated groups with collected identity values."""

    def _validated(value: "EvaluationReceiptV1 | Mapping[str, object]") -> "EvaluationReceiptV1":
        identity_values.append(_identity_fields(value))
        return validate_evaluation_receipt(value)

    def _identity_fields(value: object) -> tuple[object | None, object | None]:
        if not isinstance(value, Mapping):
            return None, None
        return value.get("candidate_identity"), value.get("model_identity", value.get("model_config_identity"))

    groups: list[tuple[str | None, tuple["EvaluationReceiptV1", ...]]] = []
    if isinstance(receipts, Mapping):
        for profile_id, group in receipts.items():
            values = tuple(_validated(value) for value in group)
            groups.append((str(profile_id), values))
    else:
        values = tuple(_validated(value) for value in receipts)
        by_identity: dict[tuple[str, str], list["EvaluationReceiptV1"]] = {}
        for item in values:
            by_identity.setdefault((item.variant.profile_id, item.variant.fingerprint), []).append(item)
        groups = [(key[0], tuple(value)) for key, value in sorted(by_identity.items())]
    return groups


def _validate_groups_compatibility(
    groups: list[tuple[str | None, tuple["EvaluationReceiptV1", ...]]],
    identity_values: list[tuple[object | None, object | None]],
    *,
    scenario_set_identity: str | None,
    experiment_id: str | None,
    candidate_identity: str | None,
    model_identity: object | None,
) -> tuple[str, str]:
    """Validate group identity compatibility and return (selected_experiment, selected_scenario)."""
    all_values = tuple(item for _, group in groups for item in group)
    selected_scenario_identity = scenario_set_identity or _scenario_set_identity(all_values)
    if not isinstance(selected_scenario_identity, str) or not selected_scenario_identity:
        raise EvaluationComparisonError(EVALUATION_COMPARISON_INCOMPATIBLE, "scenario set identity is required")
    candidate_values = {str(value) for value, _ in identity_values if value is not None}
    model_values = {_canonical_identity(value) for _, value in identity_values if value is not None}
    if len(candidate_values) > 1 or len(model_values) > 1:
        raise EvaluationComparisonError(EVALUATION_COMPARISON_INCOMPATIBLE, "receipt groups have incompatible candidate or model identities")
    if candidate_identity is not None and candidate_values != {candidate_identity}:
        raise EvaluationComparisonError(EVALUATION_COMPARISON_INCOMPATIBLE, "receipt candidate identity does not match the comparison")
    if model_identity is not None and model_values != {_canonical_identity(model_identity)}:
        raise EvaluationComparisonError(EVALUATION_COMPARISON_INCOMPATIBLE, "receipt model identity does not match the comparison")
    observed_scenario_sets = {_scenario_set_identity(group) for _, group in groups}
    if observed_scenario_sets != {selected_scenario_identity}:
        raise EvaluationComparisonError(EVALUATION_COMPARISON_INCOMPATIBLE, "receipt groups have incompatible scenario sets")
    observed_experiments = {item.experiment_id for item in all_values}
    selected_experiment = experiment_id or (next(iter(observed_experiments)) if len(observed_experiments) == 1 else None)
    if selected_experiment is None or observed_experiments != {selected_experiment}:
        raise EvaluationComparisonError(EVALUATION_COMPARISON_INCOMPATIBLE, "receipt groups have incompatible experiments")
    return selected_experiment, selected_scenario_identity


def compare_receipt_groups(
    receipts: Iterable[EvaluationReceiptV1 | Mapping[str, object]] | Mapping[str, Iterable[EvaluationReceiptV1 | Mapping[str, object]]],
    *,
    scenario_set_identity: str | None = None,
    experiment_id: str | None = None,
    candidate_identity: str | None = None,
    model_identity: object | None = None,
    human_verdicts: Mapping[str, object] | None = None,
) -> EvaluationComparison:
    """Aggregate receipts and reject incompatible canonical identity joins."""
    identity_values: list[tuple[object | None, object | None]] = []
    groups = _collect_groups(receipts, identity_values)
    if not groups or any(not value for _, value in groups):
        raise EvaluationComparisonError(EVALUATION_COMPARISON_INCOMPATIBLE, "comparison requires non-empty groups")
    selected_experiment, selected_scenario_identity = _validate_groups_compatibility(
        groups,
        identity_values,
        scenario_set_identity=scenario_set_identity,
        experiment_id=experiment_id,
        candidate_identity=candidate_identity,
        model_identity=model_identity,
    )
    aggregates = tuple(
        aggregate_receipts(group, human_verdicts=human_verdicts)
        for _, group in groups
    )
    return EvaluationComparison(
        experiment_id=selected_experiment,
        scenario_set_identity=selected_scenario_identity,
        aggregates=aggregates,
    )


def _canonical_identity(value: object) -> str:
    if isinstance(value, Mapping):
        import json

        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return str(value)


__all__ = [
    "EVALUATION_COMPARISON_INCOMPATIBLE",
    "EvaluationAggregate",
    "EvaluationComparison",
    "EvaluationComparisonError",
    "aggregate_receipts",
    "compare_receipt_groups",
]
