"""Semantic target and constraint enforcement for admitted effects."""

from __future__ import annotations

from typing import Any

from agent.resources.contracts import WORKSPACE_RESOURCE, ResourceAccess, resource_is_within


def _semantic_target_resources(admitted_intent: Any, grounded_targets: Any) -> tuple[str, ...]:
    if grounded_targets is not None:
        resources = getattr(grounded_targets, "mutation_targets", None)
        if isinstance(resources, (tuple, list, frozenset)):
            return tuple(str(item) for item in resources if isinstance(item, str))
    resources = getattr(admitted_intent, "mutation_targets", ())
    if isinstance(resources, (tuple, list, frozenset)):
        return tuple(str(item) for item in resources if isinstance(item, str))
    return ()


def _semantic_target_matches(
    accesses: tuple[ResourceAccess, ...],
    admitted_intent: Any,
    grounded_targets: Any,
) -> bool:
    targets = _semantic_target_resources(admitted_intent, grounded_targets)
    if not targets:
        return False
    # A concrete semantic mutation must remain bound to concrete grounded
    # resources.  A wildcard operation footprint is not allowed to consume a
    # narrower grounded set by overlap alone.
    if any(access.name == WORKSPACE_RESOURCE for access in accesses):
        return WORKSPACE_RESOURCE in targets
    return all(
        any(resource_is_within(target, access.name) for target in targets)
        for access in accesses
    )


def semantic_effect_error(
    tool_name: str,
    effect: str,
    accesses: tuple[ResourceAccess, ...],
    admitted_intent: Any,
    grounded_targets: Any,
    invocation_semantics: Any,
) -> str | None:
    requested = tuple(getattr(admitted_intent, "admitted_effects", ()) or ())
    prohibited = tuple(
        getattr(item, "effect", item)
        for item in (getattr(admitted_intent, "prohibited_effects", ()) or ())
    )
    if effect in prohibited:
        return (
            f"PROHIBITED_EFFECT: a ferramenta '{tool_name}' propoe o efeito duravel "
            f"'{effect}' proibido pela intent admitida."
        )
    if effect not in requested:
        return (
            f"UNREQUESTED_EFFECT: a ferramenta '{tool_name}' propoe o efeito duravel "
            f"'{effect}' nao admitido pela intent atual."
        )
    if getattr(admitted_intent, "requires_validation", False) and effect in {
        "write",
        "memory_write",
    } and not bool(getattr(invocation_semantics, "requires_validation", False)):
        return (
            f"REQUIRED_VALIDATION_UNSUPPORTED: a ferramenta '{tool_name}' "
            "nao oferece a validacao obrigatoria para o efeito admitido."
        )
    if getattr(admitted_intent, "proposal_only", False) and effect in {
        "write",
        "memory_write",
    }:
        action = str(getattr(invocation_semantics, "action", "") or "").strip().casefold()
        if not (
            str(tool_name).strip().casefold() == "code_task"
            and effect == "write"
            and action not in {"analyze", "review"}
        ):
            return (
                f"PROPOSAL_ONLY_EFFECT_FORBIDDEN: a ferramenta '{tool_name}' "
                "nao pode produzir mutacao duravel sob proposal_only."
            )
    if effect in {"write", "memory_write"} and not _semantic_target_matches(
        accesses, admitted_intent, grounded_targets
    ):
        return (
            f"UNAUTHORIZED_TARGET: a ferramenta '{tool_name}' propoe um recurso "
            "fora dos alvos concretos aterrados pela intent admitida."
        )
    return None


__all__ = ["semantic_effect_error"]
