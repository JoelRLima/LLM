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


def _explicit_defect_attribution(evidence: Mapping[str, Any]) -> FailureAttribution | None:
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
    return None


def _environmental_attribution(
    evidence: Mapping[str, Any], measurement: Mapping[str, Any]
) -> FailureAttribution | None:
    environmental = evidence.get("environmental")
    if not isinstance(environmental, Mapping):
        environmental = measurement.get("environmental_evidence")
    if not isinstance(environmental, Mapping):
        return None
    if environmental.get("concrete") is not True or not str(environmental.get("reason", "")):
        return None
    return FailureAttribution(
        CausalFailureClass.ENVIRONMENTAL,
        ("concrete_environment_failure",),
        tuple(str(item) for item in environmental.get("evidence_refs", ()))
        or ("measurement.environmental_evidence",),
    )


def _model_attribution(
    evidence: Mapping[str, Any], canonical: Mapping[str, Any]
) -> FailureAttribution | None:
    model = evidence.get("model_behavior")
    if not isinstance(model, Mapping):
        model = canonical.get("model_behavior")
    if not isinstance(model, Mapping):
        return None
    if not model.get("decision_evidence") or not model.get("canonical_runtime_evidence"):
        return None
    signature = str(model.get("signature", ""))
    if not signature:
        return None
    category = str(model.get("category", "variance")).casefold()
    direct_violation = _direct_model_contract_violation(model)
    refs = tuple(str(item) for item in model.get("evidence_refs", ())) or ("model_decisions",)
    if category == "capability" and not direct_violation:
        return FailureAttribution(
            CausalFailureClass.UNKNOWN,
            ("model_capability_contract_violation_unproven",),
            refs,
        )
    if category != "capability" and not bool(model.get("direct_trace") or model.get("variance_proven")):
        return FailureAttribution(
            CausalFailureClass.UNKNOWN,
            ("model_variance_evidence_unresolved",),
            refs,
        )
    classification = (
        CausalFailureClass.MODEL_CAPABILITY
        if category == "capability"
        else CausalFailureClass.MODEL_VARIANCE
    )
    reason = "direct_model_contract_evidence" if direct_violation else "bounded_model_trace"
    return FailureAttribution(
        classification,
        (reason, f"model_signature:{signature}"),
        tuple(str(item) for item in model.get("evidence_refs", ()))
        or ("model_decisions", "canonical_runtime"),
    )


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
    for candidate in (
        _explicit_defect_attribution(evidence),
        _environmental_attribution(evidence, measurement),
        _model_attribution(evidence, canonical),
    ):
        if candidate is not None:
            return candidate
    return FailureAttribution(
        CausalFailureClass.UNKNOWN,
        ("causal_evidence_unresolved",),
        ("failure_record",),
    )


_DIRECT_MODEL_VIOLATION_SIGNATURES = frozenset(
    {
        "missing_required_tool",
        "required_action_omitted",
        "decision_omitted_required_action",
        "invalid_structured_decision",
        "prohibited_operation_selected",
        "known_contract_mismatch",
    }
)


def _direct_model_contract_violation(model: Mapping[str, Any]) -> bool:
    """Require a model-local contract breach, not merely a failed task."""

    if any(
        model.get(key) is True
        for key in ("contract_violation", "direct_contract_violation", "decision_omitted_required_action")
    ):
        return True
    return str(model.get("signature", "")).casefold() in _DIRECT_MODEL_VIOLATION_SIGNATURES


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
        plan_tools = {
            str(step.get("tool"))
            for step in plan or ()
            if isinstance(step, Mapping) and step.get("tool")
        }
        invocation_tools = {
            str(item.get("tool"))
            for item in invocations or ()
            if isinstance(item, Mapping) and item.get("tool")
        }
        missing_required = next(
            (code.split(":", 1)[1] for code in model_failure_codes if code.startswith("required_tool_missing:")),
            None,
        )
        if missing_required and missing_required in plan_tools and missing_required not in invocation_tools:
            result["runtime_defect"] = {
                "proven": True,
                "reason_codes": ["planned_tool_not_executed"],
                "evidence_refs": ["canonical_plan", "invocation_evidence"],
            }
        direct_capability = bool(
            missing_required
            and missing_required not in plan_tools
            and missing_required not in invocation_tools
        ) or any(
            code.startswith(("canonical_binding", "evaluator:invalid_structured_decision", "required_action_omitted"))
            for code in model_failure_codes
        )
        if direct_capability:
            result["model_behavior"] = {
                "signature": "missing_required_tool" if missing_required else "known_contract_mismatch",
                "category": "capability",
                "contract_violation": True,
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
