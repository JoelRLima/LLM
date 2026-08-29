"""Final bounded classification for H-series causal evidence."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent.evaluation.attribution_models import FailureAttribution
from agent.evaluation.scenario_contracts import CausalFailureClass

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


def resolve_failure_attribution(
    evidence: Mapping[str, Any],
    measurement: Mapping[str, Any],
    canonical: Mapping[str, Any],
) -> FailureAttribution:
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


def _explicit_defect_attribution(evidence: Mapping[str, Any]) -> FailureAttribution | None:
    for key, classification, default_reason, default_ref in (
        (
            "harness_defect",
            CausalFailureClass.HARNESS_DEFECT,
            "explicit_harness_evidence",
            "harness_defect",
        ),
        (
            "runtime_defect",
            CausalFailureClass.RUNTIME_DEFECT,
            "explicit_runtime_evidence",
            "runtime_defect",
        ),
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
    evidence_problem = _model_evidence_problem(category, refs)
    if evidence_problem is not None:
        return FailureAttribution(CausalFailureClass.UNKNOWN, (evidence_problem,), refs)
    if category == "capability" and not direct_violation:
        return FailureAttribution(
            CausalFailureClass.UNKNOWN,
            ("model_capability_contract_violation_unproven",),
            refs,
        )
    if category != "capability" and not bool(
        model.get("direct_trace") or model.get("variance_proven")
    ):
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


def _model_evidence_problem(category: str, refs: tuple[str, ...]) -> str | None:
    has_raw = any(ref.startswith("model_decision:") for ref in refs)
    has_contract = any(
        ref.startswith(("required_tool:", "binding_contract:", "structured_contract"))
        for ref in refs
    )
    has_runtime = any(
        ref.startswith(("canonical_plan:", "invocation:", "validation_event:"))
        for ref in refs
    )
    if not has_raw or not has_runtime or (category == "capability" and not has_contract):
        return "model_decision_evidence_unbounded"
    return None


def _direct_model_contract_violation(model: Mapping[str, Any]) -> bool:
    if any(
        model.get(key) is True
        for key in (
            "contract_violation",
            "direct_contract_violation",
            "decision_omitted_required_action",
        )
    ):
        return True
    return str(model.get("signature", "")).casefold() in _DIRECT_MODEL_VIOLATION_SIGNATURES


__all__ = ["resolve_failure_attribution"]
