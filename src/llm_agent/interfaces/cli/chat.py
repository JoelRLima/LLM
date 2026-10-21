from __future__ import annotations

import logging
from typing import Any, Mapping, cast

from rich.console import Console

from llm_agent.application.conversation import (
    ChatRequestPreview,
    ChatTurn,
    ConversationRuntime,
    begin_chat_turn,
    finish_chat_turn,
    stream_chat_turn,
)
from llm_agent.application.model_errors import ModelConnectionError, ModelTimeoutError
from llm_agent.application.task_execution import execute_submission, observe_task_settlement
from llm_agent.interfaces.cli import turn_rendering
from llm_agent.interfaces.cli.streaming import StreamingDisplay

_logger = logging.getLogger("LLM_Agent")


def _write_literal(console: Console, content: str) -> None:
    """Write model text directly to the Console-bound stream.

    Rich rendering is intentionally bypassed here: answer content is payload,
    not CLI markup, and must not be wrapped, normalized, or interpreted.
    """

    if not content:
        return
    console.file.write(content)
    console.file.flush()


def show_request_preview(console: Console, preview: ChatRequestPreview) -> None:
    data: Mapping[str, Any] = {
        "model": preview.model,
        "temperature": preview.temperature,
        "max_output_tokens": preview.max_output_tokens,
        "stream": preview.stream,
        "structured_output": preview.structured_output_mode,
        "num_messages": preview.message_count,
    }
    console.print("\n[bold yellow][DIAGNÓSTICO] Requisição canônica:[/bold yellow]")
    console.print_json(data=data)


def _request(
    console: Console,
    turn: ChatTurn,
    callbacks: dict[str, Any],
) -> str | None:
    try:
        return cast(str | None, stream_chat_turn(turn, callbacks))
    except ModelTimeoutError:
        message = "Tempo limite da requisição excedido."
    except ModelConnectionError as exc:
        message = f"Erro de conexão: {exc}"
    except Exception as exc:
        message = f"Erro inesperado: {exc}"
        _logger.exception("Erro inesperado na requisição")
    console.print(f"[bold red]{message}[/bold red]")
    _logger.error(message)
    finish_chat_turn(turn, failed=True)
    return None


def run_chat_turn(console: Console, conversation: ConversationRuntime, text: str, diagnostic_level: int) -> None:
    turn = begin_chat_turn(
        conversation,
        text,
        include_preview=diagnostic_level == 2,
        presentation_fallback=lambda item, item_end: console.print(item, end=item_end),
    )
    if turn.preview is not None:
        show_request_preview(console, turn.preview)
    console.rule("[bold magenta]=== RESPOSTA ===[/bold magenta]")
    display = StreamingDisplay(console, conversation, turn, diagnostic_level)
    interrupted = False
    try:
        visible = _request(console, turn, display.callbacks())
    except KeyboardInterrupt:
        console.print("\r[bold red]Interrompido pelo usuário.[/bold red]")
        _logger.warning("Geração de resposta interrompida pelo usuário.")
        visible = ""
        interrupted = True
    if visible is None and not interrupted:
        return
    display.show_timings()
    if not display.content_started and not interrupted:
        console.print("\r[bold red]Sem resposta recebida.[/bold red]")
    print()
    if visible and not interrupted:
        finish_chat_turn(turn, visible)
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
    settlement = execute_submission(ctx.task_execution, text, entry="natural", stream_callback=on_chunk)
    result = observe_task_settlement(settlement, channel="interactive")
    answer_value = result.get("answer", "")
    answer = answer_value if isinstance(answer_value, str) else ("" if answer_value is None else str(answer_value))
    error_value = result.get("error")
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
    if result["legacy_transcript"]:
        from llm_agent.interfaces.cli.legacy_compat import append_legacy_turn

        append_legacy_turn(ctx, text, answer)
    return result
