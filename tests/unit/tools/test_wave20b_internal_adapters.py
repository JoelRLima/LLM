"""W20-B internal-adapter composition tests; intentionally not run yet."""

from __future__ import annotations

import pytest

from agent.tools.contracts import ToolDescriptor
from agent.tools.extension_bootstrap import WorkspaceToolRegistryComposer
from agent.tools.extension_runtime import ExtensionRuntimeMaterialization


class _Adapter:
    def __init__(self, name: str) -> None:
        self._descriptor = ToolDescriptor(name=name, description="bounded")

    def descriptors(self) -> tuple[ToolDescriptor, ...]:
        return (self._descriptor,)

    def invoke(self, invocation: object) -> object:
        del invocation
        return object()


def test_support_internal_adapter_collision_fails_before_extensions() -> None:
    with pytest.raises(ValueError):
        WorkspaceToolRegistryComposer().compose(
            _Adapter("reserved"),
            ExtensionRuntimeMaterialization(),
            internal_adapters=(_Adapter("reserved"),),
        )


def test_support_internal_adapter_order_is_explicit() -> None:
    result = WorkspaceToolRegistryComposer().compose(
        _Adapter("builtin"),
        ExtensionRuntimeMaterialization(),
        internal_adapters=(_Adapter("internal"),),
    )
    assert result.registry.names() == ("builtin", "internal")
