"""Single controlled invocation gateway for every tool execution."""

from __future__ import annotations

import concurrent.futures
import threading
import time
from dataclasses import replace
from typing import Any, Callable, Dict, Optional

from agent.approval import ApprovalDecision, ApprovalPort, ApprovalRequest, RequireExplicitApproval
from agent.runtime.logging import logger
from agent.tools.authority import ApplicationAuthoritySnapshot, TaskAuthoritySnapshot
from agent.tools.contracts import (
    AuthorizationContext,
    ToolDescriptor,
    ToolInvocation,
    ToolInvocationRequest,
    ToolResult,
    ToolStatus,
)
from agent.tools.invocation_activity import InvocationActivityMixin
from agent.tools.invocation_support import (
    _InvocationAttempt,
    _set_cancel_event,
    _token_cancelled,
    check_authority,
    denial,
    prepare_request,
    validate_arguments,
    validate_binding,
    validate_result,
)
from agent.tools.mode_enforcement import check_capability_ceiling, required_capabilities_for_invocation
from agent.tools.tool_registry import ToolRegistry


class ToolInvocationGateway(InvocationActivityMixin):
    """Canonical boundary for request, authority, approval and adapter calls."""
    def __init__(
        self,
        registry: ToolRegistry,
        *,
        event_emitter: Optional[Callable[[str, Dict[str, Any]], None]] = None,
        state_recorder: Optional[Callable[[str, Dict[str, Any], ToolResult], None]] = None,
        approval_port: ApprovalPort | None = None,
        application_authority: ApplicationAuthoritySnapshot | None = None,
        task_authority: TaskAuthoritySnapshot | None = None,
    ) -> None:
        self.registry = registry
        self.event_emitter = event_emitter
        self.state_recorder = state_recorder
        self.approval_port = approval_port or RequireExplicitApproval()
        self.application_authority = application_authority
        self.task_authority = task_authority
        self._capability_ceiling: frozenset[str] | None = None
        self._ceiling_mode: str | None = None
        self._invocation_lock = threading.Lock()
        self._active_invocations: set[str] = set()
    def set_capability_ceiling(self, capabilities: frozenset[str] | None, *, mode: str | None = None) -> None:
        self._capability_ceiling, self._ceiling_mode = (None if capabilities is None else frozenset(capabilities), mode)
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
            self._emit_denial(request_error, record_result, tool_name if isinstance(tool_name, str) else "unknown", args or {})
            return request_error
        assert request is not None
        invocation = ToolInvocation(
            tool_name=request.tool_name,
            args=dict(request.arguments),
            invocation_id=request.invocation_id,
            task_id=request.task_id,
            workspace=(
                self.application_authority.workspace_id
                if self.application_authority is not None
                else (
                    self.registry.runtime_identity.workspace_id
                    if self.registry.runtime_identity is not None
                    else None
                )
            ),
            cancellation_token=cancellation_token,
            cancellation_event=threading.Event(),
        )
        attempt = _InvocationAttempt(invocation.invocation_id)
        if not self._begin_invocation(invocation.invocation_id):
            duplicate = denial(
                invocation,
                ToolStatus.PROTOCOL_ERROR,
                "DUPLICATE_INVOCATION_ID",
                "invocation_id ja possui uma tentativa ativa.",
            )
            self._emit_denial(duplicate, record_result, invocation.tool_name, invocation.args)
            return duplicate
        try:
            descriptor, authorization_error = self._authorize(
                invocation,
                active_skills,
                allowed_capabilities,
                authorization_context,
            )
            if authorization_error is not None:
                self._complete_denial(attempt, authorization_error, record_result, invocation.tool_name, invocation.args)
                return authorization_error
            assert descriptor is not None
            self._emit("tool_start", {"tool": invocation.tool_name, "invocation_id": invocation.invocation_id})
            logger.info("[GATEWAY] Invocando tool '%s' (id: %s)", invocation.tool_name, invocation.invocation_id)
            timeout = request.timeout_seconds if request.timeout_seconds is not None else descriptor.timeout_seconds
            result = self._execute(invocation, timeout, attempt)
            result = validate_result(invocation, result)
            if result.error and result.error.code in {"INVALID_RESULT", "INVOCATION_ID_MISMATCH"}:
                result = replace(result, executed=True)
            self._complete_result(attempt, invocation, result, record_result)
            return result
        except Exception:
            self._finish_invocation(invocation.invocation_id)
            raise
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
        required_capabilities = required_capabilities_for_invocation(descriptor, invocation.args)
        authority_error = check_authority(descriptor, self.application_authority, self.task_authority, invocation, required_capabilities)
        if authority_error is not None:
            return None, authority_error
        if authorization_context is not None and required_capabilities - authorization_context.effective_capabilities():
            return None, denial(invocation, ToolStatus.PERMISSION_DENIED, "PERMISSION_DENIED", "Capabilities nao concedidas.")
        capability_error = check_capability_ceiling(invocation, descriptor, self._capability_ceiling, self._ceiling_mode, allowed_capabilities)
        if capability_error is not None:
            return None, capability_error
        try:
            validate_arguments(descriptor, invocation.args)
        except (TypeError, ValueError, AttributeError) as exc:
            return None, denial(invocation, ToolStatus.PROTOCOL_ERROR, "INVALID_ARGUMENTS", str(exc))
        approval_result = self._check_effect_approval(invocation, descriptor)
        if approval_result is not None:
            return None, approval_result
        return descriptor, None
    def _check_effect_approval(self, invocation: ToolInvocation, descriptor: ToolDescriptor) -> ToolResult | None:
        effects = frozenset({"write", "vcs_write", "process", "network", "package_install", "validate"})
        requested = effects & required_capabilities_for_invocation(descriptor, invocation.args)
        if not requested:
            return None
        self._emit("approval_requested", {"tool": invocation.tool_name, "invocation_id": invocation.invocation_id})
        try:
            decision = self.approval_port.request(
                ApprovalRequest(
                    action=invocation.tool_name,
                    resource=str(invocation.args.get("file_path") or invocation.args.get("target") or "workspace"),
                    prompt=f"Autorizar efeitos {', '.join(sorted(requested))} para {invocation.tool_name}?",
                    metadata={"task_id": invocation.task_id, "invocation_id": invocation.invocation_id, "capabilities": sorted(requested)},
                )
            )
        except Exception as exc:
            logger.warning("[GATEWAY] Approval provider failed: %s", type(exc).__name__)
            return denial(invocation, ToolStatus.FAILED, "APPROVAL_FAILED", "Approval provider failed.")
        if decision is ApprovalDecision.APPROVED:
            self._emit("approval_approved", {"tool": invocation.tool_name, "invocation_id": invocation.invocation_id})
            return None
        status = ToolStatus.BLOCKED if decision is ApprovalDecision.REQUIRED else ToolStatus.PERMISSION_DENIED
        code = "APPROVAL_REQUIRED" if status is ToolStatus.BLOCKED else "APPROVAL_DENIED"
        return denial(invocation, status, code, "A aprovacao necessaria nao foi concedida.")
    def _execute(
        self,
        invocation: ToolInvocation,
        timeout_seconds: int | None,
        attempt: _InvocationAttempt,
    ) -> ToolResult:
        if _token_cancelled(invocation.cancellation_token):
            _set_cancel_event(invocation)
            return denial(invocation, ToolStatus.CANCELLED, "CANCELLED", "Execucao cancelada.")
        if (timeout_seconds and timeout_seconds > 0) or invocation.cancellation_token is not None:
            return self._invoke_with_timeout(invocation, timeout_seconds, attempt)
        try:
            result = self.registry._invoke_from_gateway(invocation)
            return replace(result, executed=True) if isinstance(result, ToolResult) else result
        except Exception as exc:
            logger.warning("[GATEWAY] Adapter failed: %s", type(exc).__name__)
            return denial(invocation, ToolStatus.FAILED, "ADAPTER_FAILED", "Adapter invocation failed.", executed=True)
    def _invoke_with_timeout(
        self,
        invocation: ToolInvocation,
        timeout_seconds: int | None,
        attempt: _InvocationAttempt,
    ) -> ToolResult:
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        started = threading.Event()
        def invoke_adapter() -> ToolResult:
            started.set()
            result = self.registry._invoke_from_gateway(invocation)
            return replace(result, executed=True) if isinstance(result, ToolResult) else result
        future = executor.submit(invoke_adapter)
        attempt.mark_worker_pending()
        future.add_done_callback(lambda _future: self._worker_finished(attempt))
        deadline = time.monotonic() + float(timeout_seconds) if timeout_seconds else None
        try:
            while True:
                if _token_cancelled(invocation.cancellation_token):
                    _set_cancel_event(invocation)
                    future.cancel()
                    executor.shutdown(wait=False, cancel_futures=True)
                    logger.info("[GATEWAY] Tool '%s' cancelada", invocation.tool_name)
                    return denial(invocation, ToolStatus.CANCELLED, "CANCELLED", "Execucao cancelada.", executed=started.is_set())
                remaining = 0.05 if deadline is None else min(0.05, deadline - time.monotonic())
                if remaining <= 0:
                    _set_cancel_event(invocation)
                    future.cancel()
                    executor.shutdown(wait=False, cancel_futures=True)
                    logger.warning("[GATEWAY] Tool '%s' excedeu o timeout de %ss", invocation.tool_name, timeout_seconds)
                    return denial(invocation, ToolStatus.TIMED_OUT, "TIMEOUT", "Execucao excedeu o limite.", executed=started.is_set())
                try:
                    result = future.result(timeout=remaining)
                    validated = validate_result(invocation, result)
                    if validated.error and validated.error.code in {"INVALID_RESULT", "INVOCATION_ID_MISMATCH"}:
                        validated = replace(validated, executed=True)
                    executor.shutdown(wait=True, cancel_futures=True)
                    return validated
                except concurrent.futures.TimeoutError:
                    continue
        except Exception as exc:
            executor.shutdown(wait=True, cancel_futures=True)
            self._worker_finished(attempt)
            logger.warning("[GATEWAY] Adapter failed: %s", type(exc).__name__)
            return denial(invocation, ToolStatus.FAILED, "ADAPTER_FAILED", "Adapter invocation failed.", executed=started.is_set())
    def _worker_finished(self, attempt: _InvocationAttempt) -> None:
        if attempt.worker_finished() and attempt.can_release():
            self._finish_invocation(attempt.invocation_id)
    def _emit_denial(self, result: ToolResult, record_result: bool, tool_name: str, args: Dict[str, Any]) -> None:
        self._emit("tool_denied", {"invocation_id": result.invocation_id, "status": result.status.value, "reason": result.error.code if result.error else "DENIED"})
        self._record(tool_name, args, result, record_result)
    def _complete_denial(
        self,
        attempt: _InvocationAttempt,
        result: ToolResult,
        record_result: bool,
        tool_name: str,
        args: Dict[str, Any],
    ) -> None:
        if not attempt.claim_terminal():
            return
        self._emit_denial(result, record_result, tool_name, args)
        if attempt.can_release():
            self._finish_invocation(attempt.invocation_id)
    def _complete_result(
        self,
        attempt: _InvocationAttempt,
        invocation: ToolInvocation,
        result: ToolResult,
        record_result: bool,
    ) -> None:
        if not attempt.claim_terminal():
            return
        self._emit("tool_end", {"tool": invocation.tool_name, "invocation_id": invocation.invocation_id, "status": result.status.value, "ok": result.ok})
        self._record(invocation.tool_name, invocation.args, result, record_result)
        if attempt.can_release():
            self._finish_invocation(attempt.invocation_id)
    def _emit(self, event_type: str, data: Dict[str, Any]) -> None:
        if self.event_emitter is not None:
            try:
                self.event_emitter(event_type, data)
            except Exception as exc:
                logger.warning("[GATEWAY] Erro ao emitir evento '%s': %s", event_type, type(exc).__name__)
    def _record(self, tool_name: str, args: Dict[str, Any], result: ToolResult, record_result: bool) -> None:
        if record_result and self.state_recorder is not None:
            try:
                self.state_recorder(tool_name, args, result)
            except Exception as exc:
                logger.warning("[GATEWAY] Erro ao registrar resultado no estado: %s", type(exc).__name__)
__all__ = ["ToolInvocationGateway"]
