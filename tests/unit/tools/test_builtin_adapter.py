from pathlib import Path

from agent.skills import load_skill_registry
from agent.tools.builtin_adapter import BuiltinToolAdapter
from agent.tools.contracts import ToolInvocation, ToolStatus


def test_builtin_adapter_descriptors(tmp_path: Path) -> None:
    skill_registry = load_skill_registry(base_dir=tmp_path)
    adapter = BuiltinToolAdapter(skill_registry)
    descriptors = adapter.descriptors()

    assert len(descriptors) > 0
    names = [d.name for d in descriptors]
    assert "echo" in names
    assert "file_reader" in names


def test_builtin_adapter_invoke_success(tmp_path: Path) -> None:
    skill_registry = load_skill_registry(base_dir=tmp_path)
    adapter = BuiltinToolAdapter(skill_registry)

    invocation = ToolInvocation(tool_name="echo", args={"message": "hello world"})
    result = adapter.invoke(invocation)

    assert result.ok is True
    assert result.status == ToolStatus.SUCCEEDED
    assert result.data == "hello world"



def test_builtin_adapter_invoke_unavailable(tmp_path: Path) -> None:
    skill_registry = load_skill_registry(base_dir=tmp_path)
    adapter = BuiltinToolAdapter(skill_registry)

    invocation = ToolInvocation(tool_name="non_existent_tool", args={})
    result = adapter.invoke(invocation)

    assert result.ok is False
    assert result.status == ToolStatus.UNAVAILABLE
    assert result.error is not None
    assert result.error.code == "TOOL_NOT_FOUND"
