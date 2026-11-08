"""Canonical-boundary adapters executed by the single interactive worker."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Callable, Literal

from llm_agent.application.code_commands import execute_code_command
from llm_agent.application.interactive_worker import run_interactive_worker
from llm_agent.application.task_execution import (
    bind_submission_cancellation,
    read_execution_capabilities,
)
from llm_agent.application.task_execution import (
    execute_submission as execute_task_submission,
)
from llm_agent.interactive.session_controls import bind_attention
from llm_agent.interactive.worker_operations import execute_code, execute_search
from llm_agent.interfaces.cli.controller import SubmissionEnvelope
from llm_agent.interfaces.cli.worker_stream import BoundedTextBuffer, BoundedWorkerStream


@dataclass(frozen=True)
class InteractiveWorkerResult:
    """Worker settlement plus compatibility metadata for the UI stream."""

    result: Any
    stdout: str = ""
    stderr: str = ""
    assistant_streamed: bool = False
    assistant_stream_truncated: bool = False


def _bind_application(ctx: Any, cancel_event: Any, register: Callable[[Callable[[], None]], None]) -> Callable[[], None]:
    cleanup: Callable[[], None] = bind_submission_cancellation(ctx.task_execution, cancel_event, register)
    return cleanup


def _execute_search(ctx: Any, envelope: SubmissionEnvelope, cancel_event: Any) -> Any:
    return execute_search(ctx, envelope, cancel_event)


def _execute_code(
    ctx: Any,
    envelope: SubmissionEnvelope,
    cancel_event: Any,
    register: Callable[[Callable[[], None]], None],
) -> Any:
    return execute_code(ctx, envelope, cancel_event, register,
                        executor=lambda text, **options: execute_code_command(text, **options),
                        capabilities=read_execution_capabilities)


def _bind_attention(
    ctx: Any,
    envelope: SubmissionEnvelope,
    register: Callable[[Callable[[], None]], None],
) -> Any:
    return bind_attention(
        SimpleNamespace(approval_broker=getattr(ctx, "approval_broker", None),
                        view=getattr(ctx, "view_model", None), controller=getattr(ctx, "controller", None)),
        envelope, register,
    )


def _execute_application_submission(
    ctx: Any,
    envelope: SubmissionEnvelope,
    cancel_event: Any,
    register: Callable[[Callable[[], None]], None],
    output: Callable[[str], None],
) -> tuple[Callable[[], None], Any]:
    cleanup = _bind_application(ctx, cancel_event, register)
    entry: Literal["retry", "agent", "natural"] = "retry" if envelope.command_id == "run.retry" else ("agent" if envelope.command_id == "interaction.agent_submit" else "natural")
    result = execute_task_submission(ctx.task_execution, envelope.payload, entry=entry,
                                visible_text=envelope.visible_text, stream_callback=output)
    return cleanup, result


def _dispatch_interactive_submission(
    ctx: Any,
    envelope: SubmissionEnvelope,
    cancel_event: Any,
    register: Callable[[Callable[[], None]], None],
    output: Callable[[str], None],
) -> tuple[Callable[[], None], Any]:
    """Run the route selected by the submitted command metadata."""
    if envelope.command_id == "query.web_search":
        return (lambda: None), _execute_search(ctx, envelope, cancel_event)
    if envelope.command_id == "interaction.code_submit":
        return (lambda: None), _execute_code(ctx, envelope, cancel_event, register)
    return _execute_application_submission(ctx, envelope, cancel_event, register, output)


def execute_submission(
    ctx: Any,
    envelope: SubmissionEnvelope,
    cancel_event: Any,
    register: Callable[[Callable[[], None]], None],
) -> Any:
    """Dispatch only to the canonical owner selected by manifest metadata."""
    broker = _bind_attention(ctx, envelope, register)
    controller = getattr(ctx, "controller", None)
    stream_channel = getattr(controller, "stream_channel", None)
    if not isinstance(stream_channel, BoundedWorkerStream):
        stream_channel = None

    def cleanup() -> None:
        return None
    compatibility_capture = BoundedTextBuffer()
    assistant_streamed = False

    def publish_assistant(text: str) -> None:
        nonlocal assistant_streamed
        if stream_channel is None:
            before = compatibility_capture.getvalue()
            compatibility_capture.append(text)
            assistant_streamed = assistant_streamed or compatibility_capture.getvalue() != before
        else:
            # A rejected publish must not suppress the canonical terminal answer.
            published = stream_channel.publish(
                envelope.run_generation,
                "assistant",
                text,
            )
            assistant_streamed = assistant_streamed or published

    def publish_diagnostic(text: str) -> None:
        if stream_channel is None:
            compatibility_capture.append(text)
        else:
            stream_channel.publish(envelope.run_generation, "diagnostic", text)

    result: Any = None
    try:
        def operation() -> None:
            nonlocal cleanup, result
            cleanup, result = _dispatch_interactive_submission(
                ctx, envelope, cancel_event, register, publish_assistant
            )

        run_interactive_worker(operation, publish_text=publish_diagnostic)
        stream_status = (
            stream_channel.status(envelope.run_generation)
            if stream_channel is not None
            else None
        )
        return InteractiveWorkerResult(
            result,
            compatibility_capture.getvalue() if stream_channel is None else "",
            "",
            assistant_streamed or bool(stream_status and stream_status.assistant_seen),
            compatibility_capture.truncated or bool(stream_status and stream_status.truncated),
        )
    finally:
        cleanup()
        if broker is not None:
            broker.finish_generation(envelope.run_generation)


__all__ = ["InteractiveWorkerResult", "execute_submission"]
