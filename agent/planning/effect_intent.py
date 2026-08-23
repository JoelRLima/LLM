"""Bounded mapping from concrete model-plan operations to task effects."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent.planning.task_semantics_inference import infer_effect_semantics

_WRITE_CAPABILITIES = frozenset({"write", "vcs_write"})
_CODE_WRITE_ACTIONS = frozenset({"generate", "modify", "repair", "refactor"})


def _contract_capabilities(contract: Any) -> frozenset[str]:
    raw = getattr(contract, "required_capabilities", None)
    if raw is None:
        raw = getattr(contract, "capabilities", ())
    return frozenset(str(item).casefold() for item in (raw or ()))


def _code_task_writes(args: Mapping[str, Any]) -> bool:
    action = str(args.get("action", "")).casefold()
    if action in _CODE_WRITE_ACTIONS:
        return True
    if action == "template":
        return str(args.get("template", "")).casefold() == "analyze_then_modify"
    if action != "multitask":
        return False
    graph = args.get("graph")
    nodes = graph.get("nodes") if isinstance(graph, Mapping) else None
    if not isinstance(nodes, list):
        return True
    for node in nodes:
        if not isinstance(node, Mapping):
            continue
        metadata = node.get("metadata")
        metadata = metadata if isinstance(metadata, Mapping) else {}
        node_action = str(metadata.get("action", "")).casefold()
        capabilities = frozenset(
            str(item).casefold() for item in (node.get("capabilities") or ())
        )
        if node_action in _CODE_WRITE_ACTIONS or capabilities & _WRITE_CAPABILITIES:
            return True
    return False


def operation_durable_effect(
    tool_name: str,
    args: Mapping[str, Any] | None,
    contract: Any = None,
) -> str | None:
    """Return the stable effect kind for one concrete model-plan step."""

    normalized_tool = str(tool_name).strip().casefold()
    concrete_args = args if isinstance(args, Mapping) else {}
    if normalized_tool == "session_memory":
        return (
            "memory_write"
            if str(concrete_args.get("action", "")).casefold() in {"set", "delete"}
            else None
        )
    # The model-actionable shell surface is intentionally restricted to
    # read-only validation/history/tree commands.  Its broad descriptor
    # capability set is an authorization ceiling, not proof of a durable
    # effect for a concrete command.
    if normalized_tool == "shell":
        return None
    if normalized_tool == "code_task":
        return "write" if _code_task_writes(concrete_args) else None
    return "write" if _contract_capabilities(contract) & _WRITE_CAPABILITIES else None


def effect_intent_error(
    objective: str,
    tool_name: str,
    args: Mapping[str, Any] | None,
    contract: Any = None,
) -> str | None:
    """Reject model-proposed durable effects absent from trusted task intent."""

    effect = operation_durable_effect(tool_name, args, contract)
    if effect is None:
        return None
    semantics = infer_effect_semantics(objective)
    # A write appearing in both sets is the canonical representation of a
    # conditional branch (write in one branch, no-write in another).  The
    # concrete plan/effect footprint remains authoritative for what happened;
    # reject only an effect that is exclusively prohibited or unrequested.
    if effect in semantics.prohibited and effect not in semantics.requested:
        return (
            f"PROHIBITED_EFFECT: a ferramenta '{tool_name}' propoe o efeito duravel "
            f"'{effect}' proibido pelo objetivo."
        )
    if effect not in semantics.requested:
        return (
            f"UNREQUESTED_EFFECT: a ferramenta '{tool_name}' propoe o efeito duravel "
            f"'{effect}' nao solicitado pelo objetivo."
        )
    return None


__all__ = ["effect_intent_error", "operation_durable_effect"]
