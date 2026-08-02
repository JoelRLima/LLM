"""Dynamic tool registry aggregating multiple tool adapters."""

from __future__ import annotations

from typing import Dict, List, Tuple

from agent.planning.tool_metadata import ToolMetadata
from agent.tools.contracts import (
    ToolAdapter,
    ToolDescriptor,
    ToolError,
    ToolInvocation,
    ToolResult,
    ToolStatus,
)


class ToolRegistry:
    """Central registry aggregating tool adapters and resolving invocations."""

    def __init__(self) -> None:
        self._adapters: List[ToolAdapter] = []
        self._descriptors_cache: Dict[str, Tuple[ToolAdapter, ToolDescriptor]] = {}
        self._frozen = False

    def register_adapter(self, adapter: ToolAdapter) -> None:
        if self._frozen:
            raise RuntimeError("ToolRegistry congelado após o bootstrap")
        descriptors = tuple(adapter.descriptors())
        if not descriptors:
            return
        names = [descriptor.name for descriptor in descriptors]
        if len(set(names)) != len(names):
            raise ValueError("Adapter contém nomes de tools duplicados")
        collisions = sorted(set(names) & self._descriptors_cache.keys())
        if collisions:
            raise ValueError(
                "Ferramenta já registrada no ToolRegistry: " + ", ".join(collisions)
            )
        for descriptor in descriptors:
            self._validate_descriptor(descriptor)
        self._adapters.append(adapter)
        self._refresh()

    def freeze(self) -> None:
        """Freeze the published snapshot after bootstrap composition."""

        self._frozen = True

    @property
    def frozen(self) -> bool:
        return self._frozen

    @staticmethod
    def _validate_descriptor(descriptor: ToolDescriptor) -> None:
        if not descriptor.name or descriptor.name.strip() != descriptor.name:
            raise ValueError("Nome de tool inválido")
        if not isinstance(descriptor.schema, dict):
            raise ValueError(f"Schema inválido para tool '{descriptor.name}'")
        if descriptor.cost < 0:
            raise ValueError(f"Custo inválido para tool '{descriptor.name}'")

    def _refresh(self) -> None:
        self._descriptors_cache.clear()
        for adapter in self._adapters:
            for desc in adapter.descriptors():
                self._descriptors_cache[desc.name] = (adapter, desc)

    def descriptor(self, name: str) -> ToolDescriptor:
        if name not in self._descriptors_cache:
            raise KeyError(f"Ferramenta não registrada no ToolRegistry: {name}")
        return self._descriptors_cache[name][1]

    def descriptors(self) -> Tuple[ToolDescriptor, ...]:
        return tuple(self._descriptors_cache[name][1] for name in sorted(self._descriptors_cache))

    def names(self) -> Tuple[str, ...]:
        return tuple(sorted(self._descriptors_cache))

    def invoke(self, invocation: ToolInvocation) -> ToolResult:
        if invocation.tool_name not in self._descriptors_cache:
            return ToolResult(
                invocation_id=invocation.invocation_id,
                status=ToolStatus.UNAVAILABLE,
                error=ToolError(
                    "TOOL_NOT_FOUND",
                    f"Ferramenta '{invocation.tool_name}' não foi registrada.",
                ),
                message=f"Ferramenta indisponível: {invocation.tool_name}",
            )
        adapter, _ = self._descriptors_cache[invocation.tool_name]
        return adapter.invoke(invocation)

    def metadata_dict(self) -> Dict[str, ToolMetadata]:
        """Convert descriptors to ToolMetadata dictionary for plan validation and optimization."""
        result: Dict[str, ToolMetadata] = {}
        for desc in self.descriptors():
            reads = any(c in desc.capabilities for c in ("read", "vcs_read"))
            writes = any(c in desc.capabilities for c in ("write", "vcs_write"))
            side_effects = writes or any(
                c in desc.capabilities for c in ("process", "network", "package_install")
            )
            result[desc.name] = ToolMetadata(
                cost=desc.cost,
                reads_disk=reads,
                writes_disk=writes,
                modifies_workspace=writes,
                cacheable=desc.cacheable,
                side_effects=side_effects,
                category=desc.category,
            )
        return result
