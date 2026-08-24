from __future__ import annotations

import threading
from dataclasses import replace
from typing import Any, Callable, Dict, Optional

from agent.approval import ApprovalPort, RequireExplicitApproval
from agent.runtime.budget import BudgetExhausted, TaskBudgetLedger
from agent.tools.approval_execution import check_effect_approval
from agent.tools.authority import ApplicationAuthoritySnapshot, TaskAuthoritySnapshot
from agent.tools.contracts import (
    AuthorizationContext,
    CancellationSafetyMode,
    ToolDescriptor,
    ToolInvocation,
    ToolInvocationRequest,
    ToolResult,
    ToolStatus,
)
from agent.tools.invocation_activity import InvocationActivityMixin
from agent.tools.invocation_execution import InvocationExecutionMixin
from agent.tools.invocation_lifecycle import InvocationAttempt
from agent.tools.invocation_support import (
    check_authority,
    denial,
    prepare_request,
    validate_arguments,
    validate_binding,
    validate_result,
)
from agent.tools.mode_enforcement import check_capability_ceiling, required_capabilities_for_invocation
from agent.tools.tool_registry import ToolRegistry


class ToolInvocationGateway(InvocationExecutionMixin, InvocationActivityMixin):
    def __init__(
        self,
        registry: ToolRegistry,
        *,
        event_emitter: Optional[Callable[[str, Dict[str, Any]], None]] = None,
        state_recorder: Optional[Callable[[str, Dict[str, Any], ToolResult], None]] = None,
        incident_recorder: Optional[Callable[[Dict[str, Any]], None]] = None,
        state_recorder_is_canonical: bool = True,
        approval_port: ApprovalPort | None = None,
        application_authority: ApplicationAuthoritySnapshot | None = None,
        task_authority: TaskAuthoritySnapshot | None = None,
        budget_ledger: TaskBudgetLedger | None = None,
    ) -> None:
        self.registry = registry
        self.event_emitter = event_emitter
        self.state_recorder = state_recorder
        self.incident_recorder = incident_recorder
        self.state_recorder_is_canonical = bool(state_recorder_is_canonical)
        self.approval_port = approval_port or RequireExplicitApproval()
        self.application_authority = application_authority
        self.task_authority = task_authority
        self.budget_ledger = budget_ledger
        self._capability_ceiling: frozenset[str] | None = None
        self._ceiling_mode: str | None = None
        self._invocation_lock = threading.Lock()
        self._active_invocations: set[str] = set()
        self._active_invocation_meta: dict[str, dict[str, Any]] = {}
        self._activity_condition = threading.Condition(self._invocation_lock)
        self._canonical_commit_failed: set[str] = set()

    def set_budget_ledger(self, budget_ledger: TaskBudgetLedger) -> None:
        self.budget_ledger = budget_ledger

    def set_incident_recorder(self, incident_recorder: Callable[[Dict[str, Any]], None] | None) -> None:
        self.incident_recorder = incident_recorder

    def set_capability_ceiling(
        self,
        capabilities: frozenset[str] | None,
        *,
        mode: str | None = None,
    ) -> None:
        self._capability_ceiling = None if capabilities is None else frozenset(capabilities)
        self._ceiling_mode = mode

    def run(
        self,
        tool_name: str | ToolInvocationRequest,
        args: Dict[str, Any] | None = None,
        *,
        active_skills: Optional[list[str]] = None,
        allowed_capabilities: Optional[frozenset[str]] = None,
        timeout_seconds: Optional[int] = None,
        record_result: bool = True,
        task_id: Optional[str] = None,
        authorization_context: AuthorizationContext | None = None,
        cancellation_token: Any | None = None,
    ) -> ToolResult:
        """Validate and execute one request; historical calls remain wrappers."""
        request, request_error = prepare_request(tool_name, args, timeout_seconds, task_id)
        if request_error is not None:
            self._emit_denial(
                request_error,
                record_result,
                tool_name if isinstance(tool_name, str) else "unknown",
                args or {},
            )
            return request_error
        assert request is not None
        invocation = ToolInvocation(
            tool_name=request.tool_name,
            args=dict(request.arguments),
            invocation_id=request.invocation_id,
            task_id=request.task_id,
            workspace=self._workspace_id(),
            cancellation_token=cancellation_token,
            cancellation_event=threading.Event(),
        )
        attempt = InvocationAttempt(invocation.invocation_id)
        if not self._begin_invocation(
            invocation.invocation_id,
            task_id=invocation.task_id,
            cancellation_event=invocation.cancellation_event,
        ):
            return self._duplicate_or_retry_block(invocation, record_result)
        try:
            if self.budget_ledger is not None:
                try:
                    self.budget_ledger.reserve_tool_call()
                except BudgetExhausted as exc:
                    exhausted = denial(
                        invocation,
                        ToolStatus.BLOCKED,
                        BudgetExhausted.code,
                        str(exc),
                        executed=False,
                    )
                    self._complete_denial(attempt, exhausted, False, invocation.tool_name, invocation.args)
                    return exhausted
            descriptor, authorization_error = self._authorize(
                invocation,
                active_skills,
                allowed_capabilities,
                authorization_context,
            )
            if authorization_error is not None:
                self._complete_denial(
                    attempt,
                    authorization_error,
                    record_result,
                    invocation.tool_name,
                    invocation.args,
                )
                return authorization_error
            assert descriptor is not None
            mutating = self._descriptor_may_mutate(descriptor, invocation.args)
            attempt.set_mutating(mutating)
            self._set_invocation_mutating(invocation.invocation_id, mutating)
            if (
                mutating
                and self._requests_cancellable_execution(
                    request,
                    descriptor,
                    cancellation_token,
                )
                and descriptor.cancellation_safety is CancellationSafetyMode.UNSUPPORTED
            ):
                unsupported = denial(
                    invocation,
                    ToolStatus.BLOCKED,
                    "MUTATING_CANCELLATION_UNSUPPORTED",
                    "A ferramenta mutante nao declara um modo seguro de timeout/cancelamento.",
                    executed=False,
                )
                self._complete_denial(
                    attempt,
                    unsupported,
                    record_result,
                    invocation.tool_name,
                    invocation.args,
                )
                return unsupported
            attempt.mark_running()
            self._emit(
                "tool_start",
                {
                    "tool": invocation.tool_name,
                    "invocation_id": invocation.invocation_id,
                    "lifecycle": attempt.lifecycle.value,
                },
            )
            timeout = request.timeout_seconds if request.timeout_seconds is not None else descriptor.timeout_seconds
            result = validate_result(
                invocation,
                self._execute(
                    invocation,
                    timeout,
                    attempt,
                    descriptor.cancellation_safety,
                ),
            )
            if result.error and result.error.code in {"INVALID_RESULT", "INVOCATION_ID_MISMATCH"}:
                result = replace(result, executed=True)
            return self._complete_result(attempt, invocation, result, record_result)
        except Exception:
            if not attempt.has_worker_pending():
                self._finish_invocation(invocation.invocation_id)
            raise

    @staticmethod
    def _requests_cancellable_execution(
        request: ToolInvocationRequest,
        descriptor: ToolDescriptor,
        cancellation_token: Any | None,
    ) -> bool:
        return bool(
            request.timeout_seconds is not None
            or descriptor.timeout_seconds is not None
            or cancellation_token is not None
        )

    def _workspace_id(self) -> str | None:
        if self.application_authority is not None:
            workspace_id = self.application_authority.workspace_id
            return str(workspace_id) if workspace_id is not None else None
        if self.registry.runtime_identity is not None:
            workspace_id = self.registry.runtime_identity.workspace_id
            return str(workspace_id) if workspace_id is not None else None
        return None

    def _duplicate_or_retry_block(self, invocation: ToolInvocation, record_result: bool) -> ToolResult:
        if self._is_canonical_commit_failed(invocation.invocation_id):
            result = denial(
                invocation,
                ToolStatus.UNVERIFIED,
                "CANONICAL_COMMIT_RETRY_BLOCKED",
                "A tentativa anterior nao confirmou o commit canonico; a repeticao automatica foi bloqueada.",
            )
            self._emit_denial(result, False, invocation.tool_name, invocation.args)
            return result
        result = denial(
            invocation,
            ToolStatus.PROTOCOL_ERROR,
            "DUPLICATE_INVOCATION_ID",
            "invocation_id ja possui uma tentativa ativa.",
        )
        self._emit_denial(result, record_result, invocation.tool_name, invocation.args)
        return result

    def invoke(self, request: ToolInvocationRequest, **kwargs: Any) -> ToolResult:
        """Explicit name for callers that already own a canonical request."""
        return self.run(request, **kwargs)

    def _authorize(
        self,
        invocation: ToolInvocation,
        active_skills: list[str] | None,
        allowed_capabilities: frozenset[str] | None,
        authorization_context: AuthorizationContext | None,
    ) -> tuple[ToolDescriptor | None, ToolResult | None]:
        try:
            descriptor = self.registry.descriptor(invocation.tool_name)
        except KeyError:
            return None, denial(invocation, ToolStatus.UNAVAILABLE, "TOOL_NOT_FOUND", "Ferramenta nao registrada.")
        binding_error = validate_binding(self.registry, self.application_authority, descriptor, invocation)
        if binding_error is not None:
            return None, binding_error
        if active_skills is not None and invocation.tool_name not in active_skills:
            return None, denial(invocation, ToolStatus.PERMISSION_DENIED, "PERMISSION_DENIED", "Tool bloqueada pela visibilidade de planning.")
        required = required_capabilities_for_invocation(descriptor, invocation.args)
        authority_error = check_authority(descriptor, self.application_authority, self.task_authority, invocation, required)
        if authority_error is not None:
            return None, authority_error
        if authorization_context is not None and required - authorization_context.effective_capabilities():
            return None, denial(invocation, ToolStatus.PERMISSION_DENIED, "PERMISSION_DENIED", "Capabilities nao concedidas.")
        capability_error = check_capability_ceiling(
            invocation,
            descriptor,
            self._capability_ceiling,
            self._ceiling_mode,
            allowed_capabilities,
        )
        if capability_error is not None:
            return None, capability_error
        try:
            validate_arguments(descriptor, invocation.args)
        except (TypeError, ValueError, AttributeError) as exc:
            return None, denial(invocation, ToolStatus.PROTOCOL_ERROR, "INVALID_ARGUMENTS", str(exc))
        approval_result = self._check_effect_approval(invocation, descriptor)
        return (descriptor, None) if approval_result is None else (None, approval_result)

    def _check_effect_approval(
        self,
        invocation: ToolInvocation,
        descriptor: ToolDescriptor,
    ) -> ToolResult | None:
        return check_effect_approval(self, invocation, descriptor)


__all__ = ["ToolInvocationGateway"]
