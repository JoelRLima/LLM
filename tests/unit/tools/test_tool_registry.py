import json
from pathlib import Path

import pytest

from agent.skills import load_skill_registry, load_tool_registry
from agent.tools.builtin_adapter import BuiltinToolAdapter
from agent.tools.contracts import ToolDescriptor, ToolInvocation, ToolResult, ToolStatus
from agent.tools.extension_registry import ExtensionRegistry
from agent.tools.tool_registry import ToolRegistry


class _Adapter:
    def __init__(self, descriptor: ToolDescriptor) -> None:
        self._descriptor = descriptor

    def descriptors(self) -> tuple[ToolDescriptor, ...]:
        return (self._descriptor,)

    def invoke(self, invocation: ToolInvocation) -> ToolResult:
        return ToolResult(invocation_id=invocation.invocation_id, status=ToolStatus.SUCCEEDED)


def test_tool_registry_registration_and_lookup(tmp_path: Path) -> None:
    skill_reg = load_skill_registry(base_dir=tmp_path)
    adapter = BuiltinToolAdapter(skill_reg)

    registry = ToolRegistry()
    registry.register_adapter(adapter)

    assert "echo" in registry.names()
    desc = registry.descriptor("echo")
    assert desc.name == "echo"


def test_tool_registry_metadata_dict(tmp_path: Path) -> None:
    skill_reg = load_skill_registry(base_dir=tmp_path)
    adapter = BuiltinToolAdapter(skill_reg)

    registry = ToolRegistry()
    registry.register_adapter(adapter)

    meta = registry.metadata_dict()
    assert "echo" in meta
    assert "file_reader" in meta
    assert meta["file_reader"].reads_disk is True


def test_tool_registry_invoke_unknown_tool() -> None:
    registry = ToolRegistry()
    invocation = ToolInvocation(tool_name="unknown", args={})
    res = registry.invoke(invocation)

    assert res.ok is False
    assert res.status == ToolStatus.UNAVAILABLE


def test_load_tool_registry_registers_enabled_extensions(tmp_path: Path) -> None:
    manifest_path = tmp_path / "demo-manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "id": "demo.extension",
                "version": "1.0.0",
                "protocol_version": "1.0",
                "transport": "stdio",
                "entrypoint": ["python", "-c", "print('{}')"],
                "timeout_seconds": 5,
                "tools": [
                    {
                        "name": "demo_tool",
                        "description": "Ferramenta de demonstração",
                        "schema": {},
                        "capabilities": ["read"],
                        "cost": 1,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    state_path = tmp_path / "extensions.json"
    registry = ExtensionRegistry(state_path)
    registry.add(id="demo.extension", manifest_path=manifest_path)

    tool_registry = load_tool_registry(base_dir=tmp_path, extensions_state_path=state_path)

    assert "echo" in tool_registry.names()
    assert "demo_tool" in tool_registry.names()


def test_tool_registry_freeze_is_idempotent_and_preserves_reads() -> None:
    registry = ToolRegistry()
    registry.register_adapter(_Adapter(ToolDescriptor("before", "safe", schema={"nested": {"items": [1]}})))

    registry.freeze()
    registry.freeze()

    assert registry.frozen is True
    assert registry.names() == ("before",)
    with pytest.raises(RuntimeError):
        registry.register_adapter(_Adapter(ToolDescriptor("after", "safe")))


def test_frozen_registry_descriptors_and_schemas_are_read_only() -> None:
    schema = {"nested": {"items": ["original"]}}
    descriptor = ToolDescriptor("immutable", "safe", schema=schema)
    registry = ToolRegistry()
    registry.register_adapter(_Adapter(descriptor))
    registry.freeze()

    schema["nested"]["items"].append("external")
    published = registry.descriptor("immutable")

    first = published.schema
    second = published.schema
    assert isinstance(first, dict)
    assert first["nested"]["items"] == ["original"]
    assert first is not second
    assert first["nested"] is not second["nested"]

    dict.__setitem__(first, "injected", True)
    dict.__setitem__(first["nested"], "injected", True)
    del first["nested"]["items"]
    copied = first.copy()
    copied["nested"] = {"changed": True}
    assert first.get("injected") is True
    assert published.schema == {"nested": {"items": ["original"]}}
    assert published.schema == second


def test_freeze_json_like_is_composed_and_strict() -> None:
    from agent.tools.contracts import freeze_json_like

    value = {"nested": [{"items": ("x",)}, True, 0, -2, "unicode ✓"]}
    snapshot = freeze_json_like(value)
    assert not isinstance(snapshot, (dict, list))
    assert not isinstance(snapshot["nested"], (dict, list))

    value["nested"][0]["items"] = ("changed",)
    assert snapshot["nested"][0]["items"] == ("x",)
    for invalid in (float("nan"), float("inf"), float("-inf"), object()):
        with pytest.raises((TypeError, ValueError)):
            freeze_json_like(invalid)

    class StringSubclass(str):
        pass

    class IntSubclass(int):
        pass

    class FloatSubclass(float):
        pass

    for invalid in (StringSubclass("x"), IntSubclass(1), FloatSubclass(1.0), {StringSubclass("x"): 1}):
        with pytest.raises(TypeError):
            freeze_json_like(invalid)

    cycle: list[object] = []
    cycle.append(cycle)
    with pytest.raises(ValueError):
        freeze_json_like(cycle)


def test_two_frozen_registries_remain_independent() -> None:
    first = ToolRegistry()
    second = ToolRegistry()
    first.register_adapter(_Adapter(ToolDescriptor("first", "safe")))
    second.register_adapter(_Adapter(ToolDescriptor("second", "safe")))

    first.freeze()

    assert first.names() == ("first",)
    assert second.names() == ("second",)
    assert second.frozen is False
    second.freeze()
