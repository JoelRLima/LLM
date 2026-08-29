"""Tool-request binding for an exact task execution context lineage."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent.tools.invocation_request import ToolInvocationRequest


class CorrelatedToolRequestMixin:
    def _runtime_correlation(self) -> Any:
        raise NotImplementedError

    def tool_invocation_request(
        self,
        invocation_id: str,
        tool_name: str,
        arguments: Mapping[str, Any] | None = None,
        *,
        timeout_seconds: int | None = None,
    ) -> ToolInvocationRequest:
        correlation = self._runtime_correlation()
        return ToolInvocationRequest(
            invocation_id,
            tool_name,
            arguments or {},
            timeout_seconds,
            task_id=correlation.task_id,
            run_id=correlation.run_id,
            root_task_id=correlation.root_task_id,
            parent_task_id=correlation.parent_task_id,
            node_id=correlation.node_id,
        )


__all__ = ["CorrelatedToolRequestMixin"]
