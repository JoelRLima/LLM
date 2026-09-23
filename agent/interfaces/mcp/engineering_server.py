"""Low-level, tools-only MCP projection over model-safe Engineering."""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import anyio
import mcp_types as types
from mcp.server.lowlevel.server import Server
from mcp.server.stdio import stdio_server

from agent.engineering.contracts import EngineeringCaller, EngineeringExecutionContext, EngineeringWorkspaceContext
from agent.engineering.model_safe import (
    MODEL_SAFE_TOOL_DESCRIPTORS,
    ModelSafeEngineering,
    ModelSafeResponse,
    ModelSafeStatus,
    build_model_safe_engineering_service,
)
from agent.interfaces.mcp.projection import response_document
from agent.runtime.home_lifecycle import HomeLifecycleLease
from agent.runtime.paths import AppPaths
from agent.runtime.storage_bootstrap import StorageBootstrap
from agent.runtime.workspace_context import WorkspaceContext

MCP_ABANDON_ON_CANCEL = False


@dataclass(frozen=True, slots=True)
class MCPWorkspaceBinding:
    root: Path
    workspace_id: str

    @classmethod
    def from_path(cls, value: str | Path) -> "MCPWorkspaceBinding":
        context = WorkspaceContext.create(value)
        return cls(context.root, context.workspace_id)


def _context(binding: MCPWorkspaceBinding, paths: AppPaths) -> EngineeringExecutionContext:
    return EngineeringExecutionContext(
        caller=EngineeringCaller.MCP,
        app_paths=paths,
        workspace=EngineeringWorkspaceContext(binding.workspace_id, binding.root),
        source_repository=None,
        permissions=frozenset(),
        utc_now=lambda: datetime.now(timezone.utc),
        monotonic_now=time.monotonic,
        deadline_monotonic=None,
        cancellation_token=None,
    )


def _default_adapter(binding: MCPWorkspaceBinding, paths: AppPaths) -> ModelSafeEngineering:
    return build_model_safe_engineering_service(
        app_paths=paths,
        workspace=binding,
    )


def _tool(descriptor: Any) -> types.Tool:
    return types.Tool(name=descriptor.name, description=descriptor.description, input_schema=descriptor.schema)


def _result(response: ModelSafeResponse) -> types.CallToolResult:
    document = response_document(response)
    text = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return types.CallToolResult(content=[types.TextContent(text=text)], structured_content=document, is_error=response.status is not ModelSafeStatus.AVAILABLE)


def _safe_adapter_failure(operation: str, exc: BaseException) -> ModelSafeResponse:
    """Keep unexpected adapter details on stderr and the wire generic."""

    print(f"MCP adapter failure: {type(exc).__name__}", file=sys.stderr)
    return ModelSafeResponse(operation, ModelSafeStatus.DEGRADED, reason_code="MCP_ADAPTER_ERROR")


def create_server(binding: MCPWorkspaceBinding, adapter: ModelSafeEngineering, paths: AppPaths) -> Server[Any]:
    """Create a low-level Server with only the canonical tools handlers."""

    async def list_tools(_ctx: Any, _params: Any) -> types.ListToolsResult:
        return types.ListToolsResult(tools=[_tool(item) for item in MODEL_SAFE_TOOL_DESCRIPTORS])

    async def call_tool(_ctx: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
        try:
            response = await anyio.to_thread.run_sync(
                adapter.invoke,
                params.name,
                params.arguments or {},
                _context(binding, paths),
                abandon_on_cancel=MCP_ABANDON_ON_CANCEL,
            )
            return _result(response)
        except BaseException as exc:
            return _result(_safe_adapter_failure(params.name, exc))

    return Server("local-llm-agent-engineering", version="20c", on_list_tools=list_tools, on_call_tool=call_tool)


async def _serve(binding: MCPWorkspaceBinding, paths: AppPaths, adapter: ModelSafeEngineering) -> None:
    server = create_server(binding, adapter, paths)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def serve_engineering_stdio(workspace: str | Path, *, home: str | Path | None = None) -> int:
    binding = MCPWorkspaceBinding.from_path(workspace)
    paths = AppPaths.discover(app_home=home)
    lease = HomeLifecycleLease.begin_startup(paths.home_dir)
    try:
        StorageBootstrap().prepare(paths)
        lease.activate()
        anyio.run(_serve, binding, paths, _default_adapter(binding, paths))
    finally:
        lease.close()
    return 0


__all__ = ["MCP_ABANDON_ON_CANCEL", "MCPWorkspaceBinding", "create_server", "serve_engineering_stdio"]
