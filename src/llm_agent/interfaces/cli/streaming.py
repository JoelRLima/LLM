from __future__ import annotations

import logging
from typing import Any

from rich.console import Console

from llm_agent.application.conversation import ChatTurn, ConversationRuntime, read_conversation

_logger = logging.getLogger("LLM_Agent")


class StreamingDisplay:
    """Owns presentation state and callbacks for one streamed chat response."""

    def __init__(
        self,
        console: Console,
        conversation: ConversationRuntime,
        turn: ChatTurn,
        diagnostic_level: int,
    ) -> None:
        self.console = console
        self.conversation = conversation
        self.turn = turn
        self.diagnostic_level = diagnostic_level
        self.chunk_count = 0
        self.thinking_started = False
        self.content_started = False
        self.timings: dict[str, Any] | None = None

    def _emit(self, value: object, *, end: str = "\n") -> None:
        self.turn.present_stream_text(value, end=end)

    def on_raw_line(self, line: str) -> None:
        self.chunk_count += 1
        if self.diagnostic_level == 2 and line.strip():
            suffix = "..." if len(line) > 300 else ""
            self._emit(f"\n[dim yellow][DIAG] Chunk {self.chunk_count}: {line[:300]}{suffix}[/dim yellow]")
        elif self.diagnostic_level == 1 and self.chunk_count % 5 == 0:
            self._emit(f"\rRecebendo... {self.chunk_count} chunks", end="")

    def on_thinking_chunk(self, text: str) -> None:
        if not self.thinking_started:
            self._clear_progress()
            self._emit("[bold cyan][PENSAMENTO]:[/bold cyan]")
            self.thinking_started = True
        self._emit(text, end="")

    def on_content_chunk(self, text: str) -> None:
        if not self.content_started:
            self._clear_progress()
            title = "[RESPOSTA FINAL]" if self.thinking_started and read_conversation(self.conversation).thinking_budget else "[RESPOSTA]"
            self._emit(f"[bold green]{title}:[/bold green]")
            self.content_started = True
        self._emit(text, end="")

    def on_error(self, message: str) -> None:
        self._emit(f"\n\n[bold red]Erro do servidor: {message}[/bold red]")
        _logger.error("Erro reportado pelo servidor no stream: %s", message)

    def on_done(self, timings: dict[str, Any]) -> None:
        self.timings = timings

    def callbacks(self) -> dict[str, Any]:
        return {
            "on_raw_line": self.on_raw_line,
            "on_thinking_chunk": self.on_thinking_chunk,
            "on_content_chunk": self.on_content_chunk,
            "on_error": self.on_error,
            "on_done": self.on_done,
        }

    def show_timings(self) -> None:
        if self.diagnostic_level < 1 or not self.timings:
            return
        prompt_n = self.timings.get("prompt_n", "?")
        predicted_n = self.timings.get("predicted_n", "?")
        prompt_ms = float(self.timings.get("prompt_ms", 0))
        predicted_ms = float(self.timings.get("predicted_ms", 0))
        self._emit(f"\n\n[bold yellow][DIAG] Tokens: prompt={prompt_n}, resposta={predicted_n}[/bold yellow]")
        self._emit(f"[bold yellow][DIAG] Tempo: prompt={prompt_ms:.0f}ms, geração={predicted_ms:.0f}ms[/bold yellow]")

    def _clear_progress(self) -> None:
        if self.diagnostic_level == 1:
            self._emit("\r" + " " * 50, end="")
