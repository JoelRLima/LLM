from __future__ import annotations

from typing import Any

from rich.console import Console

from agent.llm.session import ChatSession
from agent.runtime.logging import logger
from agent.runtime.worker_output import emit_worker_output


class StreamingDisplay:
    """Owns presentation state and callbacks for one streamed chat response."""

    def __init__(self, console: Console, session: ChatSession, diagnostic_level: int) -> None:
        self.console = console
        self.session = session
        self.diagnostic_level = diagnostic_level
        self.chunk_count = 0
        self.thinking_started = False
        self.content_started = False
        self.timings: dict[str, Any] | None = None

    def _emit(self, value: object, *, end: str = "\n") -> None:
        emit_worker_output(
            value,
            end=end,
            fallback=lambda item, item_end: self.console.print(item, end=item_end),
        )

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
            title = "[RESPOSTA FINAL]" if self.thinking_started and self.session.thinking_budget else "[RESPOSTA]"
            self._emit(f"[bold green]{title}:[/bold green]")
            self.content_started = True
        self._emit(text, end="")

    def on_error(self, message: str) -> None:
        self._emit(f"\n\n[bold red]Erro do servidor: {message}[/bold red]")
        logger.error("Erro reportado pelo servidor no stream: %s", message)

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
