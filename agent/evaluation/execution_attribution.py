"""Evidence-based causal attribution for one H-series run."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent.evaluation.attribution_binding import binding_attribution
from agent.evaluation.attribution_classification import resolve_failure_attribution
from agent.evaluation.attribution_decisions import (
    canonical_tool_entries,
    decision_records,
    invocation_entries,
)
from agent.evaluation.attribution_models import FailureAttribution
from agent.evaluation.attribution_required import required_tool_attribution
from agent.evaluation.attribution_structured import structured_attribution
from agent.evaluation.scenario_contracts import CausalFailureClass, EvidenceLevel, HSeriesArm, HSeriesScenario
from agent.llm.errors import ModelConnectionError, ModelTimeoutError


def _observation(report: Any) -> Any:
    return getattr(report, "observation", None)


def failure_mapping(report: Any) -> Mapping[str, Any]:
    observation = _observation(report)
    measurement = getattr(observation, "measurement", {}) if observation is not None else {}
    return measurement if isinstance(measurement, Mapping) else {}


def evidence_mapping(report: Any) -> Mapping[str, Any]:
    observation = _observation(report)
    evidence = getattr(observation, "evidence", {}) if observation is not None else {}
    return evidence if isinstance(evidence, Mapping) else {}


def classify_failure(
    report: Any,
    failures: tuple[str, ...],
    level: EvidenceLevel,
    *,
    attribution_evidence: Mapping[str, Any] | None = None,
) -> FailureAttribution:
    """Attribute a failed run only when the supplied evidence supports it."""

    if not failures and bool(getattr(report, "passed", False)):
        return FailureAttribution(CausalFailureClass.UNKNOWN, ("no_failure",), ())
    evidence = derive_attribution_evidence(report, failures, level)
    supplied = attribution_evidence if isinstance(attribution_evidence, Mapping) else {}
    # Runtime, harness, and environmental facts may be added by the trusted
    # campaign boundary after report construction (for example identity
    # drift).  Model causation is never accepted from a caller-authored
    # summary: it is always re-derived from the raw decision in ``report``.
    for key in ("environmental", "harness_defect", "runtime_defect"):
        if key in supplied:
            evidence[key] = supplied[key]
    return resolve_failure_attribution(
        evidence,
        failure_mapping(report),
        evidence_mapping(report),
    )


def derive_attribution_evidence(
    report: Any, failures: tuple[str, ...], level: EvidenceLevel
) -> dict[str, Any]:
    del level
    measurement = failure_mapping(report)
    evidence = evidence_mapping(report)
    result = _base_attribution_evidence(measurement, evidence)
    if not failures:
        return result
    records = decision_records(evidence)
    canonical_entries = canonical_tool_entries(evidence.get("canonical_plan"))
    invocation_history = invocation_entries(evidence)
    derived = (
        required_tool_attribution(
            failures,
            records,
            canonical_entries,
            evidence.get("canonical_plan"),
            invocation_history,
        ),
        binding_attribution(evidence, failures, records, canonical_entries),
        structured_attribution(evidence, failures, records),
    )
    for source in derived:
        _merge_missing(result, source)
    return result


def _base_attribution_evidence(
    measurement: Mapping[str, Any], evidence: Mapping[str, Any]
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for measurement_key, evidence_key in (
        ("environmental_evidence", "environmental"),
        ("harness_defect_evidence", "harness_defect"),
        ("runtime_defect_evidence", "runtime_defect"),
    ):
        if measurement.get(measurement_key):
            result[evidence_key] = measurement[measurement_key]
        if evidence.get(evidence_key) and evidence_key not in result:
            result[evidence_key] = evidence[evidence_key]
    return result


def _merge_missing(target: dict[str, Any], source: Mapping[str, Any]) -> None:
    for key, value in source.items():
        if key not in target:
            target[key] = value


def is_environmental_exception(exc: BaseException) -> bool:
    return isinstance(exc, (ModelConnectionError, ModelTimeoutError, ConnectionError, TimeoutError))


def exception_report(
    scenario: HSeriesScenario, arm: HSeriesArm, exc: BaseException
) -> dict[str, Any]:
    return {
        "scenario_id": f"{scenario.h_id.lower()}-{arm.arm_id}",
        "capability": f"h-series/{scenario.h_id.lower()}",
        "passed_by_existing_evaluator": False,
        "changed_files": [],
        "failures": [{"code": "execution_exception", "message": type(exc).__name__}],
        "observation": {
            "success": False,
            "steps": 0,
            "status": "unavailable" if is_environmental_exception(exc) else "failed",
            "answer": "",
            "error": type(exc).__name__,
        },
    }


__all__ = [
    "FailureAttribution",
    "classify_failure",
    "derive_attribution_evidence",
    "evidence_mapping",
    "exception_report",
    "failure_mapping",
    "is_environmental_exception",
]
