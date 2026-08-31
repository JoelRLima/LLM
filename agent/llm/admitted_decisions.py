"""Typed projections after exact request-contract admission."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from agent.llm.admitted_decision_core import (
    AdmittedModelDecision,
    DirectResponseDecision,
    EffectObservationBlockedDecision,
    EffectObservationCompleteWithoutEffectDecision,
    EffectObservationContinuationBlockedDecision,
    EffectObservationContinuationCompleteWithoutEffectDecision,
    EffectObservationContinuationDecision,
    EffectObservationContinuationExecuteDecision,
    EffectObservationExecuteDecision,
    InitialPlanDecision,
    ReasoningBoundaryBlockedDecision,
    ReasoningBoundaryCompleteDecision,
    ReasoningBoundaryContinuationBlockedDecision,
    ReasoningBoundaryContinuationCompleteDecision,
    ReasoningBoundaryContinuationDecision,
    ReasoningBoundaryContinuationExecuteDecision,
    ReasoningBoundaryExecuteDecision,
)
from agent.llm.admitted_decision_projection import project_admitted
from agent.llm.admitted_decision_variants import (
    DecisionBinding,
    FinalGenerationDecision,
    LegacyModelDecision,
    MacroPlanDecision,
    MacroPlanStep,
    ModelDecisionValue,
    ModelDecisionWithCompatibility,
    ReactiveFinalDecision,
    ReactiveToolDecision,
    ReplanDecision,
    SummarizationDecision,
    TaskContractBlockedDecision,
    TaskContractDecision,
    TaskContractNeedsInputDecision,
    TaskSpecBlockedDecision,
    TaskSpecDecision,
    ToolDiscoveryDecision,
)
from agent.llm.decision_contract import (
    ModelRequestContract,
    admit_model_decision_value,
    legacy_model_decision_compatibility,
    resolve_request_contract,
)

_project_admitted = project_admitted


def admit_typed_model_decision(
    value: Any,
    *,
    request_contract: ModelRequestContract | str | None = None,
    step_type: str | None = None,
) -> ModelDecisionValue | None:
    """Admit raw parsed data once, then return its immutable typed variant."""

    if isinstance(value, AdmittedModelDecision):
        expected = resolve_request_contract(
            request_contract=request_contract, step_type=step_type
        )
        return cast(ModelDecisionValue, value) if expected is value.request_contract else None
    admitted = admit_model_decision_value(
        value, step_type=step_type, request_contract=request_contract
    )
    if admitted is None:
        return None
    contract = resolve_request_contract(
        request_contract=request_contract, step_type=step_type
    )
    if contract is None:
        return None
    return _project_admitted(admitted, contract)


def _project_exactly_admitted_model_decision(
    value: Mapping[str, Any], contract: ModelRequestContract
) -> ModelDecisionValue:
    """Trusted boundary seam for a value returned by exact admission."""

    return _project_admitted(value, contract)


def ask_typed_model_decision(
    context_manager: Any,
    prompt: str,
    *,
    request_contract: ModelRequestContract,
    step_type: str | None = None,
    **kwargs: Any,
) -> ModelDecisionValue | None:
    """Request a typed decision through the exact contract seam."""

    typed_request = getattr(context_manager, "ask_model_typed", None)
    if callable(typed_request):
        result = typed_request(
            prompt,
            request_contract=request_contract,
            step_type=step_type,
            **kwargs,
        )
        if not isinstance(result, AdmittedModelDecision):
            return None
        return cast(ModelDecisionValue, result) if result.request_contract is request_contract else None
    result = context_manager.ask_model(
        prompt,
        request_contract=request_contract,
        step_type=step_type,
        **kwargs,
    )
    return admit_typed_model_decision(
        result, request_contract=request_contract, step_type=step_type
    )


def ask_model_decision_with_compatibility(
    context_manager: Any,
    prompt: str,
    *,
    request_contract: ModelRequestContract,
    step_type: str | None = None,
    **kwargs: Any,
) -> ModelDecisionWithCompatibility | None:
    """Request one decision while keeping legacy responses noncanonical."""

    effective_step_type = step_type or request_contract.value
    result = context_manager.ask_model(
        prompt,
        step_type=effective_step_type,
        request_contract=request_contract,
        **kwargs,
    )
    typed = admit_typed_model_decision(
        result, request_contract=request_contract, step_type=step_type
    )
    if typed is not None:
        return typed
    compatible = legacy_model_decision_compatibility(
        result,
        step_type=step_type,
        request_contract=request_contract,
    )
    if compatible is None:
        return None
    return LegacyModelDecision(
        contract=request_contract,
        payload=_freeze_compatibility_payload(compatible),
    )


def _freeze_compatibility_payload(value: Mapping[str, Any]) -> Mapping[str, Any]:
    from agent.llm.admitted_decision_core import _freeze_mapping

    return _freeze_mapping(value)


__all__ = [
    "AdmittedModelDecision",
    "DecisionBinding",
    "DirectResponseDecision",
    "EffectObservationBlockedDecision",
    "EffectObservationCompleteWithoutEffectDecision",
    "EffectObservationContinuationBlockedDecision",
    "EffectObservationContinuationCompleteWithoutEffectDecision",
    "EffectObservationContinuationDecision",
    "EffectObservationContinuationExecuteDecision",
    "EffectObservationExecuteDecision",
    "FinalGenerationDecision",
    "InitialPlanDecision",
    "LegacyModelDecision",
    "MacroPlanDecision",
    "MacroPlanStep",
    "ModelDecisionValue",
    "ModelDecisionWithCompatibility",
    "ReactiveFinalDecision",
    "ReactiveToolDecision",
    "ReasoningBoundaryBlockedDecision",
    "ReasoningBoundaryCompleteDecision",
    "ReasoningBoundaryContinuationBlockedDecision",
    "ReasoningBoundaryContinuationCompleteDecision",
    "ReasoningBoundaryContinuationDecision",
    "ReasoningBoundaryContinuationExecuteDecision",
    "ReasoningBoundaryExecuteDecision",
    "ReplanDecision",
    "SummarizationDecision",
    "TaskContractBlockedDecision",
    "TaskContractDecision",
    "TaskContractNeedsInputDecision",
    "TaskSpecBlockedDecision",
    "TaskSpecDecision",
    "ToolDiscoveryDecision",
    "admit_typed_model_decision",
    "ask_model_decision_with_compatibility",
    "ask_typed_model_decision",
]
