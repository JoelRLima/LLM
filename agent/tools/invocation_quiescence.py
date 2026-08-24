"""Bounded timeout/cancellation quiescence for gateway invocations."""

from __future__ import annotations

import concurrent.futures
import threading
import time
from collections.abc import Mapping
from dataclasses import replace
from typing import Any, cast

from agent.reporting.artifact_projection import project_artifact_evidence
from agent.runtime.budget import BudgetExhausted
from agent.runtime.logging import logger
from agent.tools.contracts import (
    CancellationSafetyMode,
    ToolError,
    ToolInvocation,
    ToolResult,
    ToolStatus,
)
from agent.tools.invocation_lifecycle import InvocationAttempt
from agent.tools.invocation_support import (
    _set_cancel_event,
    _token_cancelled,
    denial,
    validate_result,
)

CANCELLATION_GRACE_SECONDS = 2.0


class InvocationLivenessError(RuntimeError):
    """A supported mutating adapter violated its bounded quiescence contract."""


class InvocationQuiescenceMixin:
    def _invoke_with_timeout(
        self: Any,
        invocation: ToolInvocation,
        timeout_seconds: int | None,
        attempt: InvocationAttempt,
        cancellation_safety: CancellationSafetyMode,
    ) -> ToolResult:
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        started = threading.Event()

        def invoke_adapter() -> ToolResult:
            started.set()
            result = self.registry._invoke_from_gateway(invocation)
            return cast(ToolResult, replace(result, executed=True) if isinstance(result, ToolResult) else result)

        future = executor.submit(invoke_adapter)
        attempt.mark_worker_pending()
        future.add_done_callback(lambda _future: self._worker_finished(attempt))
        deadline = time.monotonic() + float(timeout_seconds) if timeout_seconds else None
        try:
            while True:
                requested = self._requested_terminal_result(
                    invocation,
                    future,
                    executor,
                    attempt,
                    cancellation_safety,
                    started,
                    deadline,
                    timeout_seconds,
                )
                if requested is not None:
                    return cast(ToolResult, requested)
                remaining = _remaining_wait(deadline)
                try:
                    result = future.result(timeout=remaining)
                    validated = validate_result(invocation, result)
                    if validated.error and validated.error.code in {
                        "INVALID_RESULT",
                        "INVOCATION_ID_MISMATCH",
                    }:
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
        except InvocationLivenessError:
            executor.shutdown(wait=False, cancel_futures=True)
            raise
        except Exception as exc:
            executor.shutdown(wait=True, cancel_futures=True)
            attempt.mark_quiescent()
            logger.warning("[GATEWAY] Adapter failed: %s", type(exc).__name__)
            return denial(
                invocation,
                ToolStatus.FAILED,
                "ADAPTER_FAILED",
                "Adapter invocation failed.",
                executed=started.is_set(),
            )

    def _requested_terminal_result(
        self: Any,
        invocation: ToolInvocation,
        future: concurrent.futures.Future[ToolResult],
        executor: concurrent.futures.ThreadPoolExecutor,
        attempt: InvocationAttempt,
        cancellation_safety: CancellationSafetyMode,
        started: threading.Event,
        deadline: float | None,
        timeout_seconds: int | None,
    ) -> ToolResult | None:
        if _token_cancelled(invocation.cancellation_token):
            _set_cancel_event(invocation)
            attempt.request_cancel()
            worker_result = self._quiesce_after_effect_request(
                future,
                executor,
                attempt,
                mutating=attempt.mutating,
                cancellation_safety=cancellation_safety,
            )
            logger.info("[GATEWAY] Tool '%s' cancelada", invocation.tool_name)
            return cast(ToolResult, self._merge_effect_request_result(
                invocation, ToolStatus.CANCELLED, "CANCELLED", "Execucao cancelada.",
                worker_result, started=started.is_set()))
        if deadline is None or deadline > time.monotonic():
            return None
        _set_cancel_event(invocation)
        attempt.request_cancel(timed_out=True)
        worker_result = self._quiesce_after_effect_request(
            future,
            executor,
            attempt,
            mutating=attempt.mutating,
            cancellation_safety=cancellation_safety,
        )
        logger.warning(
            "[GATEWAY] Tool '%s' excedeu o timeout de %ss",
            invocation.tool_name,
            timeout_seconds,
        )
        return cast(ToolResult, self._merge_effect_request_result(
            invocation, ToolStatus.TIMED_OUT, "TIMEOUT", "Execucao excedeu o limite.",
            worker_result, started=started.is_set()))

    @staticmethod
    def _quiesce_after_effect_request(
        future: concurrent.futures.Future[ToolResult],
        executor: concurrent.futures.ThreadPoolExecutor,
        attempt: InvocationAttempt,
        *,
        mutating: bool,
        cancellation_safety: CancellationSafetyMode,
    ) -> ToolResult | None:
        """Close effect lifetime before publishing a mutating terminal result."""

        attempt.begin_quiescing()
        future.cancel()
        if not mutating:
            executor.shutdown(wait=False, cancel_futures=True)
            return None
        worker_result: ToolResult | None = None
        quiesced = False
        try:
            _require_supported_safety(cancellation_safety, attempt)
            candidate = future.result(timeout=CANCELLATION_GRACE_SECONDS)
            quiesced = True
            if isinstance(candidate, ToolResult):
                worker_result = candidate
        except concurrent.futures.TimeoutError as exc:
            attempt.mark_liveness_failure()
            raise InvocationLivenessError(
                "mutating adapter exceeded bounded cancellation grace"
            ) from exc
        except concurrent.futures.CancelledError:
            quiesced = True
        except InvocationLivenessError:
            raise
        except Exception:
            quiesced = True
        finally:
            executor.shutdown(wait=quiesced, cancel_futures=True)
            if quiesced:
                attempt.mark_quiescent()
        return worker_result

    @staticmethod
    def _worker_effect_truth_is_proven(worker_result: ToolResult) -> bool:
        projected = worker_result.to_legacy_dict(include_details=True)
        detail = projected.get("error_detail")
        if isinstance(detail, Mapping) and detail.get("physical_effect_unknown") is True:
            return False
        data = projected.get("data")
        if isinstance(data, Mapping) and data.get("physical_effect_unknown") is True:
            return False
        artifact = project_artifact_evidence(projected)
        if artifact.mutation_occurred or artifact.persisted_mutation or artifact.rollback_occurred:
            return True
        return isinstance(data, Mapping) and any(
            key in data
            for key in (
                "mutation_occurred",
                "persisted_mutation",
                "surviving_mutation",
                "rollback_occurred",
                "final_state",
            )
        )

    def _merge_effect_request_result(
        self: Any,
        invocation: ToolInvocation,
        status: ToolStatus,
        code: str,
        message: str,
        worker_result: ToolResult | None,
        *,
        started: bool,
    ) -> ToolResult:
        validated_worker = _validated_worker(invocation, worker_result)
        physical_effect_unknown = bool(
            started
            and (
                validated_worker is None
                or not self._worker_effect_truth_is_proven(validated_worker)
            )
        )
        detail: dict[str, Any] = {"physical_effect_unknown": physical_effect_unknown}
        if validated_worker is not None:
            detail["worker_status"] = validated_worker.status.value
            if validated_worker.error is not None:
                detail["worker_error_code"] = validated_worker.error.code
        merged = denial(invocation, status, code, message, executed=started)
        if validated_worker is None:
            return replace(merged, error=ToolError(code, message, detail))
        return replace(
            merged,
            data=validated_worker.data,
            artifacts=validated_worker.artifacts,
            evidence_provenance=validated_worker.evidence_provenance,
            error=ToolError(code, message, detail),
        )

    def _worker_finished(self: Any, attempt: InvocationAttempt) -> None:
        if attempt.worker_finished() and attempt.can_release():
            self._finish_invocation(attempt.invocation_id)


def _remaining_wait(deadline: float | None) -> float:
    return 0.05 if deadline is None else max(0.0, min(0.05, deadline - time.monotonic()))


def _require_supported_safety(
    cancellation_safety: CancellationSafetyMode, attempt: InvocationAttempt
) -> None:
    if cancellation_safety in {
        CancellationSafetyMode.BOUNDED_COOPERATIVE,
        CancellationSafetyMode.PROCESS_KILLABLE,
    }:
        return
    attempt.mark_liveness_failure()
    raise InvocationLivenessError(
        "mutating cancellation reached execution without a supported safety mode"
    )


def _validated_worker(
    invocation: ToolInvocation, worker_result: ToolResult | None
) -> ToolResult | None:
    if worker_result is None:
        return None
    candidate = validate_result(invocation, worker_result)
    if candidate.error and candidate.error.code in {
        "INVALID_RESULT",
        "INVOCATION_ID_MISMATCH",
    }:
        return None
    return candidate


__all__ = ["InvocationLivenessError", "InvocationQuiescenceMixin"]
