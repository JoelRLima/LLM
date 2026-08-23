"""Execution, quiescence, and canonical commit behavior for the gateway."""

from __future__ import annotations

import concurrent.futures
import threading
import time
from dataclasses import replace
from typing import Any, Dict, cast

from agent.runtime.budget import BudgetExhausted
from agent.runtime.logging import logger
from agent.tools.contracts import ToolDescriptor, ToolError, ToolInvocation, ToolResult, ToolStatus
from agent.tools.invocation_lifecycle import InvocationAttempt
from agent.tools.invocation_support import (
    _set_cancel_event,
    _token_cancelled,
    denial,
    validate_result,
)
from agent.tools.mode_enforcement import required_capabilities_for_invocation


class InvocationExecutionMixin:
    @staticmethod
    def _descriptor_may_mutate(descriptor: ToolDescriptor, args: Dict[str, Any]) -> bool:
        capabilities = required_capabilities_for_invocation(descriptor, args)
        return bool(
            capabilities
            & frozenset({"write", "vcs_write", "network", "package_install", "process", "memory", "validate"})
        )

    def _execute(
        self: Any,
        invocation: ToolInvocation,
        timeout_seconds: int | None,
        attempt: InvocationAttempt,
    ) -> ToolResult:
        if _token_cancelled(invocation.cancellation_token):
            _set_cancel_event(invocation)
            attempt.mark_quiescent()
            return denial(invocation, ToolStatus.CANCELLED, "CANCELLED", "Execucao cancelada.")
        if (timeout_seconds and timeout_seconds > 0) or invocation.cancellation_token is not None:
            return cast(ToolResult, self._invoke_with_timeout(invocation, timeout_seconds, attempt))
        try:
            result = self.registry._invoke_from_gateway(invocation)
            return replace(result, executed=True) if isinstance(result, ToolResult) else result
        except BudgetExhausted:
            raise
        except Exception as exc:
            logger.warning("[GATEWAY] Adapter failed: %s", type(exc).__name__)
            return denial(invocation, ToolStatus.FAILED, "ADAPTER_FAILED", "Adapter invocation failed.", executed=True)
        finally:
            attempt.mark_quiescent()

    def _invoke_with_timeout(
        self: Any,
        invocation: ToolInvocation,
        timeout_seconds: int | None,
        attempt: InvocationAttempt,
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
                    attempt.request_cancel()
                    self._quiesce_after_effect_request(future, executor, attempt, mutating=attempt.mutating)
                    logger.info("[GATEWAY] Tool '%s' cancelada", invocation.tool_name)
                    return denial(invocation, ToolStatus.CANCELLED, "CANCELLED", "Execucao cancelada.", executed=started.is_set())
                remaining = 0.05 if deadline is None else min(0.05, deadline - time.monotonic())
                if remaining <= 0:
                    _set_cancel_event(invocation)
                    attempt.request_cancel(timed_out=True)
                    self._quiesce_after_effect_request(future, executor, attempt, mutating=attempt.mutating)
                    logger.warning("[GATEWAY] Tool '%s' excedeu o timeout de %ss", invocation.tool_name, timeout_seconds)
                    return denial(invocation, ToolStatus.TIMED_OUT, "TIMEOUT", "Execucao excedeu o limite.", executed=started.is_set())
                try:
                    result = future.result(timeout=remaining)
                    validated = validate_result(invocation, result)
                    if validated.error and validated.error.code in {"INVALID_RESULT", "INVOCATION_ID_MISMATCH"}:
                        validated = replace(validated, executed=True)
                    executor.shutdown(wait=True, cancel_futures=True)
                    attempt.mark_quiescent()
                    return validated
                except concurrent.futures.TimeoutError:
                    continue
        except BudgetExhausted:
            executor.shutdown(wait=True, cancel_futures=True)
            attempt.mark_quiescent()
            raise
        except Exception as exc:
            executor.shutdown(wait=True, cancel_futures=True)
            attempt.mark_quiescent()
            logger.warning("[GATEWAY] Adapter failed: %s", type(exc).__name__)
            return denial(invocation, ToolStatus.FAILED, "ADAPTER_FAILED", "Adapter invocation failed.", executed=started.is_set())

    @staticmethod
    def _quiesce_after_effect_request(
        future: concurrent.futures.Future[ToolResult],
        executor: concurrent.futures.ThreadPoolExecutor,
        attempt: InvocationAttempt,
        *,
        mutating: bool,
    ) -> None:
        """Close effect lifetime before publishing a mutating terminal result."""

        attempt.begin_quiescing()
        future.cancel()
        if not mutating:
            executor.shutdown(wait=False, cancel_futures=True)
            return
        try:
            future.result()
        except concurrent.futures.CancelledError:
            pass
        except Exception:
            pass
        finally:
            executor.shutdown(wait=True, cancel_futures=True)
            attempt.mark_quiescent()

    def _worker_finished(self: Any, attempt: InvocationAttempt) -> None:
        if attempt.worker_finished() and attempt.can_release():
            self._finish_invocation(attempt.invocation_id)

    def _emit_denial(
        self: Any,
        result: ToolResult,
        record_result: bool,
        tool_name: str,
        args: Dict[str, Any],
    ) -> None:
        self._emit(
            "tool_denied",
            {
                "invocation_id": result.invocation_id,
                "status": result.status.value,
                "reason": result.error.code if result.error else "DENIED",
            },
        )
        self._record(tool_name, args, result, record_result)

    def _complete_denial(
        self: Any,
        attempt: InvocationAttempt,
        result: ToolResult,
        record_result: bool,
        tool_name: str,
        args: Dict[str, Any],
    ) -> None:
        if not attempt.worker_pending:
            attempt.mark_quiescent()
        if not attempt.claim_terminal():
            return
        self._emit_denial(result, record_result, tool_name, args)
        if attempt.can_release():
            self._finish_invocation(attempt.invocation_id)

    def _complete_result(
        self: Any,
        attempt: InvocationAttempt,
        invocation: ToolInvocation,
        result: ToolResult,
        record_result: bool,
    ) -> ToolResult:
        if not attempt.claim_terminal():
            return result
        committed_result = result
        commit_error = self._record(invocation.tool_name, invocation.args, result, record_result)
        if commit_error is not None:
            with self._invocation_lock:
                self._canonical_commit_failed.add(invocation.invocation_id)
            committed_result = replace(
                result,
                status=ToolStatus.UNVERIFIED,
                error=ToolError(
                    "CANONICAL_COMMIT_FAILED",
                    "A execucao terminou, mas o commit do estado canonico falhou.",
                    {
                        "original_status": result.status.value,
                        "physical_effect_unknown": result.executed is not False,
                    },
                ),
                message="Execucao nao verificada: commit canonico falhou; efeitos fisicos podem ter ocorrido.",
            )
        self._emit(
            "tool_end",
            {
                "tool": invocation.tool_name,
                "invocation_id": invocation.invocation_id,
                "status": committed_result.status.value,
                "ok": committed_result.ok,
                "lifecycle": attempt.lifecycle.value,
            },
        )
        if attempt.can_release():
            self._finish_invocation(attempt.invocation_id)
        return committed_result

    def _emit(self: Any, event_type: str, data: Dict[str, Any]) -> None:
        if self.event_emitter is not None:
            try:
                self.event_emitter(event_type, data)
            except Exception as exc:
                logger.warning("[GATEWAY] Erro ao emitir evento '%s': %s", event_type, type(exc).__name__)

    def _record(
        self: Any,
        tool_name: str,
        args: Dict[str, Any],
        result: ToolResult,
        record_result: bool,
    ) -> Exception | None:
        if not record_result or self.state_recorder is None:
            return None
        try:
            self.state_recorder(tool_name, args, result)
        except Exception as exc:
            logger.warning("[GATEWAY] Erro ao registrar resultado no estado: %s", type(exc).__name__)
            if self.state_recorder_is_canonical:
                return exc
        return None

    def _is_canonical_commit_failed(self: Any, invocation_id: str) -> bool:
        with self._invocation_lock:
            return invocation_id in self._canonical_commit_failed


__all__ = ["InvocationExecutionMixin"]
