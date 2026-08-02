from pathlib import Path

from agent.skills import load_skill_registry
from agent.tools.builtin_adapter import BuiltinToolAdapter
from agent.tools.contracts import ToolDescriptor, ToolInvocation, ToolResult, ToolStatus
from agent.tools.invocation_gateway import ToolInvocationGateway
from agent.tools.tool_registry import ToolRegistry


def test_gateway_run_success(tmp_path: Path) -> None:
    skill_reg = load_skill_registry(base_dir=tmp_path)
    registry = ToolRegistry()
    registry.register_adapter(BuiltinToolAdapter(skill_reg))

    events = []
    gateway = ToolInvocationGateway(
        registry,
        event_emitter=lambda event_type, data: events.append((event_type, data)),
    )

    result = gateway.run("echo", {"message": "hello"})

    assert result.ok is True
    assert result.status == ToolStatus.SUCCEEDED
    assert len(events) == 2
    assert events[0][0] == "tool_start"
    assert events[1][0] == "tool_end"
    assert events[0][1]["invocation_id"] == result.invocation_id
    assert events[1][1]["invocation_id"] == result.invocation_id


def test_gateway_permission_denied(tmp_path: Path) -> None:
    skill_reg = load_skill_registry(base_dir=tmp_path)
    registry = ToolRegistry()
    registry.register_adapter(BuiltinToolAdapter(skill_reg))

    gateway = ToolInvocationGateway(registry)

    # Active skills does not include echo
    result = gateway.run("echo", {"message": "hello"}, active_skills=["file_reader"])

    assert result.ok is False
    assert result.status == ToolStatus.PERMISSION_DENIED
    assert result.error is not None
    assert result.error.code == "PERMISSION_DENIED"


def test_gateway_tool_unavailable() -> None:
    registry = ToolRegistry()
    gateway = ToolInvocationGateway(registry)

    result = gateway.run("non_existent", {})

    assert result.ok is False
    assert result.status == ToolStatus.UNAVAILABLE


def test_gateway_rejects_arguments_that_do_not_match_schema() -> None:
    class FakeAdapter:
        def descriptors(self) -> tuple[ToolDescriptor, ...]:
            return (
                ToolDescriptor(
                    name="validated_tool",
                    description="tool with schema",
                    schema={
                        "type": "object",
                        "properties": {"message": {"type": "string"}},
                        "required": ["message"],
                    },
                ),
            )

        def invoke(self, invocation: ToolInvocation) -> ToolResult:
            return ToolResult(invocation_id=invocation.invocation_id, status=ToolStatus.SUCCEEDED, data="ok")

    registry = ToolRegistry()
    registry.register_adapter(FakeAdapter())
    gateway = ToolInvocationGateway(registry)

    result = gateway.run("validated_tool", {})

    assert result.ok is False
    assert result.status == ToolStatus.PROTOCOL_ERROR
    assert result.error is not None
    assert result.error.code == "INVALID_ARGUMENTS"


def test_gateway_capabilities_are_enforced() -> None:
    class FakeAdapter:
        def descriptors(self) -> tuple[ToolDescriptor, ...]:
            return (
                ToolDescriptor(
                    name="network_tool",
                    description="tool requiring network",
                    schema={},
                    capabilities=frozenset({"network"}),
                ),
            )

        def invoke(self, invocation: ToolInvocation) -> ToolResult:
            return ToolResult(invocation_id=invocation.invocation_id, status=ToolStatus.SUCCEEDED, data="ok")

    registry = ToolRegistry()
    registry.register_adapter(FakeAdapter())
    gateway = ToolInvocationGateway(registry)

    result = gateway.run("network_tool", {}, allowed_capabilities=frozenset({"read"}))

    assert result.ok is False
    assert result.status == ToolStatus.PERMISSION_DENIED
    assert result.error is not None
    assert result.error.code == "PERMISSION_DENIED"
