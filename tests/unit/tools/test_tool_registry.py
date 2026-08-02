import json
from pathlib import Path

from agent.skills import load_skill_registry, load_tool_registry
from agent.tools.builtin_adapter import BuiltinToolAdapter
from agent.tools.contracts import ToolInvocation, ToolStatus
from agent.tools.extension_registry import ExtensionRegistry
from agent.tools.tool_registry import ToolRegistry


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
