"""Finite Application projection for the MCP ModelSafe Engineering interface."""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from llm_agent.agent.engineering.contracts import (
    EngineeringCaller,
    EngineeringExecutionContext,
    EngineeringWorkspaceContext,
)
from llm_agent.agent.engineering.model_safe import (
    MODEL_SAFE_TOOL_DESCRIPTORS,
    ModelSafeEngineering,
    ModelSafeResponse,
    ModelSafeStatus,
    build_model_safe_engineering_service,
)


@dataclass(frozen=True, slots=True)
class ModelSafeToolView:
    name: str
    description: str
    input_schema: Mapping[str, Any]
    cacheable: bool
    idempotent: bool
    result_data_schema: Mapping[str, Any] | None


@dataclass(frozen=True, slots=True)
class ModelSafeInvocationResult:
    document: Mapping[str, Any]
    is_error: bool


@dataclass(frozen=True, slots=True)
class ModelSafeContext:
    engineering: ModelSafeEngineering
    execution: EngineeringExecutionContext


def build_model_safe_context(*, app_paths: Any, workspace: Any) -> ModelSafeContext:
    owner = build_model_safe_engineering_service(app_paths=app_paths, workspace=workspace)
    bound_workspace = EngineeringWorkspaceContext(workspace.workspace_id, workspace.root)
    execution = EngineeringExecutionContext(
        caller=EngineeringCaller.MCP,
        app_paths=app_paths,
        workspace=bound_workspace,
        source_repository=None,
        permissions=frozenset(),
        utc_now=lambda: datetime.now(timezone.utc),
        monotonic_now=time.monotonic,
        deadline_monotonic=None,
        cancellation_token=None,
    )
    return ModelSafeContext(owner, execution)


def list_model_safe_tools(_context: ModelSafeContext | None = None) -> tuple[ModelSafeToolView, ...]:
    return tuple(
        ModelSafeToolView(
            name=item.name,
            description=item.description,
            input_schema=item.schema,
            cacheable=item.cacheable,
            idempotent=item.idempotent,
            result_data_schema=item.result_data_schema,
        )
        for item in MODEL_SAFE_TOOL_DESCRIPTORS
    )


def invoke_model_safe_tool(context: ModelSafeContext, name: str, arguments: Mapping[str, Any]) -> ModelSafeInvocationResult:
    response = context.engineering.invoke(name, arguments, context.execution)
    return ModelSafeInvocationResult(response.to_dict(), response.status is not ModelSafeStatus.AVAILABLE)


def model_safe_adapter_failure(name: str, exc: BaseException) -> ModelSafeInvocationResult:
    del exc
    response = ModelSafeResponse(name, ModelSafeStatus.DEGRADED, reason_code="MCP_ADAPTER_ERROR")
    return ModelSafeInvocationResult(response.to_dict(), response.status is not ModelSafeStatus.AVAILABLE)


__all__ = [
    "ModelSafeContext",
    "ModelSafeInvocationResult",
    "ModelSafeToolView",
    "build_model_safe_context",
    "invoke_model_safe_tool",
    "list_model_safe_tools",
    "model_safe_adapter_failure",
]
