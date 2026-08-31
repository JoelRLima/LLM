"""Typed projections from already-admitted decision mappings."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from agent.llm.admitted_decision_core import (
    DirectResponseDecision,
    EffectObservationBlockedDecision,
    EffectObservationCompleteWithoutEffectDecision,
    EffectObservationExecuteDecision,
    InitialPlanDecision,
    ReasoningBoundaryBlockedDecision,
    ReasoningBoundaryCompleteDecision,
    ReasoningBoundaryExecuteDecision,
    _freeze_items,
    _freeze_mapping,
    _freeze_mappings,
)
from agent.llm.admitted_decision_variants import (
    DecisionBinding,
    FinalGenerationDecision,
    MacroPlanDecision,
    MacroPlanStep,
    ModelDecisionValue,
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
from agent.llm.decision_contract import ModelRequestContract
from agent.task_definition.models import TaskContract, TaskSpec


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


def _project_task_contract(value: Mapping[str, Any]) -> ModelDecisionValue:
    action = value.get("action")
    if action == "define_contract":
        return TaskContractDecision(contract=TaskContract.from_dict(value["contract"]))
    if action == "needs_input":
        return TaskContractNeedsInputDecision(
            reason=value["reason"],
            question=value["question"],
        )
    return TaskContractBlockedDecision(reason=value["reason"])


def _project_task_spec(value: Mapping[str, Any]) -> ModelDecisionValue:
    if value.get("action") == "define_spec":
        return TaskSpecDecision(spec=TaskSpec.from_dict(value["spec"]))
    return TaskSpecBlockedDecision(reason=value["reason"])


def project_admitted(
    value: Mapping[str, Any],
    contract: ModelRequestContract,
) -> ModelDecisionValue:
    """Project a value returned by the exact admission contract."""

    authority = {
        ModelRequestContract.TASK_CONTRACT: _project_task_contract,
        ModelRequestContract.TASK_SPEC: _project_task_spec,
        ModelRequestContract.INITIAL_PLAN: _project_initial,
        ModelRequestContract.EFFECT_OBSERVATION_CONTINUATION: _project_effect,
        ModelRequestContract.REASONING_BOUNDARY_CONTINUATION: _project_reasoning,
        ModelRequestContract.MACRO_PLAN: _project_macro,
    }
    projector = authority.get(contract)
    if projector is not None:
        return projector(value)
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


__all__ = ["project_admitted"]
