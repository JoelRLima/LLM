"""Execution entry point for the tool invocation gateway."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Dict, cast

from agent.planning.effect_intent import operation_durable_effect
from agent.runtime.budget import BudgetExhausted
from agent.runtime.logging import logger
from agent.tools.contracts import (
    CancellationSafetyMode,
    ToolDescriptor,
    ToolInvocation,
    ToolResult,
    ToolStatus,
)
from agent.tools.invocation_commit import InvocationCommitMixin
from agent.tools.invocation_lifecycle import InvocationAttempt
from agent.tools.invocation_quiescence import (
    InvocationLivenessError,
    InvocationQuiescenceMixin,
)
from agent.tools.invocation_support import _set_cancel_event, _token_cancelled, denial
from agent.tools.mode_enforcement import required_capabilities_for_invocation


class InvocationExecutionMixin(InvocationQuiescenceMixin, InvocationCommitMixin):
    @staticmethod
    def _descriptor_may_mutate(
        descriptor: ToolDescriptor, args: Dict[str, Any]
    ) -> bool:
        if operation_durable_effect(descriptor.name, args, descriptor) is not None:
            return True
        # These descriptors expose both read-only and mutating actions. Their
        # broad capability ceiling is not evidence that this invocation owns
        # a durable effect.
        if descriptor.name.casefold() in {"code_task", "session_memory"}:
            return False
        capabilities = required_capabilities_for_invocation(descriptor, args)
        return bool(
            capabilities
            & frozenset({"write", "vcs_write", "package_install", "memory"})
        )

    def supports_cancellable_execution(
        self: Any,
        tool_name: str,
        args: Dict[str, Any],
    ) -> bool:
        """Whether this concrete invocation may safely receive a live token."""

        try:
            descriptor = self.registry.descriptor(tool_name)
        except KeyError:
            return True
        return not (
            self._descriptor_may_mutate(descriptor, args)
            and descriptor.cancellation_safety is CancellationSafetyMode.UNSUPPORTED
        )

    def _execute(
        self: Any,
        invocation: ToolInvocation,
        timeout_seconds: int | None,
        attempt: InvocationAttempt,
        cancellation_safety: CancellationSafetyMode,
    ) -> ToolResult:
        if _token_cancelled(invocation.cancellation_token):
            _set_cancel_event(invocation)
            attempt.mark_quiescent()
            return denial(
                invocation,
                ToolStatus.CANCELLED,
                "CANCELLED",
                "Execucao cancelada.",
            )
        if (
            timeout_seconds
            and timeout_seconds > 0
            or invocation.cancellation_token is not None
        ):
            return cast(
                ToolResult,
                self._invoke_with_timeout(
                    invocation,
                    timeout_seconds,
                    attempt,
                    cancellation_safety,
                ),
            )
        try:
            result = self.registry._invoke_from_gateway(invocation)
            return (
                replace(result, executed=True)
                if isinstance(result, ToolResult)
                else result
            )
        except BudgetExhausted:
            raise
        except Exception as exc:
            logger.warning("[GATEWAY] Adapter failed: %s", type(exc).__name__)
            return denial(
                invocation,
                ToolStatus.FAILED,
                "ADAPTER_FAILED",
                "Adapter invocation failed.",
                executed=True,
            )
        finally:
            attempt.mark_quiescent()


__all__ = ["InvocationExecutionMixin", "InvocationLivenessError"]
