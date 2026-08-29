"""Observability bindings for the canonical tool invocation gateway."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Dict

from agent.runtime.budget import BudgetExhausted
from agent.tools.contracts import ToolInvocation, ToolInvocationRequest, ToolResult, ToolStatus
from agent.tools.invocation_lifecycle import InvocationAttempt
from agent.tools.invocation_support import denial


class InvocationObservabilityMixin:
    event_dispatcher: Any
    correlation_provider: Callable[[], Any] | None
    event_fields_provider: Callable[[], Any] | None
    incident_recorder: Callable[[Dict[str, Any]], None] | None
    budget_ledger: Any

    if TYPE_CHECKING:
        def _complete_denial(
            self,
            attempt: InvocationAttempt,
            result: ToolResult,
            record_result: bool,
            tool_name: str,
            args: Dict[str, Any],
        ) -> None: ...

    def set_event_dispatcher(
        self,
        event_dispatcher: Any,
        correlation_provider: Callable[[], Any] | None = None,
        event_fields_provider: Callable[[], Any] | None = None,
    ) -> None:
        self.event_dispatcher = event_dispatcher
        if correlation_provider is not None:
            self.correlation_provider = correlation_provider
        if event_fields_provider is not None:
            self.event_fields_provider = event_fields_provider

    def set_incident_recorder(self, incident_recorder: Callable[[Dict[str, Any]], None] | None) -> None:
        self.incident_recorder = incident_recorder

    def _preflight_denial(
        self,
        request: ToolInvocationRequest,
        invocation: ToolInvocation,
        attempt: InvocationAttempt,
        record_result: bool,
    ) -> ToolResult | None:
        message = self._runtime_lineage_error(request)
        if message is not None:
            result = denial(
                invocation, ToolStatus.PROTOCOL_ERROR, "RUNTIME_LINEAGE_INVALID", message,
                executed=False,
            )
            self._complete_denial(
                attempt, result, record_result, invocation.tool_name, invocation.args
            )
            return result
        if self.budget_ledger is None:
            return None
        try:
            self.budget_ledger.reserve_tool_call()
        except BudgetExhausted as exc:
            result = denial(
                invocation, ToolStatus.BLOCKED, BudgetExhausted.code, str(exc), executed=False
            )
            self._complete_denial(attempt, result, False, invocation.tool_name, invocation.args)
            return result
        return None

    def _runtime_lineage_error(self, request: ToolInvocationRequest) -> str | None:
        supplied = any(
            value is not None
            for value in (
                request.task_id,
                request.run_id,
                request.root_task_id,
                request.parent_task_id,
                request.node_id,
            )
        )
        if not supplied:
            return None
        provider = self.correlation_provider
        correlation = provider() if callable(provider) else None
        if correlation is None:
            return "correlated tool request requires the active runtime correlation owner"
        if request.run_id is None or request.root_task_id is None or request.task_id is None:
            return "runtime lineage must include run_id, root_task_id and task_id"
        if request.run_id != getattr(correlation, "run_id", None) or request.root_task_id != getattr(
            correlation, "root_task_id", None
        ):
            return "tool request lineage does not belong to the active runtime attempt"
        if request.task_id != request.root_task_id and (
            request.parent_task_id is None or request.node_id is None
        ):
            return "child runtime lineage requires parent_task_id and node_id"
        return None


__all__ = ["InvocationObservabilityMixin"]
