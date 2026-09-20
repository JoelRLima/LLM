"""Composer-safe rendering and local query presentation."""

from __future__ import annotations

from typing import Any

from agent.interfaces.cli import turn_rendering
from agent.interfaces.cli.interactive_shell import prompt_from
from agent.interfaces.cli.output_projection import render_publication
from agent.interfaces.cli.query_rendering import query_arguments, render_query_result, submit_query
from agent.interfaces.cli.ui import console
from agent.outputs.models import (
    OutputContentPolicy,
    OutputError,
    OutputKind,
    OutputPublishRequest,
    OutputSource,
)

__all__ = [
    "drain_worker_stream",
    "prompt",
    "query_arguments",
    "render_controller_outcome",
    "render_query_result",
    "render_rejected_preserve",
    "render_worker_message",
    "render_worker_stream",
    "submit_query",
]


def prompt(ctx: Any) -> str | None:
    mode = getattr(ctx.orchestrator, "operational_mode_label", "FULL")
    diagnostic = turn_rendering.diagnostic_prompt_token(int(getattr(ctx, "modo_diagnostico", 0)))
    shell = getattr(ctx, "shell", None)
    draft = str(getattr(ctx, "draft_text", "") or "")
    set_draft = getattr(shell, "set_draft", None)
    if draft and callable(set_draft):
        set_draft(draft)
    if hasattr(ctx, "draft_text"):
        ctx.draft_text = ""
    message = f"\nVocê [{mode}]{diagnostic} > "
    if shell is not None:
        value = shell.prompt(message)
        return None if value is None else str(value)
    value = prompt_from(console, message, default=draft, suppress_interrupt=True)
    if value is None:
        return None
    return str(value)


def render_controller_outcome(ctx: Any, outcome: Any, *, preserve_text: str | None = None) -> None:
    shell = getattr(ctx, "shell", None)
    view_model = getattr(ctx, "view_model", None)
    controller = getattr(ctx, "controller", None)
    if view_model is not None and controller is not None:
        view_model.set_pending_count(len(controller.pending.list()))

    def emit(message: object) -> None:
        if shell is not None:
            shell.print_background(message)
        else:
            console.print(message, markup=False)

    disposition = getattr(outcome, "disposition", "")
    if disposition == "REJECTED_PRESERVE" and preserve_text is None:
        raise ValueError("REJECTED_PRESERVE requires the exact visible input")
    generation = getattr(outcome, "run_generation", None)
    pending_id = getattr(outcome, "pending_id", None)
    reason = getattr(outcome, "reason", None)
    messages = {
        "ACCEPTED": f"[interactive] execução iniciada (generation={generation})",
        "PENDING_FOLLOWUP": f"[interactive] pendente #{pending_id}; use /pending para revisar e enviar quando ocioso",
        "REJECTED_PRESERVE": f"[interactive] entrada preservada; exige estado ocioso ({reason or 'REQUIRES_IDLE'})",
    }
    message = messages.get(disposition)
    if message is not None:
        if disposition == "REJECTED_PRESERVE" and preserve_text is not None:
            ctx.draft_text = preserve_text
            set_draft = getattr(shell, "set_draft", None)
            if callable(set_draft):
                set_draft(preserve_text)
        emit(message)


def render_rejected_preserve(ctx: Any, outcome: Any, visible_text: str) -> None:
    """Render every preserve rejection with its exact original input."""

    if getattr(outcome, "disposition", "") != "REJECTED_PRESERVE":
        raise ValueError("preserve renderer requires REJECTED_PRESERVE")
    render_controller_outcome(ctx, outcome, preserve_text=visible_text)


def _emit(ctx: Any, value: object) -> None:
    shell = getattr(ctx, "shell", None)
    if shell is not None:
        shell.print_background(value)
    else:
            console.print(value, markup=False)


def render_worker_stream(ctx: Any, chunk: Any) -> None:
    """Render one already-accepted worker chunk on the prompt/UI thread."""

    kind = getattr(chunk, "kind", "diagnostic")
    text = str(getattr(chunk, "text", ""))
    if not text:
        return
    if kind == "assistant":
        shell = getattr(ctx, "shell", None)
        stream = getattr(shell, "print_stream", None)
        if callable(stream):
            stream(text)
        else:
            _emit(ctx, text)
        return
    _emit(ctx, f"[worker] {text}")


def drain_worker_stream(ctx: Any) -> None:
    """Consume all currently available chunks without blocking the composer."""

    controller = getattr(ctx, "controller", None)
    poll = getattr(controller, "poll_stream", None)
    if not callable(poll):
        return
    for chunk in poll():
        render_worker_stream(ctx, chunk)


def _render_worker_error(ctx: Any, error: BaseException, generation: int | None = None) -> None:
    _emit(ctx, f"[interactive] execução falhou: {type(error).__name__}: {error}")
    view_model = getattr(ctx, "view_model", None)
    if view_model is not None and generation is not None:
        view_model.apply_error(generation, error)
    if any(token in str(error).casefold() for token in ("endpoint", "connection", "conex", "timeout", "model unavailable")):
        _emit(ctx, "Ações: /retry | /model select <profile> | /doctor")


def _worker_result_parts(message: Any) -> tuple[Any, str, str, bool, bool]:
    result = getattr(message, "result", None)
    if not (hasattr(result, "result") and hasattr(result, "stdout") and hasattr(result, "stderr")):
        return result, "", "", False, False
    return (
        getattr(result, "result", None),
        str(getattr(result, "stdout", "") or ""),
        str(getattr(result, "stderr", "") or ""),
        bool(getattr(result, "assistant_streamed", False)),
        bool(getattr(result, "assistant_stream_truncated", False)),
    )


def _publish_worker_diagnostic(
    ctx: Any,
    stdout: str,
    stderr: str,
    source_truncated: bool,
    *,
    source: OutputSource,
) -> Any:
    application = getattr(ctx, "application", None)
    accessor = getattr(application, "output_service", None)
    if not callable(accessor):
        return None
    diagnostic = stdout
    if stderr:
        diagnostic += ("\n" if diagnostic else "") + "[stderr]\n" + stderr
    try:
        return accessor().publish(
            OutputPublishRequest(
                kind=OutputKind.LOG,
                source=source,
                title="Worker diagnostic output",
                text=diagnostic,
                content_policy=OutputContentPolicy.PUBLIC_TEXT,
                source_truncated=source_truncated,
                metadata={"source_label": "worker settled"},
            )
        )
    except OutputError:
        return None


def _render_worker_diagnostics(ctx: Any, stdout: str, stderr: str, publication: Any) -> None:
    if publication is not None and publication.artifact is not None:
        render_publication(publication, lambda value: _emit(ctx, value))
        return
    if stdout:
        _emit(ctx, stdout)
    if stderr:
        _emit(ctx, stderr)


def _render_streamed_worker_result(ctx: Any, result: Any, truncated: bool) -> None:
    if truncated:
        _emit(ctx, "[interactive] resposta parcial; saída excedeu o limite de streaming")
    status = str(getattr(result, "status", "")).casefold()
    if status in {"", "succeeded", "success"}:
        return
    summary = getattr(result, "error", None) or getattr(result, "summary", None)
    if summary:
        _emit(ctx, summary)


def _render_settled_worker_result(ctx: Any, result: Any) -> None:
    to_legacy_dict = getattr(result, "to_legacy_dict", None)
    if callable(to_legacy_dict):
        document = to_legacy_dict()
        _emit(ctx, document.get("data") or document.get("error") or document.get("status", "concluído"))
        return
    answer = getattr(result, "answer", None)
    summary = getattr(result, "summary", None) or getattr(result, "error", None)
    if answer or summary:
        _emit(ctx, answer or summary)


def _render_worker_result(
    ctx: Any,
    message: Any,
    result: Any,
    stdout: str,
    stderr: str,
    assistant_streamed: bool = False,
    assistant_stream_truncated: bool = False,
) -> None:
    view_model = getattr(ctx, "view_model", None)
    if view_model is not None:
        view_model.apply_result(message.run_generation, result)
    assistant_streamed = assistant_streamed or bool(getattr(message, "assistant_streamed", False))
    assistant_stream_truncated = assistant_stream_truncated or bool(
        getattr(message, "assistant_stream_truncated", False)
    )
    publication = None
    if not assistant_streamed and (stdout or stderr):
        publication = _publish_worker_diagnostic(
            ctx,
            stdout,
            stderr,
            assistant_stream_truncated,
            source=OutputSource.WORKER_DIAGNOSTIC,
        )
    _render_worker_diagnostics(ctx, stdout, stderr, publication)
    if assistant_streamed:
        _render_streamed_worker_result(ctx, result, assistant_stream_truncated)
        return
    _render_settled_worker_result(ctx, result)


def render_worker_message(ctx: Any, message: Any) -> None:
    drain_worker_stream(ctx)
    error = getattr(message, "error", None)
    if error is not None:
        _render_worker_error(ctx, error, getattr(message, "run_generation", None))
        return
    result, stdout, stderr, assistant_streamed, assistant_stream_truncated = _worker_result_parts(message)
    _render_worker_result(
        ctx,
        message,
        result,
        stdout,
        stderr,
        assistant_streamed,
        assistant_stream_truncated,
    )
