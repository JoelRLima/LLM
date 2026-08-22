"""Evidence-based causal attribution for one Block 7 run."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from agent.evaluation.block7 import CausalFailureClass, EvidenceLevel, HSeriesArm, HSeriesScenario
from agent.llm.errors import ModelConnectionError, ModelTimeoutError


@dataclass(frozen=True)
class FailureAttribution:
    classification: CausalFailureClass
    reason_codes: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()


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
    evidence = dict(attribution_evidence or {})
    measurement = failure_mapping(report)
    canonical = evidence_mapping(report)
    environmental = evidence.get("environmental")
    if not isinstance(environmental, Mapping):
        environmental = measurement.get("environmental_evidence")
    if isinstance(environmental, Mapping) and environmental.get("concrete") is True and str(environmental.get("reason", "")):
        return FailureAttribution(
            CausalFailureClass.ENVIRONMENTAL,
            ("concrete_environment_failure",),
            tuple(str(item) for item in environmental.get("evidence_refs", ())) or ("measurement.environmental_evidence",),
        )
    for key, classification, default_reason, default_ref in (
        ("harness_defect", CausalFailureClass.HARNESS_DEFECT, "explicit_harness_evidence", "harness_defect"),
        ("runtime_defect", CausalFailureClass.RUNTIME_DEFECT, "explicit_runtime_evidence", "runtime_defect"),
    ):
        defect = evidence.get(key)
        if isinstance(defect, Mapping) and defect.get("proven") is True:
            return FailureAttribution(
                classification,
                tuple(str(item) for item in defect.get("reason_codes", ())) or (default_reason,),
                tuple(str(item) for item in defect.get("evidence_refs", ())) or (default_ref,),
            )
    model = evidence.get("model_behavior")
    if not isinstance(model, Mapping):
        model = canonical.get("model_behavior")
    if isinstance(model, Mapping) and model.get("decision_evidence") and model.get("canonical_runtime_evidence"):
        signature = str(model.get("signature", ""))
        if signature:
            category = str(model.get("category", "variance")).casefold()
            classification = CausalFailureClass.MODEL_CAPABILITY if category == "capability" else CausalFailureClass.MODEL_VARIANCE
            return FailureAttribution(
                classification,
                ("adequate_model_behavior_evidence", f"model_signature:{signature}"),
                tuple(str(item) for item in model.get("evidence_refs", ())) or ("model_decisions", "canonical_runtime"),
            )
    return FailureAttribution(
        CausalFailureClass.UNKNOWN,
        ("causal_evidence_unresolved",),
        ("failure_record",),
    )


def derive_attribution_evidence(
    report: Any, failures: tuple[str, ...], level: EvidenceLevel
) -> dict[str, Any]:
    measurement = failure_mapping(report)
    evidence = evidence_mapping(report)
    result: dict[str, Any] = {}
    for measurement_key, evidence_key in (
        ("environmental_evidence", "environmental"),
        ("harness_defect_evidence", "harness_defect"),
        ("runtime_defect_evidence", "runtime_defect"),
    ):
        if measurement.get(measurement_key):
            result[evidence_key] = measurement[measurement_key]
    decisions = evidence.get("model_decisions") or evidence.get("repair_decisions") or evidence.get("route_decisions")
    status = evidence.get("terminal_status") or measurement.get("status")
    plan = evidence.get("canonical_plan")
    invocations = evidence.get("invocation_evidence")
    if failures and decisions and status and (plan is not None or invocations is not None):
        model_failure_codes = tuple(
            code for code in failures
            if code.startswith(("evaluator:", "required_tool_missing", "canonical_binding", "required_", "grounding_", "forbidden_answer"))
        )
        result["model_behavior"] = {
            "signature": "known_contract_mismatch" if model_failure_codes else "observed_model_failure",
            "category": "capability" if any("required_tool_missing" in code or "canonical_binding" in code for code in model_failure_codes) else "variance",
            "decision_evidence": True,
            "canonical_runtime_evidence": True,
            "evidence_refs": ["model_decisions", "canonical_plan", "invocation_evidence", "terminal_status"],
        }
    return result


def is_environmental_exception(exc: BaseException) -> bool:
    return isinstance(exc, (ModelConnectionError, ModelTimeoutError, ConnectionError, TimeoutError))


def exception_report(scenario: HSeriesScenario, arm: HSeriesArm, exc: BaseException) -> dict[str, Any]:
    return {
        "scenario_id": f"{scenario.h_id.lower()}-{arm.arm_id}",
        "capability": f"block7/{scenario.h_id.lower()}",
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
    "FailureAttribution", "classify_failure", "derive_attribution_evidence",
    "evidence_mapping", "exception_report", "failure_mapping", "is_environmental_exception",
]
