from __future__ import annotations

from typing import Any

from rich.console import Console

from agent.interfaces.cli.streaming import StreamingDisplay
from agent.llm.session import ChatSession, SessionConnectionError, SessionTimeoutError
from agent.runtime.logging import logger


def show_payload_preview(console: Console, session: ChatSession) -> None:
    payload = session.build_payload()
    preview = {key: value for key, value in payload.items() if key != "messages"}
    preview["num_messages"] = len(payload["messages"])
    console.print("\n[bold yellow][DIAGNÓSTICO] Payload enviado:[/bold yellow]")
    console.print_json(data=preview)


def _request(console: Console, session: ChatSession) -> Any | None:
    try:
        return session.send_request(session.build_payload(), stream=True)
    except SessionTimeoutError:
        message = "Tempo limite da requisição excedido."
    except SessionConnectionError as exc:
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
        show_payload_preview(console, session)
    console.rule("[bold magenta]=== RESPOSTA ===[/bold magenta]")
    response = _request(console, session)
    if response is None:
        return
    display = StreamingDisplay(console, session, diagnostic_level)
    interrupted = False
    try:
        visible = session.process_stream(response, display.callbacks())
    except KeyboardInterrupt:
        console.print("\r[bold red]Interrompido pelo usuário.[/bold red]")
        logger.warning("Geração de resposta interrompida pelo usuário.")
        visible = ""
        interrupted = True
    display.show_timings()
    if not display.content_started and not interrupted:
        console.print("\r[bold red]Sem resposta recebida.[/bold red]")
    print()
    if visible and not interrupted:
        session.add_assistant_message(visible)
    else:
        state = "interrompida" if interrupted else "vazia"
        console.print(f"[bold yellow]A resposta foi {state}; sua mensagem foi mantida no histórico.[/bold yellow]")


def run_agent_turn(console: Console, ctx: Any, text: str) -> None:
    streamed = False

    def on_chunk(chunk: str) -> None:
        nonlocal streamed
        streamed = True
        print(chunk, end="", flush=True)

    console.print("[bold blue]Agente:[/bold blue]")
    answer = ctx.orchestrator.run(text, stream_callback=on_chunk)
    print()
    if answer and not streamed:
        console.print(answer)
    ctx.session.add_user_message(text)
    ctx.session.add_assistant_message(answer)
