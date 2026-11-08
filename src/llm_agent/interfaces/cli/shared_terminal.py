"""Terminal presentation over the same InteractiveSession used by the desktop."""
from __future__ import annotations

from concurrent.futures import Future, TimeoutError
from typing import Any

from llm_agent.interactive.action_parser import parse_action
from llm_agent.interfaces.cli.interactive_shell import InteractiveShell


class SharedTerminal:
    def __init__(self, session: Any, shell: InteractiveShell) -> None:
        self.session = session
        self.shell = shell
        self.pending: list[Future[Any]] = []
        self.ended = False
        self._attention_key: tuple[Any, ...] | None = None
        self._streamed = False
        self._stream_owner: Future[Any] | None = None

    def emit(self, value: str) -> None:
        if not value.startswith("[diagnóstico] "):
            self._streamed = True
        self.shell.print_stream(value)

    def render(self, result: Any, *, streamed: bool = False) -> None:
        document = result[0] if isinstance(result, tuple) and result else result
        if not isinstance(document, dict):
            self.shell.print_background(str(document))
            return
        if document.get("session_exit"):
            self.ended = True
        answer = document.get("answer") or document.get("error")
        complete_stream = streamed and document.get("status") == "succeeded" and not getattr(self.session, "last_stream_truncated", False)
        if answer and not complete_stream:
            if streamed:
                self.shell.print_background("\nResposta completa:")
            self.shell.print_background(str(answer))
        if document.get("status") in {"failed", "error", "cancelled", "timeout"}:
            self.shell.print_background(f"Estado: {document['status']}")
        if document.get("receipt"):
            from llm_agent.interfaces.cli.operational_receipt import print_operational_receipt

            print_operational_receipt(document)

    def pump(self) -> None:
        self.session.pump_stream()
        for future in tuple(self.pending):
            try:
                result = future.result(timeout=0)
            except TimeoutError:
                if not future.done():
                    continue
                self.pending.remove(future)
                self.shell.print_background("A operação terminou com timeout; confira /status.")
            except Exception as exc:
                self.pending.remove(future)
                self.shell.print_background(str(exc))
            else:
                self.pending.remove(future)
                self.render(result, streamed=future is self._stream_owner and self._streamed)
                if future is self._stream_owner:
                    self._stream_owner = None
                    self._streamed = False
        attention = self.session.attention()
        key = tuple(attention.get(k) for k in ("attention_id", "run_generation", "request_fingerprint")) if attention else None
        if key != self._attention_key:
            self._attention_key = key
            if attention:
                self.shell.print_background(
                    f"Aprovação pendente: {attention['action']} · {attention['resource']}\n{attention['prompt']}\n"
                    "Revise /attention details e decida com /attention approve ou /attention deny."
                )

    def submit(self, text: str) -> None:
        self.pump()
        match = parse_action(text)
        busy = self.session.controller.is_busy()
        if busy and (match is None and not text.lstrip().startswith("/") or match and match.binding.routing_kind == "AGENTIC"):
            item = self.session.enqueue(text)
            self.shell.print_background(str(item.get("answer") or f"Pedido guardado: #{item['pending_id']}. Use /pending após a tarefa terminar."))
            return
        if len(self.pending) >= 4:
            raise RuntimeError("Aguarde as consultas atuais terminarem.")
        future = self.session.submit(text, stream_callback=self.emit)
        if not busy:
            self._stream_owner = future
            self._streamed = False
        self.pending.append(future)
        self.pump()

    def run(self) -> None:
        self.shell.set_background_pump(self.pump)
        self.shell.set_interrupt_handler(self.session.cancel)
        self.shell.print_background(
            f"Workbench CLI · {self.session.workspace.root}\n"
            "1. Consulte /ls, /find texto ou /git diff.\n"
            "2. Use /commands para descobrir ações.\n"
            "3. Com LLM desligada, texto sem / executa um comando do shell; /exec funciona em qualquer modo.\n"
            "   Cada envio inicia processo novo na pasta do projeto, sem sandbox; /exec --timeout 60 COMANDO limita o tempo.\n"
            "4. Use /llm on para ativar a LLM; /llm test confere o catálogo sem gerar.\n"
            "Durante tarefas: /status, /cancel, /attention e /pending. Ctrl+C cancela."
        )
        while not self.ended:
            self.pump()
            mode = "LLM ATIVA" if self.session.llm_enabled else "LOCAL · LLM OFF"
            text = self.shell.prompt(f"\n[{mode}] {self.session.workspace.root} > ")
            if text is None:
                return
            if not text.strip():
                continue
            try:
                self.submit(text)
            except (ValueError, RuntimeError, OSError) as exc:
                self.shell.print_background(str(exc))
