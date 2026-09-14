"""Canonical-boundary adapters executed by the single interactive worker."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Callable

from agent.interfaces.cli.controller import SubmissionEnvelope
from agent.interfaces.cli.worker_stream import BoundedTextBuffer, BoundedWorkerStream
from agent.runtime.worker_output import bind_worker_output


@dataclass(frozen=True)
class InteractiveWorkerResult:
    """Worker settlement plus compatibility metadata for the UI stream."""

    result: Any
    stdout: str = ""
    stderr: str = ""
    assistant_streamed: bool = False
    assistant_stream_truncated: bool = False


def _bind_application(ctx: Any, cancel_event: Any, register: Callable[[Callable[[], None]], None]) -> Callable[[], None]:
    application = ctx.application
    bind = getattr(application, "bind_interactive_cancellation", None)
    request = getattr(application, "request_cancel_only", None)
    cleanup = bind(cancel_event) if callable(bind) else (lambda: None)
    if callable(request):
        register(request)
    return cleanup


def _execute_search(ctx: Any, envelope: SubmissionEnvelope, cancel_event: Any) -> Any:
    gateway = getattr(ctx.orchestrator, "tool_invocation_gateway", None)
    if gateway is None:
        return SimpleNamespace(status="unavailable", error="web_search indisponível", data=None)
    query = envelope.payload.strip()
    result = gateway.run(
        "web_search",
        {"query": query},
        active_skills=None,
        allowed_capabilities=getattr(ctx.orchestrator, "allowed_capabilities", None),
        cancellation_token=cancel_event,
    )
    return result


def _execute_code(
    ctx: Any,
    envelope: SubmissionEnvelope,
    cancel_event: Any,
    register: Callable[[Callable[[], None]], None],
) -> Any:
    from agent.code.application import CodeRequest, CodingApplicationService, build_code_context
    from agent.code.commands import CodeCommandError, parse_code_command
    from agent.code.policy import ChangeApprover
    from agent.tools.authority import OperationalMode
    from agent.tools.invocation_semantics import CODE_TASK_ACTIONS
    from agent.tools.mode_enforcement import requests_test_execution

    try:
        parsed = parse_code_command(envelope.visible_text)
    except CodeCommandError as exc:
        return SimpleNamespace(status="failed", error=str(exc), summary=str(exc))
    if parsed.action in CODE_TASK_ACTIONS - {"analyze", "review"}:
        mode_allows = getattr(ctx.orchestrator, "mode_allows", None)
        if not callable(mode_allows) or not mode_allows({"write", "validate"}):
            return SimpleNamespace(status="blocked", error="Ação negada pelo modo operacional ativo.", summary="ação negada")
        mode = getattr(ctx.orchestrator, "operational_mode", None)
        if mode is not OperationalMode.FULL and requests_test_execution({"include_tests": parsed.include_tests}):
            return SimpleNamespace(status="blocked", error="Execução de testes exige modo FULL.", summary="modo FULL exigido")
    if parsed.action == "help":
        return SimpleNamespace(status="succeeded", answer="/code help", summary="/code help")

    request = CodeRequest(
        action=parsed.action,
        objective=parsed.objective,
        targets=parsed.targets,
        include_tests=parsed.include_tests,
        template=parsed.template,
    )
    service_context = build_code_context(ctx.config, ctx.session.gateway)
    register(service_context.cancellation.cancel)
    if cancel_event.is_set():
        service_context.cancellation.cancel()
    workspace = getattr(ctx, "workspace", None)
    base_dir = workspace.root if workspace is not None else "."
    broker = getattr(ctx, "approval_broker", None)

    class _BrokerChangeApprover(ChangeApprover):
        requires_explicit_approval = True

        def approve(self, preview: Any, assessment: Any) -> bool:
            if broker is None:
                return False
            return bool(broker.approve_change(preview, assessment))

    return CodingApplicationService(base_dir, service_context, ctx.config).execute(
        request,
        approver=_BrokerChangeApprover() if broker is not None else None,
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
    application = ctx.application
    if envelope.command_id == "retry":
        resume = getattr(application, "resume", None)
        result = resume(stream_callback=output) if callable(resume) else application.interact(
            "/continue",
            boundary="task",
            visible_user_text=envelope.visible_text,
            task_payload="/continue",
            stream_callback=output,
        )
    elif envelope.command_id == "agent":
        result = application.interact(
            envelope.payload,
            boundary="task",
            visible_user_text=envelope.visible_text,
            task_payload=envelope.payload,
            stream_callback=output,
        )
    else:
        result = application.interact(
            envelope.payload,
            boundary="natural",
            visible_user_text=envelope.visible_text,
            task_payload=envelope.payload,
            stream_callback=output,
        )
    return cleanup, result


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

    result: Any
    try:
        with bind_worker_output(publish_diagnostic):
            if envelope.command_id == "search":
                result = _execute_search(ctx, envelope, cancel_event)
            elif envelope.command_id == "code":
                result = _execute_code(ctx, envelope, cancel_event, register)
            else:
                cleanup, result = _execute_application_submission(
                    ctx, envelope, cancel_event, register, publish_assistant
                )
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
