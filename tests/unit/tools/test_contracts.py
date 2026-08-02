from agent.tools.contracts import (
    ToolDescriptor,
    ToolError,
    ToolResult,
    ToolStatus,
)


def test_tool_status_values() -> None:
    assert ToolStatus.SUCCEEDED.value == "succeeded"
    assert ToolStatus.FAILED.value == "failed"
    assert ToolStatus.TIMED_OUT.value == "timed_out"
    assert ToolStatus.PERMISSION_DENIED.value == "permission_denied"
    assert ToolStatus.BLOCKED.value == "blocked"
    assert ToolStatus.UNVERIFIED.value == "unverified"


def test_tool_result_ok_property() -> None:
    succeeded = ToolResult(invocation_id="1", status=ToolStatus.SUCCEEDED, data="ok")
    failed = ToolResult(
        invocation_id="2",
        status=ToolStatus.FAILED,
        error=ToolError("ERR", "failed"),
    )
    assert succeeded.ok is True
    assert failed.ok is False


def test_non_success_statuses_are_terminal_in_legacy_shape() -> None:
    for status in (ToolStatus.BLOCKED, ToolStatus.CANCELLED, ToolStatus.UNVERIFIED):
        result = ToolResult(invocation_id="1", status=status)
        legacy = result.to_legacy_dict()
        assert legacy["ok"] is False
        assert legacy["done"] is True
        assert legacy["status"] == status.value


def test_tool_result_to_legacy_dict() -> None:
    result = ToolResult(
        invocation_id="123",
        status=ToolStatus.SUCCEEDED,
        data={"output": "hello"},
        message="Success",
    )
    legacy = result.to_legacy_dict()
    assert legacy == {
        "invocation_id": "123",
        "ok": True,
        "done": True,
        "status": "succeeded",
        "data": {"output": "hello"},
        "error": None,
        "message": "Success",
    }


def test_tool_descriptor_defaults() -> None:
    desc = ToolDescriptor(name="test_tool", description="A test tool")
    assert desc.name == "test_tool"
    assert desc.cost == 5
    assert desc.cacheable is False
    assert desc.capabilities == frozenset()
