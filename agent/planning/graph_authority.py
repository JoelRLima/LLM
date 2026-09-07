"""Graph-level capability derivation and preflight.

Node-declared capabilities are requests at this boundary, never grants.  For
registered actions the trusted invocation-semantic contract is authoritative;
the union is checked against the immutable parent context before a node can be
scheduled.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from agent.capabilities import Capability, canonical_capabilities, capability_values
from agent.tools.invocation_semantics import CODE_TASK_ACTIONS, resolve_invocation_semantics


class GraphAuthorityError(PermissionError):
    """Fail-closed graph derivation or preflight error."""

    def __init__(self, reason_code: str, detail: str | None = None) -> None:
        self.reason_code = reason_code
        self.code = reason_code
        self.missing_capabilities = tuple(
            item.strip()
            for item in (detail or "").split(",")
            if item.strip()
        ) if reason_code == "GRAPH_CAPABILITY_DENIED" else ()
        super().__init__(reason_code if detail is None else f"{reason_code}: {detail}")


@dataclass(frozen=True, slots=True)
class GraphNodeRequirement:
    node_id: str
    required_capabilities: frozenset[str]
    source: str
    nested_requirements: tuple["GraphNodeRequirement", ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "required_capabilities",
            capability_values(self.required_capabilities),
        )


@dataclass(frozen=True, slots=True)
class GraphAuthorityRequirements:
    required_capabilities: frozenset[str]
    node_requirements: tuple[GraphNodeRequirement, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "required_capabilities",
            capability_values(self.required_capabilities),
        )

    def for_node(self, node_id: str) -> GraphNodeRequirement:
        for item in self.node_requirements:
            if item.node_id == node_id:
                return item
        raise GraphAuthorityError("GRAPH_NODE_REQUIREMENT_MISSING", node_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "required_capabilities": sorted(self.required_capabilities),
            "nodes": {
                item.node_id: {
                    "required_capabilities": sorted(item.required_capabilities),
                    "source": item.source,
                }
                for item in self.node_requirements
            },
        }


class _TrustedCodeTaskDescriptor:
    name = "code_task"
    capabilities = capability_values(
        (
            Capability.READ,
            Capability.WRITE,
            Capability.VALIDATE,
            Capability.ANALYZE,
            Capability.PROCESS,
        )
    )
    cacheable = False
    idempotent = False
    cancellation_safety = "unsupported"


def _descriptor_for(tool_name: str, registry: Any) -> Any:
    if tool_name == "code_task":
        return _TrustedCodeTaskDescriptor()
    if registry is None:
        raise GraphAuthorityError("GRAPH_TOOL_REGISTRY_REQUIRED", tool_name)
    descriptor = None
    lookup = getattr(registry, "descriptor", None)
    if callable(lookup):
        try:
            descriptor = lookup(tool_name)
        except (KeyError, LookupError) as exc:
            raise GraphAuthorityError("GRAPH_UNKNOWN_TOOL", tool_name) from exc
    elif isinstance(registry, Mapping):
        descriptor = registry.get(tool_name)
    if descriptor is None:
        raise GraphAuthorityError("GRAPH_UNKNOWN_TOOL", tool_name)
    return descriptor


def _resolve_trusted_action_semantics(
    metadata: Mapping[str, Any],
    raw_tool: Any,
    raw_action: Any,
    trusted_tool_registry: Any,
    *,
    max_depth: int,
    strict_w14: bool,
) -> tuple[frozenset[str], str, tuple[GraphNodeRequirement, ...]]:
    tool_name = str(raw_tool or "code_task").strip().casefold()
    action = str(raw_action).strip().casefold() if raw_action is not None else None
    if tool_name == "code_task" and action is None:
        action = "analyze"
    if tool_name == "code_task" and action not in CODE_TASK_ACTIONS:
        raise GraphAuthorityError("GRAPH_UNKNOWN_ACTION", action or "")
    descriptor = _descriptor_for(tool_name, trusted_tool_registry)
    semantic_metadata = dict(metadata)
    if tool_name == "code_task" and "action" not in semantic_metadata:
        semantic_metadata["action"] = action
    try:
        semantics = resolve_invocation_semantics(descriptor, semantic_metadata)
    except (TypeError, ValueError, KeyError) as exc:
        raise GraphAuthorityError("GRAPH_ACTION_SEMANTICS_INVALID", tool_name) from exc
    required = capability_values(semantics.required_capabilities)
    nested: tuple[GraphNodeRequirement, ...] = ()
    nested_raw = metadata.get("graph")
    if nested_raw is not None:
        try:
            from .task_graph import task_graph_from_dict

            nested_graph = task_graph_from_dict(nested_raw)
        except (TypeError, ValueError) as exc:
            raise GraphAuthorityError("GRAPH_NESTED_INVALID") from exc
        nested_result = derive_graph_requirements(
            nested_graph,
            trusted_tool_registry=trusted_tool_registry,
            max_depth=max_depth - 1,
            strict_w14=strict_w14,
        )
        nested = nested_result.node_requirements
        required = capability_values(
            set(required) | set(nested_result.required_capabilities)
        )
    return required, f"trusted-invocation:{tool_name}:{action or 'default'}", nested


def _trusted_action_semantics(
    node: Any,
    trusted_tool_registry: Any,
    *,
    max_depth: int,
    strict_w14: bool,
) -> tuple[frozenset[str], str, tuple[GraphNodeRequirement, ...]]:
    metadata = getattr(node, "metadata", {})
    if not isinstance(metadata, Mapping):
        raise GraphAuthorityError("GRAPH_METADATA_INVALID", str(getattr(node, "node_id", "")))
    raw_tool = metadata.get("tool")
    raw_action = metadata.get("action")
    has_trusted_action = raw_tool is not None or raw_action is not None
    if not has_trusted_action:
        if strict_w14:
            # CodingTaskNodeExecutor has one deterministic default: a node
            # without an explicit action is an analyze invocation.  The
            # declared node.capabilities set remains a non-authoritative
            # request and is deliberately ignored on this path.
            raw_tool = "code_task"
            raw_action = "analyze"
        else:
            try:
                requested = capability_values(canonical_capabilities(getattr(node, "capabilities", ())))
            except (TypeError, ValueError) as exc:
                raise GraphAuthorityError("GRAPH_UNKNOWN_CAPABILITY") from exc
            return requested, "legacy-explicit-request", ()
    if not (has_trusted_action or strict_w14):
        raise GraphAuthorityError("GRAPH_ACTION_SEMANTICS_MISSING", str(getattr(node, "node_id", "")))
    return _resolve_trusted_action_semantics(
        metadata,
        raw_tool,
        raw_action,
        trusted_tool_registry,
        max_depth=max_depth,
        strict_w14=strict_w14,
    )


def derive_graph_requirements(
    graph: Any,
    trusted_tool_registry: Any = None,
    *,
    max_depth: int = 8,
    strict_w14: bool = False,
) -> GraphAuthorityRequirements:
    """Derive one truthful canonical capability union for the whole graph."""

    if max_depth <= 0:
        raise GraphAuthorityError("GRAPH_NESTING_LIMIT")
    raw_nodes = getattr(graph, "nodes", None)
    nodes: tuple[Any, ...]
    if isinstance(raw_nodes, tuple):
        nodes = raw_nodes
    else:
        try:
            nodes = tuple(raw_nodes) if raw_nodes is not None else ()
        except TypeError as exc:
            raise GraphAuthorityError("GRAPH_INVALID") from exc
    requirements: list[GraphNodeRequirement] = []
    union: set[str] = set()
    for node in nodes:
        try:
            required, source, nested = _trusted_action_semantics(
                node,
                trusted_tool_registry,
                max_depth=max_depth,
                strict_w14=strict_w14,
            )
        except GraphAuthorityError:
            raise
        requirement = GraphNodeRequirement(
            str(getattr(node, "node_id", "")),
            required,
            source,
            nested,
        )
        requirements.append(requirement)
        union.update(required)
    return GraphAuthorityRequirements(frozenset(union), tuple(requirements))


def preflight_graph_capabilities(
    graph: Any,
    parent_permissions: Any,
    trusted_tool_registry: Any = None,
    *,
    strict_w14: bool = False,
) -> GraphAuthorityRequirements:
    """Derive, then enforce, the graph-vs-parent subset relation."""

    requirements = derive_graph_requirements(
        graph,
        trusted_tool_registry=trusted_tool_registry,
        strict_w14=strict_w14,
    )
    try:
        parent = canonical_capabilities(parent_permissions)
    except (TypeError, ValueError) as exc:
        raise GraphAuthorityError("GRAPH_PARENT_CAPABILITIES_INVALID") from exc
    missing = set(requirements.required_capabilities) - {item.value for item in parent}
    if missing:
        detail = ", ".join(sorted(missing))
        raise GraphAuthorityError("GRAPH_CAPABILITY_DENIED", detail)
    return requirements


__all__ = [
    "GraphAuthorityError",
    "GraphAuthorityRequirements",
    "GraphNodeRequirement",
    "derive_graph_requirements",
    "preflight_graph_capabilities",
]
