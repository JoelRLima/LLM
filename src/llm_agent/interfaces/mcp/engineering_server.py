"""Low-level, tools-only MCP projection over model-safe Engineering."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anyio
import mcp_types as types
from mcp.server.lowlevel.server import Server
from mcp.server.stdio import stdio_server

from llm_agent.application.context import AppPaths, WorkspaceContext
from llm_agent.application.home_operations import startup_home_operation
from llm_agent.application.model_safe_mcp import (
    ModelSafeContext,
    ModelSafeInvocationResult,
    ModelSafeToolView,
    build_model_safe_context,
    invoke_model_safe_tool,
    list_model_safe_tools,
    model_safe_adapter_failure,
)

MCP_ABANDON_ON_CANCEL = False


@dataclass(frozen=True, slots=True)
class MCPWorkspaceBinding:
    root: Path
    workspace_id: str

    @classmethod
    def from_path(cls, value: str | Path) -> "MCPWorkspaceBinding":
        context = WorkspaceContext.create(value)
        return cls(context.root, context.workspace_id)


def _default_adapter(binding: MCPWorkspaceBinding, paths: AppPaths) -> ModelSafeContext:
    return build_model_safe_context(app_paths=paths, workspace=binding)


def _tool(descriptor: ModelSafeToolView) -> types.Tool:
    return types.Tool(name=descriptor.name, description=descriptor.description, input_schema=descriptor.input_schema)


def _list_tools(context: ModelSafeContext) -> types.ListToolsResult:
    return types.ListToolsResult(tools=[_tool(item) for item in list_model_safe_tools(context)])


def _result(response: ModelSafeInvocationResult) -> types.CallToolResult:
    import json

    document = dict(response.document)
    text = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return types.CallToolResult(content=[types.TextContent(text=text)], structured_content=document, is_error=response.is_error)


def _safe_adapter_failure(operation: str, exc: BaseException) -> ModelSafeInvocationResult:
    """Keep unexpected adapter details on stderr and the wire generic."""

    print(f"MCP adapter failure: {type(exc).__name__}", file=sys.stderr)
    return model_safe_adapter_failure(operation, exc)


def create_server(binding: MCPWorkspaceBinding, adapter: ModelSafeContext, paths: AppPaths) -> Server[Any]:
    """Create a low-level Server with only the canonical tools handlers."""

    async def list_tools(_ctx: Any, _params: Any) -> types.ListToolsResult:
        return _list_tools(adapter)

    async def call_tool(_ctx: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
        try:
            response = await anyio.to_thread.run_sync(
                invoke_model_safe_tool,
                adapter,
                params.name,
                params.arguments or {},
                abandon_on_cancel=MCP_ABANDON_ON_CANCEL,
            )
            return _result(response)
        except BaseException as exc:
            return _result(_safe_adapter_failure(params.name, exc))

    return Server("local-llm-agent-engineering", version="20c", on_list_tools=list_tools, on_call_tool=call_tool)


async def _serve(binding: MCPWorkspaceBinding, paths: AppPaths, adapter: ModelSafeContext) -> None:
    server = create_server(binding, adapter, paths)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def serve_engineering_stdio(workspace: str | Path, *, home: str | Path | None = None) -> int:
    binding = MCPWorkspaceBinding.from_path(workspace)
    paths = AppPaths.discover(app_home=home)
    with startup_home_operation(paths):
        anyio.run(_serve, binding, paths, _default_adapter(binding, paths))
    return 0


__all__ = ["MCP_ABANDON_ON_CANCEL", "MCPWorkspaceBinding", "create_server", "serve_engineering_stdio"]
