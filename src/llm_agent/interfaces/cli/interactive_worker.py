"""Canonical-boundary adapters executed by the single interactive worker."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Literal

from llm_agent.application.code_commands import execute_code_command
from llm_agent.application.code_review import CodeReviewAssessment, CodeReviewPreview
from llm_agent.application.interactive_worker import run_interactive_worker
from llm_agent.application.task_execution import (
    bind_submission_cancellation,
    execute_workspace_command,
    read_execution_capabilities,
)
from llm_agent.application.task_execution import (
    execute_submission as execute_task_submission,
)
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
    return execute_workspace_command(ctx.task_execution, "search", envelope.payload.strip(), cancellation_event=cancel_event)


def _execute_code(
    ctx: Any,
    envelope: SubmissionEnvelope,
    cancel_event: Any,
    register: Callable[[Callable[[], None]], None],
) -> Any:
    def allows_write_validate() -> bool:
        capability: bool = read_execution_capabilities(ctx.task_execution).allows_write_validate
        return capability

    broker = getattr(ctx, "approval_broker", None)

    def approval_factory(_assume_yes: bool) -> Callable[[CodeReviewPreview, CodeReviewAssessment], bool] | None:
        if broker is None:
            return None

        def approve(preview: CodeReviewPreview, assessment: CodeReviewAssessment) -> bool:
            return bool(broker.approve_change(preview, assessment))

        return approve

    workspace = getattr(ctx, "workspace", None)
    return execute_code_command(
        envelope.visible_text,
        config=ctx.config,
        conversation=ctx.conversation,
        workspace_root=workspace.root if workspace is not None else ".",
        allows_write_validate=allows_write_validate,
        is_full_mode=lambda: read_execution_capabilities(ctx.task_execution).is_full_mode,
        approval_factory=approval_factory,
        register_cancellation=register,
        cancellation_requested=cancel_event.is_set,
    )


def _bind_attention(
    ctx: Any,
    envelope: SubmissionEnvelope,
    register: Callable[[Callable[[], None]], None],
) -> Any:
    broker = getattr(ctx, "approval_broker", None)
    if broker is None:
        return None
    controller = getattr(ctx, "controller", None)
    view_model = getattr(ctx, "view_model", None)

    def _attention_arrived(_snapshot: Any) -> None:
        if view_model is not None:
            view_model.set_attention(True)
        if controller is not None:
            controller.mark_waiting_attention(envelope.run_generation)

    def _attention_cleared(_identity: Any) -> None:
        if view_model is not None:
            view_model.set_attention(False)
        if controller is not None:
            controller.mark_running(envelope.run_generation)

    broker.bind_generation(
        envelope.run_generation,
        on_attention=_attention_arrived,
        on_cleared=_attention_cleared,
    )
    register(lambda: broker.invalidate(generation=envelope.run_generation))
    return broker


def _execute_application_submission(
    ctx: Any,
    envelope: SubmissionEnvelope,
    cancel_event: Any,
    register: Callable[[Callable[[], None]], None],
    output: Callable[[str], None],
) -> tuple[Callable[[], None], Any]:
    cleanup = _bind_application(ctx, cancel_event, register)
    entry: Literal["retry", "agent", "natural"] = "retry" if envelope.command_id == "retry" else ("agent" if envelope.command_id == "agent" else "natural")
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
    if envelope.command_id == "search":
        return (lambda: None), _execute_search(ctx, envelope, cancel_event)
    if envelope.command_id == "code":
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
