from __future__ import annotations

import ast
import asyncio
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from llm_agent.agent.engineering.model_safe import MODEL_SAFE_TOOL_DESCRIPTORS, ModelSafeResponse, ModelSafeStatus
from llm_agent.application import model_safe_mcp

ROOT = Path(__file__).resolve().parents[2]
EXPECTED_NAMES = (
    "engineering_list",
    "engineering_describe",
    "engineering_result",
    "engineering_inspect_completed",
    "engineering_health_offline",
)


def test_c18_application_tool_views_have_exact_canonical_descriptor_parity() -> None:
    source = (ROOT / "src/llm_agent/application/model_safe_mcp.py").read_text(encoding="utf-8")
    source_tree = ast.parse(source)
    app_literals = {node.value for node in ast.walk(source_tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)}
    assert set(EXPECTED_NAMES).isdisjoint(app_literals)
    assert not any(isinstance(node, ast.Name) and node.id == "ToolDescriptor" for node in ast.walk(source_tree))
    projected = model_safe_mcp.list_model_safe_tools()
    assert len(projected) == len(MODEL_SAFE_TOOL_DESCRIPTORS) == len(EXPECTED_NAMES)
    assert tuple(view.name for view in projected) == EXPECTED_NAMES
    for view, descriptor in zip(projected, MODEL_SAFE_TOOL_DESCRIPTORS, strict=True):
        assert view.name == descriptor.name
        assert view.description == descriptor.description
        assert view.input_schema == descriptor.schema
        assert view.cacheable == descriptor.cacheable
        assert view.idempotent == descriptor.idempotent
        assert view.result_data_schema == descriptor.result_data_schema


def test_c18_mcp_list_tools_and_call_tool_preserve_canonical_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    mcp_types = ModuleType("mcp_types")

    @dataclass
    class Tool:
        name: str
        description: str
        input_schema: Mapping[str, object]

    @dataclass
    class ListToolsResult:
        tools: list[Tool]

    @dataclass
    class TextContent:
        text: str

    @dataclass
    class CallToolResult:
        content: list[TextContent]
        structured_content: Mapping[str, object]
        is_error: bool

    @dataclass
    class CallToolRequestParams:
        name: str
        arguments: Mapping[str, object]

    mcp_types.Tool = Tool
    mcp_types.ListToolsResult = ListToolsResult
    mcp_types.TextContent = TextContent
    mcp_types.CallToolResult = CallToolResult
    mcp_types.CallToolRequestParams = CallToolRequestParams

    class Server:
        def __class_getitem__(cls, _item):
            return cls

        def __init__(self, *_args, on_list_tools, on_call_tool, **_kwargs):
            self.on_list_tools = on_list_tools
            self.on_call_tool = on_call_tool

    modules = {
        "mcp": ModuleType("mcp"),
        "mcp.server": ModuleType("mcp.server"),
        "mcp.server.lowlevel": ModuleType("mcp.server.lowlevel"),
        "mcp.server.lowlevel.server": ModuleType("mcp.server.lowlevel.server"),
        "mcp.server.stdio": ModuleType("mcp.server.stdio"),
    }
    for _module_name, module in modules.items():
        module.__path__ = []
    modules["mcp.server.lowlevel.server"].Server = Server
    modules["mcp.server.stdio"].stdio_server = lambda: None
    modules["mcp"].server = modules["mcp.server"]
    modules["mcp.server"].lowlevel = modules["mcp.server.lowlevel"]
    modules["mcp.server.lowlevel"].server = modules["mcp.server.lowlevel.server"]
    modules["mcp.server"].stdio = modules["mcp.server.stdio"]
    for name, module in {**modules, "mcp_types": mcp_types}.items():
        monkeypatch.setitem(sys.modules, name, module)
    server_module_name = "llm_agent.interfaces.mcp.engineering_server"
    monkeypatch.delitem(sys.modules, server_module_name, raising=False)
    from llm_agent.interfaces.mcp import engineering_server

    expected = ModelSafeResponse("engineering_list", ModelSafeStatus.AVAILABLE, {"operations": []})

    class Owner:
        def invoke(self, _name, _arguments, _execution):
            return expected

    context = model_safe_mcp.ModelSafeContext(Owner(), SimpleNamespace())
    server = engineering_server.create_server(SimpleNamespace(), context, SimpleNamespace())
    result = asyncio.run(server.on_list_tools(None, None))
    tools = result.tools
    assert len(tools) == len(MODEL_SAFE_TOOL_DESCRIPTORS)
    for wire, descriptor in zip(tools, MODEL_SAFE_TOOL_DESCRIPTORS, strict=True):
        schema = wire.input_schema
        assert wire.name == descriptor.name
        assert wire.description == descriptor.description
        assert schema == descriptor.schema
    call = asyncio.run(server.on_call_tool(None, CallToolRequestParams("engineering_list", {})))
    assert call.is_error is False
    assert call.structured_content == expected.to_dict()
    monkeypatch.setitem(sys.modules, server_module_name, engineering_server)


def test_c18_context_binds_one_canonical_owner_and_is_reused(monkeypatch) -> None:
    calls = []

    class Owner:
        def invoke(self, name, arguments, execution):
            return ModelSafeResponse(name, ModelSafeStatus.AVAILABLE, {"same_context": execution is context.execution})

    owner = Owner()
    monkeypatch.setattr(model_safe_mcp, "build_model_safe_engineering_service", lambda **_kwargs: calls.append(owner) or owner)
    workspace = SimpleNamespace(root=ROOT, workspace_id="workspace-c18")
    context = model_safe_mcp.build_model_safe_context(app_paths=SimpleNamespace(), workspace=workspace)
    first = model_safe_mcp.invoke_model_safe_tool(context, "engineering_list", {})
    second = model_safe_mcp.invoke_model_safe_tool(context, "engineering_list", {})
    assert calls == [owner]
    assert first.document["data"]["same_context"] is True
    assert second.document["data"]["same_context"] is True
    assert first.is_error is False and second.is_error is False


def test_c18_adapter_failure_is_safe_and_uses_canonical_response_shape() -> None:
    result = model_safe_mcp.model_safe_adapter_failure("engineering_list", RuntimeError("private details"))
    assert result.document == ModelSafeResponse(
        "engineering_list", ModelSafeStatus.DEGRADED, reason_code="MCP_ADAPTER_ERROR"
    ).to_dict()
    assert result.is_error is True
    assert "private details" not in str(result.document)


def test_c18_mcp_has_no_direct_agent_model_safe_references() -> None:
    paths = sorted((ROOT / "src/llm_agent/interfaces/mcp").glob("*.py"))
    trees = [ast.parse(path.read_text(encoding="utf-8")) for path in paths]
    forbidden = {
        "MODEL_SAFE_TOOL_DESCRIPTORS",
        "ModelSafeEngineering",
        "ModelSafeResponse",
        "ModelSafeStatus",
        "build_model_safe_engineering_service",
    }
    referenced = {node.id for tree in trees for node in ast.walk(tree) if isinstance(node, ast.Name)}
    referenced.update(node.attr for tree in trees for node in ast.walk(tree) if isinstance(node, ast.Attribute))
    assert forbidden.isdisjoint(referenced)
