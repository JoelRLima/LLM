"""Pure semantics resolver for one concrete tool invocation.

The descriptor supplies trusted capability metadata. Arguments refine the
descriptor only through bounded operation contracts; model-authored metadata
or target claims are never used as authority evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from agent.resources.contracts import ResourceAccess
from agent.tools.invocation_semantics_support import (
    CODE_COMMAND_ACTIONS,
    CODE_READ_ACTIONS,
    CODE_TASK_ACTIONS,
    CODE_WRITE_ACTIONS,
    resolve_invocation_components,
)


@dataclass(frozen=True, slots=True)
class InvocationSemantics:
    tool_name: str
    action: str | None
    capabilities: frozenset[str]
    durable_effects: tuple[str, ...] = ()
    resource_access: tuple[ResourceAccess, ...] = ()
    read_only: bool = True
    cancellation_safe: bool = True
    cacheable: bool = False
    idempotent: bool = False
    requires_validation: bool = False
    external_side_effects: tuple[str, ...] = ()
    task_state_mutation: bool = False
    workspace_mutation: bool = False

    @property
    def may_mutate(self) -> bool:
        """Whether this invocation can durably mutate task/application state."""

        return self.task_state_mutation or self.workspace_mutation

    @property
    def required_capabilities(self) -> frozenset[str]:
        return self.capabilities

    @property
    def resource_claims(self) -> tuple[ResourceAccess, ...]:
        return self.resource_access


def resolve_invocation_semantics(
    descriptor: Any, args: Mapping[str, Any] | None = None,
) -> InvocationSemantics:
    """Resolve descriptor plus concrete arguments into one immutable fact."""

    arguments = args if isinstance(args, Mapping) else {}
    name, action, capabilities, durable, read_only, accesses = resolve_invocation_components(
        descriptor, arguments
    )
    external = tuple(
        capability
        for capability in ("network", "process", "package_install", "validate")
        if capability in capabilities
    )
    workspace_mutation = any(
        access.write and access.name != "memory"
        for access in accesses
    )
    task_state_mutation = bool(durable)
    cancellation_safety = getattr(descriptor, "cancellation_safety", None)
    cancellation_safe = str(getattr(cancellation_safety, "value", cancellation_safety)).casefold() != "unsupported"
    return InvocationSemantics(
        tool_name=name,
        action=action,
        capabilities=frozenset(capabilities),
        durable_effects=tuple(dict.fromkeys(durable)),
        resource_access=accesses,
        read_only=read_only,
        cancellation_safe=cancellation_safe,
        cacheable=bool(getattr(descriptor, "cacheable", False)) and read_only,
        idempotent=bool(getattr(descriptor, "idempotent", False)),
        requires_validation="validate" in capabilities and not read_only,
        external_side_effects=external,
        task_state_mutation=task_state_mutation,
        workspace_mutation=workspace_mutation,
    )


__all__ = [
    "CODE_COMMAND_ACTIONS",
    "CODE_READ_ACTIONS",
    "CODE_TASK_ACTIONS",
    "CODE_WRITE_ACTIONS",
    "InvocationSemantics",
    "resolve_invocation_semantics",
]
