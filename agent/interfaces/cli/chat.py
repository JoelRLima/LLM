from __future__ import annotations

from typing import Any, Mapping

from rich.console import Console

from agent.interfaces.cli import turn_rendering
from agent.interfaces.cli.streaming import StreamingDisplay
from agent.llm.errors import ModelConnectionError, ModelTimeoutError
from agent.llm.session import ChatSession
from agent.runtime.logging import logger


def _write_literal(console: Console, content: str) -> None:
    """Write model text directly to the Console-bound stream.

    Rich rendering is intentionally bypassed here: answer content is payload,
    not CLI markup, and must not be wrapped, normalized, or interpreted.
    """

    if not content:
        return
    console.file.write(content)
    console.file.flush()


def show_request_preview(console: Console, session: ChatSession) -> None:
    request = session.build_request(stream=True)
    preview: Mapping[str, Any] = {
        "model": request.model,
        "temperature": request.temperature,
        "max_output_tokens": request.max_output_tokens,
        "stream": request.stream,
        "structured_output": (
            request.structured_output.mode.value
            if request.structured_output is not None
            else None
        ),
        "num_messages": len(request.messages),
    }
    console.print("\n[bold yellow][DIAGNÓSTICO] Requisição canônica:[/bold yellow]")
    console.print_json(data=preview)


def _request(
    console: Console,
    session: ChatSession,
    callbacks: dict[str, Any],
) -> str | None:
    try:
        request = session.build_request(stream=True)
        return session.consume_stream_request(request, callbacks)
    except ModelTimeoutError:
        message = "Tempo limite da requisição excedido."
    except ModelConnectionError as exc:
        message = f"Erro de conexão: {exc}"
    except Exception as exc:
        message = f"Erro inesperado: {exc}"
        logger.exception("Erro inesperado na requisição")
    console.print(f"[bold red]{message}[/bold red]")
    logger.error(message)
    session.remove_last_user_message()
    return None


def run_chat_turn(console: Console, session: ChatSession, text: str, diagnostic_level: int) -> None:
    session.add_user_message(text)
    if diagnostic_level == 2:
        show_request_preview(console, session)
    console.rule("[bold magenta]=== RESPOSTA ===[/bold magenta]")
    display = StreamingDisplay(console, session, diagnostic_level)
    interrupted = False
    try:
        visible = _request(console, session, display.callbacks())
    except KeyboardInterrupt:
        console.print("\r[bold red]Interrompido pelo usuário.[/bold red]")
        logger.warning("Geração de resposta interrompida pelo usuário.")
        visible = ""
        interrupted = True
    if visible is None and not interrupted:
        return
    display.show_timings()
    if not display.content_started and not interrupted:
        console.print("\r[bold red]Sem resposta recebida.[/bold red]")
    print()
    if visible and not interrupted:
        session.add_assistant_message(visible)
    else:
        state = "interrompida" if interrupted else "vazia"
        console.print(f"[bold yellow]A resposta foi {state}; sua mensagem foi mantida no histórico.[/bold yellow]")


def run_agent_turn(console: Console, ctx: Any, text: str) -> Any:
    streamed_content = False

    def on_chunk(chunk: str) -> None:
        nonlocal streamed_content
        if isinstance(chunk, str) and chunk:
            streamed_content = True
            _write_literal(console, chunk)

    turn_rendering.render_turn_waiting(console)
    turn_rendering.render_agent_label(console)
    interact = getattr(ctx.application, "interact", None)
    if callable(interact):
        result = interact(text, boundary="natural", stream_callback=on_chunk)
    else:
        from agent.interfaces.cli.legacy_compat import dispatch_natural_facade

        result = dispatch_natural_facade(ctx, text)
    answer_value = getattr(result, "answer", "")
    answer = answer_value if isinstance(answer_value, str) else ("" if answer_value is None else str(answer_value))
    error_value = getattr(result, "error", None)
    error = error_value if isinstance(error_value, str) else ("" if error_value is None else str(error_value))
    if streamed_content:
        _write_literal(console, "\n")
    elif answer:
        _write_literal(console, answer)
        _write_literal(console, "\n")
    elif error:
        _write_literal(console, error)
        _write_literal(console, "\n")
    turn_rendering.render_turn_result(console, result, int(getattr(ctx, "modo_diagnostico", 0)))
    if not callable(interact):
        from agent.interfaces.cli.legacy_compat import append_legacy_turn

        append_legacy_turn(ctx, text, answer)
    return result
