from __future__ import annotations

from typing import Any, Mapping

from rich.console import Console

from agent.interfaces.cli.streaming import StreamingDisplay
from agent.llm.errors import ModelConnectionError, ModelTimeoutError
from agent.llm.session import ChatSession
from agent.runtime.logging import logger


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
    streamed = False

    def on_chunk(chunk: str) -> None:
        nonlocal streamed
        streamed = True
        print(chunk, end="", flush=True)

    console.print("[bold blue]Agente:[/bold blue]")
    result = ctx.application.run(text, stream_callback=on_chunk)
    answer = result.answer
    print()
    if answer and not streamed:
        console.print(answer)
    ctx.session.add_user_message(text)
    ctx.session.add_assistant_message(answer)
    return result
