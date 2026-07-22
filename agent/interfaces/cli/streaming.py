from __future__ import annotations

from typing import Any

from rich.console import Console

from agent.llm.session import ChatSession
from agent.runtime.logging import logger


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

    def on_raw_line(self, line: str) -> None:
        self.chunk_count += 1
        if self.diagnostic_level == 2 and line.strip():
            suffix = "..." if len(line) > 300 else ""
            self.console.print(f"\n[dim yellow][DIAG] Chunk {self.chunk_count}: {line[:300]}{suffix}[/dim yellow]")
        elif self.diagnostic_level == 1 and self.chunk_count % 5 == 0:
            print(f"\rRecebendo... {self.chunk_count} chunks", end="", flush=True)

    def on_thinking_chunk(self, text: str) -> None:
        if not self.thinking_started:
            self._clear_progress()
            self.console.print("[bold cyan][PENSAMENTO]:[/bold cyan]")
            self.thinking_started = True
        print(text, end="", flush=True)

    def on_content_chunk(self, text: str) -> None:
        if not self.content_started:
            self._clear_progress()
            title = "[RESPOSTA FINAL]" if self.thinking_started and self.session.thinking_budget else "[RESPOSTA]"
            self.console.print(f"[bold green]{title}:[/bold green]")
            self.content_started = True
        print(text, end="", flush=True)

    def on_error(self, message: str) -> None:
        self.console.print(f"\n\n[bold red]Erro do servidor: {message}[/bold red]")
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
        self.console.print(f"\n\n[bold yellow][DIAG] Tokens: prompt={prompt_n}, resposta={predicted_n}[/bold yellow]")
        self.console.print(f"[bold yellow][DIAG] Tempo: prompt={prompt_ms:.0f}ms, geração={predicted_ms:.0f}ms[/bold yellow]")

    def _clear_progress(self) -> None:
        if self.diagnostic_level == 1:
            print("\r" + " " * 50, end="", flush=True)
