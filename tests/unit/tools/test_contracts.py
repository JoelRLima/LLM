import pytest

from agent.tools.contracts import (
    ToolDescriptor,
    ToolError,
    ToolOriginKind,
    ToolResult,
    ToolStatus,
)


def test_tool_descriptor_preserves_historical_positional_fields() -> None:
    descriptor = ToolDescriptor(
        "x", "description", {}, frozenset(), 5, None, False, False, "EXECUTE", "adapter"
    )
    assert descriptor.adapter_id == "adapter"
    assert descriptor.origin_kind is ToolOriginKind.BUILTIN
    assert descriptor.extension_id is None


def test_legacy_tool_result_name_is_only_an_edge_compatibility_projection() -> None:
    from agent.contracts import LegacyToolResult
    from agent.contracts import ToolResult as LegacyName
    from agent.tools.contracts import ToolResult as CanonicalToolResult

    assert LegacyName is LegacyToolResult
    assert LegacyName is not CanonicalToolResult


def test_tool_descriptor_new_origin_fields_are_keyword_only() -> None:
    descriptor = ToolDescriptor(
        "x",
        "description",
        origin_kind=ToolOriginKind.EXTENSION,
        extension_id="canonical.extension",
        adapter_id="canonical.extension",
    )
    assert descriptor.origin_kind is ToolOriginKind.EXTENSION
    assert descriptor.extension_id == "canonical.extension"


@pytest.mark.parametrize("extension_id", ["", " ", "../escape", "UPPER", " space "])
def test_tool_descriptor_rejects_noncanonical_extension_id(extension_id: str) -> None:
    with pytest.raises(ValueError):
        ToolDescriptor(
            "x",
            "description",
            origin_kind=ToolOriginKind.EXTENSION,
            extension_id=extension_id,
            adapter_id="extension",
        )


def test_tool_descriptor_copies_capabilities_to_frozenset() -> None:
    capabilities = {"read"}
    descriptor = ToolDescriptor("x", "description", capabilities=capabilities)
    capabilities.add("write")
    assert descriptor.capabilities == frozenset({"read"})
    assert type(descriptor.capabilities) is frozenset


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


def test_tool_result_rich_legacy_projection_preserves_receipt_details() -> None:
    result = ToolResult(
        invocation_id="denied-1",
        status=ToolStatus.PERMISSION_DENIED,
        error=ToolError("TASK_AUTHORITY_MISSING", "Authority da tarefa ausente."),
        message="Authority da tarefa ausente.",
    )

    projected = result.to_legacy_dict(include_details=True)

    assert projected["error_code"] == "TASK_AUTHORITY_MISSING"
    assert projected["error_detail"] is None
    assert projected["artifacts"] == []


def test_tool_descriptor_defaults() -> None:
    desc = ToolDescriptor(name="test_tool", description="A test tool")
    assert desc.name == "test_tool"
    assert desc.cost == 5
    assert desc.cacheable is False
    assert desc.capabilities == frozenset()


@pytest.mark.parametrize(
    "result_data_schema",
    [
        {"type": ["string"]},
        {"type": "string", "properties": {}},
        {"type": "array", "items": []},
        {"type": "object", "properties": {"value": "string"}},
        {"type": "string", "future_value": "not allowed"},
    ],
)
def test_tool_descriptor_rejects_unsafe_result_data_schema(result_data_schema) -> None:
    with pytest.raises((TypeError, ValueError)):
        ToolDescriptor("shaped", "shaped", result_data_schema=result_data_schema)
