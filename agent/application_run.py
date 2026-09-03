"""Execution-boundary helpers for the standalone application."""

from __future__ import annotations

import io
from collections.abc import Callable
from contextlib import nullcontext, redirect_stdout
from typing import Any, cast

from agent.application_result import AgentRunResult
from agent.orchestration.task_lifecycle import TaskLifecycleMixin
from agent.planning.completion_observations import publish_outcome
from agent.planning.task_completion import mark_terminal_blocked
from agent.reporting.run_receipt import derive_error, derive_status, public_exception_message
from agent.runtime.budget import BudgetExhausted
from agent.tools.invocation_execution import InvocationLivenessError


def continuity_snapshot(application: Any) -> Any:
    from agent.continuity import TaskContinuityService

    return TaskContinuityService(application.workspace_paths).snapshot()


def refusal_result(
    application: Any,
    snapshot: Any,
    *,
    reason_code: str | None = None,
) -> AgentRunResult:
    document = snapshot.to_dict() if callable(getattr(snapshot, "to_dict", None)) else {}
    selected_reason = reason_code or str(document.get("reason_code") or "TASK_RESUME_UNAVAILABLE")
    message = (
        "A tarefa nao pode ser retomada com seguranca "
        f"({selected_reason}); o checkpoint foi preservado."
    )
    return AgentRunResult(
        status="unavailable",
        answer="",
        workspace=str(application.workspace.root),
        error=message,
        metadata={"reason_code": selected_reason, "continuity": document},
        receipt={
            "status": "unavailable",
            "success": False,
            "reason_code": selected_reason,
            "continuity": document,
        },
    )


def pause_after_interrupt(application: Any) -> str:
    state = getattr(application.orchestrator, "agent_state", None)
    objective = getattr(state, "objective", None)
    application.orchestrator.cancellation_token.cancel()
    if not isinstance(objective, str) or not objective.strip():
        return "Tarefa pausada por interrupcao antes de um objetivo ser iniciado."
    ensure_correlation = getattr(application.orchestrator, "_ensure_run_correlation", None)
    if callable(ensure_correlation):
        ensure_correlation()
    lifecycle = TaskLifecycleMixin()
    lifecycle.orchestrator = application.orchestrator
    return cast(str, lifecycle._handle_interrupt())


def run_locked(
    application: Any,
    objective: str | None,
    *,
    stream_callback: Callable[[str], None] | None = None,
    explicit_resume: bool = False,
) -> AgentRunResult:
    if bool(getattr(application, "_closed", False)):
        raise RuntimeError("A aplicação já foi encerrada.")
    if explicit_resume:
        snapshot = continuity_snapshot(application)
        if not bool(getattr(snapshot, "resumable", False)):
            return refusal_result(application, snapshot)
    captured = io.StringIO()
    application._task_attempted = True
    vars(application.orchestrator).update(
        {
            "_last_failure_code": None,
            "_last_failure_layer": None,
            "_resume_refusal_reason": None,
            "_run_id": None,
            "_run_correlation": None,
            "_task_start_time": 0.0,
            "_run_metric_recorded": False,
            "_metrics_start_line": None,
            "_canonical_run_snapshot": None,
        }
    )
    invocation = _invoke(application, objective, captured, stream_callback, explicit_resume)
    if isinstance(invocation, AgentRunResult):
        return invocation
    answer = invocation
    if explicit_resume:
        refusal_reason = getattr(application.orchestrator, "_resume_refusal_reason", None)
        if isinstance(refusal_reason, str) and refusal_reason:
            return refusal_result(
                application,
                continuity_snapshot(application),
                reason_code=refusal_reason,
            )
    status = derive_status(application.orchestrator)
    metadata: dict[str, Any] = {}
    legacy_output = captured.getvalue().strip()
    if legacy_output:
        metadata["legacy_output"] = legacy_output
    return cast(
        AgentRunResult,
        application._result(
            status,
            answer,
            error=derive_error(application.orchestrator, status),
            metadata=metadata,
        ),
    )


def _invoke(
    application: Any,
    objective: str | None,
    captured: io.StringIO,
    stream_callback: Callable[[str], None] | None,
    explicit_resume: bool,
) -> str | AgentRunResult:
    output_context = redirect_stdout(captured) if stream_callback is None else nullcontext()
    callback_args: dict[str, Any] = (
        {} if stream_callback is None else {"stream_callback": stream_callback}
    )
    if explicit_resume:
        callback_args["explicit_resume"] = True
    try:
        with output_context:
            return str(application.orchestrator.run(objective, **callback_args))
    except KeyboardInterrupt:
        answer = pause_after_interrupt(application)
        return cast(
            AgentRunResult,
            application._result("unavailable", answer, metadata={"continuity_status": "paused"}),
        )
    except BudgetExhausted as exc:
        vars(application.orchestrator)["_last_failure_code"] = BudgetExhausted.code
        vars(application.orchestrator)["_last_failure_layer"] = "budget"
        message = mark_terminal_blocked(
            application.orchestrator,
            reason_code=BudgetExhausted.code,
            message="A tarefa atingiu o limite de execucao e nao pode prosseguir agora.",
            status="block",
        )
        return cast(
            AgentRunResult,
            application._result("blocked", "", error=message or public_exception_message(exc)),
        )
    except InvocationLivenessError:
        raise
    except Exception as exc:
        vars(application.orchestrator)["_last_failure_code"] = getattr(exc, "code", None)
        vars(application.orchestrator)["_last_failure_layer"] = getattr(exc, "layer", None)
        fail_task = getattr(application.orchestrator, "fail_task", None)
        if callable(fail_task):
            fail_task()
        publish_outcome(application.orchestrator)
        return cast(
            AgentRunResult,
            application._result("failed", "", error=public_exception_message(exc)),
        )


__all__ = ["continuity_snapshot", "pause_after_interrupt", "refusal_result", "run_locked"]
