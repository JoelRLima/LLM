"""Composer-safe rendering and local query presentation."""

from __future__ import annotations

import shlex
from typing import Any

from agent.interfaces.cli import turn_rendering
from agent.interfaces.cli.interactive_shell import prompt_from
from agent.interfaces.cli.ui import console


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
    return prompt_from(console, message, default=draft, suppress_interrupt=True)


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


def _render_worker_result(
    ctx: Any,
    message: Any,
    result: Any,
    stdout: str,
    stderr: str,
    assistant_streamed: bool = False,
    assistant_stream_truncated: bool = False,
) -> None:
    if stdout:
        _emit(ctx, stdout)
    if stderr:
        _emit(ctx, stderr)
    view_model = getattr(ctx, "view_model", None)
    if view_model is not None:
        view_model.apply_result(message.run_generation, result)
    assistant_streamed = assistant_streamed or bool(getattr(message, "assistant_streamed", False))
    assistant_stream_truncated = assistant_stream_truncated or bool(
        getattr(message, "assistant_stream_truncated", False)
    )
    if assistant_streamed:
        if assistant_stream_truncated:
            _emit(ctx, "[interactive] resposta parcial; saída excedeu o limite de streaming")
        status = str(getattr(result, "status", "")).casefold()
        if status not in {"", "succeeded", "success"}:
            summary = getattr(result, "error", None) or getattr(result, "summary", None)
            if summary:
                _emit(ctx, summary)
        return
    to_legacy_dict = getattr(result, "to_legacy_dict", None)
    if callable(to_legacy_dict):
        document = to_legacy_dict()
        _emit(ctx, document.get("data") or document.get("error") or document.get("status", "concluído"))
        return
    answer = getattr(result, "answer", None)
    summary = getattr(result, "summary", None) or getattr(result, "error", None)
    if answer or summary:
        _emit(ctx, answer or summary)


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


def query_arguments(text: str, command_id: str) -> dict[str, Any] | None:
    try:
        tokens = shlex.split(text.strip())
    except ValueError as exc:
        return {"_error": str(exc)}
    if command_id == "list_files":
        return {"path": tokens[1] if len(tokens) > 1 else "."}
    if command_id == "read":
        return {"file_path": tokens[1]} if len(tokens) > 1 else None
    if command_id == "find":
        return {"pattern": " ".join(tokens[1:]), "path": "."} if len(tokens) > 1 else None
    if command_id == "git_status":
        return {}
    if command_id == "diff":
        detail = bool(tokens[1:] and tokens[1].casefold() in {"--full", "--detail", "full", "detail"})
        return {"paths": tuple(tokens[2:] if detail else tokens[1:]), "detail": detail}
    return None


def render_query_result(ctx: Any, result: Any) -> None:
    shell = getattr(ctx, "shell", None)

    def emit(value: object) -> None:
        if shell is not None:
            shell.print_background(value)
        else:
            console.print(value, markup=False)

    marker = getattr(result, "live_marker", None)
    if marker:
        emit(marker)
    if not getattr(result, "ok", False):
        emit(getattr(result, "error", None) or "query failed")
        return
    data = getattr(result, "data", None)
    if isinstance(data, dict) and "items" in data:
        rows = data.get("items", [])
        text = "\n".join(f"{row.get('type', '?')} {row.get('relative', row.get('name', ''))}" for row in rows)
        emit(text + ("\n[output truncated]" if data.get("truncated") else "") or "(empty workspace)")
    elif isinstance(data, dict) and "content" in data:
        text = str(data.get("content", ""))
        emit(text + ("\n[output truncated]" if data.get("truncated") else ""))
    elif isinstance(data, dict) and "matches" in data:
        text = "\n".join(f"{row.get('file')}:{row.get('line')}: {row.get('content')}" for row in data.get("matches", []))
        emit(text + ("\n[output truncated]" if data.get("truncated") else "") or "(no matches)")
    elif isinstance(data, dict) and "files" in data:
        rows = data.get("files", [])
        text = "\n".join(f"{row.get('file')}: +{row.get('added', '?')} -{row.get('deleted', '?')}" for row in rows)
        emit(f"files={data.get('file_count', len(rows))}" + ("\n" + text if text else ""))
    else:
        text = str(data or "(no output)")
        emit(text + ("\n[output truncated]" if getattr(result, "truncated", False) else ""))


def submit_query(text: str, ctx: Any, command_id: str) -> bool:
    executor = getattr(ctx, "query_executor", None)
    service = getattr(ctx, "query_service", None)
    if executor is None or service is None:
        return False
    arguments = query_arguments(text, command_id)
    if arguments is None or "_error" in arguments:
        render_query_result(ctx, type("QueryError", (), {"ok": False, "error": arguments and arguments.get("_error") or f"Uso: {text.split()[0]} <argumento>"})())
        return True
    submitted = executor.submit(
        command_id,
        arguments,
        task_active=bool(getattr(ctx, "controller", None) is not None and ctx.controller.is_busy()),
        execute=service.execute,
    )
    if hasattr(submitted, "ok"):
        render_query_result(ctx, submitted)
    else:
        render_controller_outcome(ctx, type("QueryAccepted", (), {"disposition": "ACCEPTED", "run_generation": submitted.query_generation})())
    return True
