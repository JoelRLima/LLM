"""Bounded mapping from concrete model-plan operations to task effects."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent.planning.task_semantics_authority import admit_effect_authority
from agent.planning.task_semantics_inference import predicate_resolutions_from_observations
from agent.planning.task_semantics_types import PredicateEvidence, PredicateResolutionState
from agent.resources.contracts import WORKSPACE_RESOURCE, ResourceAccess, ResourceMode, resources_overlap
from agent.tools.invocation_semantics import resolve_invocation_semantics


def _semantic_descriptor(tool_name: str, contract: Any) -> Any:
    """Build a disconnected descriptor view from trusted contract metadata."""

    class _Descriptor:
        name = tool_name
        capabilities = getattr(contract, "required_capabilities", None)
        if capabilities is None:
            capabilities = getattr(contract, "capabilities", ())
        cacheable = bool(getattr(contract, "cacheable", False))
        idempotent = bool(getattr(contract, "idempotent", False))
        cancellation_safety = getattr(contract, "cancellation_safety", "unsupported")

    return _Descriptor()


def operation_durable_effect(
    tool_name: str,
    args: Mapping[str, Any] | None,
    contract: Any = None,
) -> str | None:
    """Return the stable effect kind for one concrete model-plan step."""

    concrete_args = args if isinstance(args, Mapping) else {}
    normalized_tool = str(tool_name).strip().casefold()
    descriptor = (
        contract
        if str(getattr(contract, "name", "")).strip().casefold() == normalized_tool
        else _semantic_descriptor(normalized_tool, contract)
    )
    semantics = resolve_invocation_semantics(descriptor, concrete_args)
    return semantics.durable_effects[0] if semantics.durable_effects else None


def _operation_accesses(
    tool_name: str,
    args: Mapping[str, Any] | None,
    contract: Any = None,
) -> tuple[ResourceAccess, ...]:
    concrete_args = args if isinstance(args, Mapping) else {}
    normalized_tool = str(tool_name).strip().casefold()
    descriptor = (
        contract
        if str(getattr(contract, "name", "")).strip().casefold() == normalized_tool
        else _semantic_descriptor(normalized_tool, contract)
    )
    return resolve_invocation_semantics(descriptor, concrete_args).resource_access


def _predicate_is_active(
    intent: Any,
    predicate_resolutions: Mapping[str, Any] | None = None,
) -> bool:
    predicate_id = getattr(intent, "predicate_id", None)
    condition = getattr(intent, "condition", None)
    if predicate_id is None:
        # A condition string without a canonical identity is decorative text,
        # never permission.
        return condition is None
    state = getattr(intent, "predicate_state", PredicateResolutionState.UNRESOLVED)
    if predicate_resolutions is not None:
        raw = predicate_resolutions.get(predicate_id)
        if isinstance(raw, PredicateEvidence):
            state = (
                raw.state
                if raw.predicate_id == predicate_id
                else PredicateResolutionState.UNRESOLVED
            )
        elif isinstance(raw, Mapping):
            try:
                evidence = PredicateEvidence(
                    predicate_id,
                    raw["state"],
                    raw["evidence_ref"],
                    raw["provenance"],
                )
                state = evidence.state
            except (KeyError, TypeError, ValueError):
                state = PredicateResolutionState.UNRESOLVED
    if not isinstance(state, PredicateResolutionState):
        try:
            state = PredicateResolutionState(str(state).strip().upper())
        except ValueError:
            state = PredicateResolutionState.UNRESOLVED
    if state is PredicateResolutionState.UNRESOLVED:
        return False
    expected = getattr(intent, "predicate_expected", None)
    if type(expected) is not bool:
        return False
    return (state is PredicateResolutionState.TRUE) is expected


def _intent_matches(
    intent: Any,
    effect: str,
    access: ResourceAccess,
    predicate_resolutions: Mapping[str, Any] | None = None,
) -> bool:
    if intent.effect != effect:
        return False
    if not _predicate_is_active(intent, predicate_resolutions):
        return False
    return intent.target == WORKSPACE_RESOURCE or resources_overlap(
        intent.target, access.name
    )


def effect_intent_matches(
    intent: Any,
    effect: str,
    access: ResourceAccess,
    *,
    predicate_resolutions: Mapping[str, Any] | None = None,
) -> bool:
    """Public target matcher shared by plan and observed-effect checks."""

    return _intent_matches(intent, effect, access, predicate_resolutions)


def effect_intent_error(
    objective: str,
    tool_name: str,
    args: Mapping[str, Any] | None,
    contract: Any = None,
    *,
    predicate_resolutions: Mapping[str, Any] | None = None,
    available_observations: Any = None,
) -> str | None:
    """Reject model-proposed durable effects absent from trusted task intent."""

    concrete_args = args if isinstance(args, Mapping) else {}
    normalized_tool = str(tool_name).strip().casefold()
    effect = operation_durable_effect(tool_name, args, contract)
    if effect is None:
        return None
    authority = admit_effect_authority(objective)
    if predicate_resolutions is None and available_observations is not None:
        predicate_resolutions = predicate_resolutions_from_observations(
            objective,
            available_observations,
        )
    accesses = _operation_accesses(tool_name, args, contract)
    if not accesses:
        accesses = (ResourceAccess(WORKSPACE_RESOURCE, ResourceMode.WRITE),)
    requested_intents = authority.authorized_effects
    prohibited_intents = authority.constraint_intents
    requested_matches = tuple(
        intent
        for intent in requested_intents
        if any(
            _intent_matches(intent, effect, access, predicate_resolutions)
            for access in accesses
        )
    )
    prohibited_matches = tuple(
        intent
        for intent in prohibited_intents
        if any(
            _intent_matches(intent, effect, access, predicate_resolutions)
            for access in accesses
        )
    )
    conditional_intents = tuple(
        item.candidate
        for item in authority.decisions
        if getattr(item.candidate, "conditional", False)
        or getattr(item.candidate, "condition", None) is not None
    )
    proposal_only_code_task = (
        normalized_tool == "code_task"
        and effect == "write"
        and str(concrete_args.get("action", "")).strip().casefold()
        not in {"analyze", "review"}
        and authority.proposal_only
    )
    if proposal_only_code_task:
        # code_task first materializes a non-durable ChangeSet preview. The
        # code-task boundary separately forces approval to remain required so
        # this exception can never turn an explicit no-apply request into a
        # physical mutation.
        return None
    # A prohibition wins for the same concrete target unless the objective
    # represented distinct conditional branches.  This prevents a broad
    # requested write from laundering a target-specific prohibition.
    conditional_pair = bool(
        requested_matches
        and prohibited_matches
        and all(
            getattr(item, "condition", None)
            for item in (*requested_matches, *prohibited_matches)
        )
    )
    if prohibited_matches and not conditional_pair:
        return (
            f"PROHIBITED_EFFECT: a ferramenta '{tool_name}' propoe o efeito duravel "
            f"'{effect}' proibido pelo objetivo."
        )
    if not requested_matches:
        if conditional_intents:
            return (
                f"UNRESOLVED_CONDITIONAL_EFFECT: a ferramenta '{tool_name}' propoe o efeito duravel "
                "sem predicate resolvido por evidencia confiavel."
            )
        return (
            f"UNREQUESTED_EFFECT: a ferramenta '{tool_name}' propoe o efeito duravel "
            f"'{effect}' nao solicitado pelo objetivo."
        )
    return None


__all__ = ["effect_intent_error", "effect_intent_matches", "operation_durable_effect"]
