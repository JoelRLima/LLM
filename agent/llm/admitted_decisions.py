"""Typed projections after exact ``ModelRequestContract`` admission."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
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
    _freeze_items,
    _freeze_mapping,
    _freeze_mappings,
)
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
    ToolDiscoveryDecision,
)
from agent.llm.decision_contract import (
    ModelRequestContract,
    admit_model_decision_value,
    legacy_model_decision_compatibility,
    resolve_request_contract,
)


def _bindings(value: Mapping[str, Any]) -> Mapping[str, DecisionBinding]:
    return MappingProxyType(
        {
            target: DecisionBinding(
                from_step=binding["from_step"],
                path=tuple(binding["path"]),
            )
            for target, binding in value.items()
        }
    )


def _project_initial(value: Mapping[str, Any]) -> ModelDecisionValue:
    if value.get("action") == "direct_response":
        return DirectResponseDecision(answer=value["answer"])
    obligations = value.get("obligations")
    return InitialPlanDecision(
        plan=_freeze_mappings(value["plan"]),
        obligations=_freeze_items(obligations) if obligations is not None else None,
    )


def _project_effect(value: Mapping[str, Any]) -> ModelDecisionValue:
    action = value.get("action")
    if action == "execute":
        return EffectObservationExecuteDecision(plan=_freeze_mappings(value["plan"]))
    if action == "complete_without_effect":
        return EffectObservationCompleteWithoutEffectDecision(
            observation_index=value["observation_index"]
        )
    return EffectObservationBlockedDecision(reason=value["reason"])


def _project_reasoning(value: Mapping[str, Any]) -> ModelDecisionValue:
    action = value.get("action")
    obligations = value.get("obligations")
    frozen_obligations = _freeze_items(obligations) if obligations is not None else None
    if action == "execute":
        return ReasoningBoundaryExecuteDecision(
            plan=_freeze_mappings(value["plan"]), obligations=frozen_obligations
        )
    if action == "complete":
        return ReasoningBoundaryCompleteDecision(
            reason=value["reason"], obligations=frozen_obligations
        )
    return ReasoningBoundaryBlockedDecision(reason=value["reason"])


def _project_macro(value: Mapping[str, Any]) -> ModelDecisionValue:
    return MacroPlanDecision(
        steps=tuple(
            MacroPlanStep(
                id=step["id"],
                title=step["title"],
                goal=step["goal"],
                priority=step["priority"],
                depends_on=tuple(step["depends_on"]) if "depends_on" in step else None,
                estimated_tools=(
                    tuple(step["estimated_tools"])
                    if "estimated_tools" in step
                    else None
                ),
            )
            for step in value["steps"]
        )
    )


def _project_reactive(
    value: Mapping[str, Any], contract: ModelRequestContract
) -> ModelDecisionValue:
    if contract is ModelRequestContract.REACTIVE_TOOL_DECISION and value.get("action") == "final":
        return ReactiveFinalDecision(answer=value["answer"])
    args = _freeze_mapping(value["args"])
    raw_bindings = value.get("bindings")
    bindings = _bindings(raw_bindings) if raw_bindings is not None else None
    if contract is ModelRequestContract.REPLAN:
        return ReplanDecision(tool=value["tool"], args=args, bindings=bindings)
    return ReactiveToolDecision(tool=value["tool"], args=args, bindings=bindings)


def _project_admitted(
    value: Mapping[str, Any], contract: ModelRequestContract
) -> ModelDecisionValue:
    """Project a value already returned by ``admit_model_decision_value``."""

    if contract is ModelRequestContract.INITIAL_PLAN:
        return _project_initial(value)
    if contract is ModelRequestContract.EFFECT_OBSERVATION_CONTINUATION:
        return _project_effect(value)
    if contract is ModelRequestContract.REASONING_BOUNDARY_CONTINUATION:
        return _project_reasoning(value)
    if contract is ModelRequestContract.MACRO_PLAN:
        return _project_macro(value)
    if contract in {
        ModelRequestContract.REACTIVE_TOOL_DECISION,
        ModelRequestContract.REPLAN,
    }:
        return _project_reactive(value, contract)
    if contract is ModelRequestContract.FINAL_GENERATION:
        return FinalGenerationDecision(answer=value["answer"])
    if contract is ModelRequestContract.SUMMARIZATION:
        return SummarizationDecision(summary=value["summary"])
    if contract is ModelRequestContract.TOOL_DISCOVERY:
        return ToolDiscoveryDecision(tools=tuple(value["tools"]))
    raise ValueError(f"unsupported request contract: {contract!r}")


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
    """Request one decision while keeping legacy responses explicitly noncanonical.

    This is the bounded compatibility edge for old model fixtures and persisted
    callers.  Canonical responses still take the exact admission path; a
    retained compatibility shape is wrapped separately and never returned as
    an ``AdmittedModelDecision``.
    """

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
        payload=_freeze_mapping(compatible),
    )

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
    "ToolDiscoveryDecision",
    "admit_typed_model_decision",
    "ask_model_decision_with_compatibility",
    "ask_typed_model_decision",
]
