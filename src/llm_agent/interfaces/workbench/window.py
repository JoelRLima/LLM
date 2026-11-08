"""A chat with a resizable, lazy artifact notebook."""

from __future__ import annotations

import tkinter as tk
from concurrent.futures import Future
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText
from typing import Any, Callable

from llm_agent.application.workbench.contracts import PanelContent, PanelReference
from llm_agent.interfaces.workbench.panels import ArtifactPanels
from llm_agent.interfaces.workbench.session import WorkbenchSession


class WorkbenchWindow:
    def __init__(self, root: tk.Tk, session: WorkbenchSession) -> None:
        self.root = root
        self.session = session
        self._pending: tuple[Future[Any], Callable[[Any], None], Callable[[str], None]] | None = None
        self._closing = False
        self._destroyed = False
        root.title("LLM Agent • Workbench técnico")
        root.geometry("1180x760")
        root.minsize(800, 500)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.status = tk.StringVar(value="Pronto • sessão READ_ONLY • aprovações indisponíveis nesta fatia")
        ttk.Label(root, text=str(session.start.workspace), padding=8).pack(fill="x")
        self.split = ttk.Panedwindow(root, orient="horizontal")
        self.split.pack(fill="both", expand=True)
        chat = ttk.Frame(self.split, padding=8)
        side = ttk.Frame(self.split, padding=8)
        self.split.add(chat, weight=3)
        self.split.add(side, weight=2)
        self.transcript = ScrolledText(chat, wrap="word", state="disabled", font="TkDefaultFont")
        self.transcript.pack(fill="both", expand=True)
        offers_bar = ttk.Frame(chat)
        offers_bar.pack(fill="x", pady=5)
        self.offers = ttk.Treeview(offers_bar, columns=("phase",), show="tree headings", height=3)
        self.offers.heading("#0", text="Relatórios e artefatos disponíveis")
        self.offers.heading("phase", text="Completude")
        self.offers.column("#0", width=260)
        self.offers.column("phase", width=130)
        self.offers.pack(side="left", fill="x", expand=True)
        scroll = ttk.Scrollbar(offers_bar, orient="vertical", command=self.offers.yview)
        scroll.pack(side="left", fill="y")
        self.offers.configure(yscrollcommand=scroll.set)
        self._offers: dict[str, PanelReference] = {}
        self.offers.bind("<Double-1>", lambda event: self.open_selected())
        ttk.Button(chat, text="Abrir selecionado ao lado", command=self.open_selected).pack(anchor="w")
        self.prompt = ScrolledText(chat, height=4, wrap="word", font="TkDefaultFont")
        self.prompt.pack(fill="x", pady=8)
        self.prompt.bind("<Control-Return>", self._send_key)
        bar = ttk.Frame(chat)
        bar.pack(fill="x")
        self.send = ttk.Button(bar, text="Enviar (Ctrl+Enter)", command=self.submit)
        self.send.pack(side="left")
        ttk.Button(bar, text="Solicitar cancelamento", command=self.cancel).pack(side="left", padx=6)
        ttk.Button(bar, text="Artefatos", command=self.outputs).pack(side="right")
        ttk.Button(bar, text="Ferramentas", command=self.inventory).pack(side="right", padx=6)
        self.panels = ArtifactPanels(side, self.load)
        ttk.Label(root, textvariable=self.status, padding=8).pack(fill="x")
        self.append("Workbench", "Solicite uma análise. Relatórios produzidos pelo Agent podem ser abertos ao lado da conversa.")
        self.prompt.focus_set()
        root.after(100, self.poll)

    def append(self, role: str, text: str) -> None:
        if len(text) > 64_000:
            text = text[:64_000] + "\n[Prévia da conversa limitada nesta janela.]"
        self.transcript.configure(state="normal")
        self.transcript.insert("end", f"{role}\n{text}\n\n")
        # Bound the presentation buffer; the canonical conversation remains with the Agent.
        if int(self.transcript.index("end-1c").split(".")[0]) > 2500:
            self.transcript.delete("1.0", "500.0")
        self.transcript.configure(state="disabled")
        self.transcript.see("end")

    def _send_key(self, event: tk.Event[Any]) -> str:
        self.submit()
        return "break"

    def watch(self, future: Future[Any], done: Callable[[Any], None], error: Callable[[str], None] | None = None) -> None:
        self._pending = future, done, error or (lambda message: self.append("Erro", message))
        self.send.configure(state="disabled")
        self.status.set("Solicitação em processamento…")

    def submit(self) -> None:
        text = self.prompt.get("1.0", "end").strip()
        if self._pending or self._closing or not text:
            return
        if len(text) > 16_000:
            self.status.set("Limite de 16.000 caracteres por solicitação nesta fatia.")
            return
        self.prompt.delete("1.0", "end")
        self.append("Você", text)
        self.watch(self.session.submit(text), self.settled)

    def settled(self, result: tuple[dict[str, Any], PanelReference | None]) -> None:
        observation, report = result
        answer = observation.get("answer") or observation.get("summary") or observation.get("error")
        self.append("Agent", str(answer) if answer else "A aplicação não forneceu resposta textual.")
        status = observation.get("status")
        if status is not None:
            self.append("Estado informado pela aplicação", str(status))
        if report is not None:
            self.offer(report)

    def offer(self, reference: PanelReference) -> None:
        existing = next((key for key, value in self._offers.items() if value.key == reference.key), None)
        if existing is not None:
            self.offers.selection_set(existing)
            return
        if len(self._offers) >= 128:
            first = next(iter(self._offers))
            self._offers.pop(first)
            self.offers.delete(first)
        phase = {"final": "final", "partial": "parcial", "unknown": "completude não informada"}[reference.completeness]
        run = reference.run_id or "execução não informada"
        key = self.offers.insert("", "end", text=f"{reference.title} • {run}", values=(phase,))
        self._offers[key] = reference
        self.offers.selection_set(key)
        self.offers.see(key)

    def open_selected(self) -> None:
        selected = self.offers.selection()
        if selected:
            self.open(self._offers[selected[0]])

    def open(self, reference: PanelReference) -> None:
        if self._pending or self._closing:
            self.status.set("Aguarde a operação atual para abrir o painel.")
            return
        try:
            self.panels.open(reference)
        except (ValueError, RuntimeError) as exc:
            self.append("Painel", str(exc))

    def load(self, reference: PanelReference, offset: int) -> None:
        if self._pending or self._closing:
            self.status.set("Aguarde a operação atual para carregar outro trecho.")
            return
        self.watch(self.session.read(reference, offset), self.panels.show,
                   lambda message: self.panels.error(reference, message))

    def outputs(self) -> None:
        if self._pending or self._closing:
            return
        self.watch(self.session.outputs(), self.output_list)

    def output_list(self, references: tuple[PanelReference, ...]) -> None:
        if not references:
            self.append("Artefatos", "Nenhum output retido neste workspace.")
        for reference in references:
            self.offer(reference)

    def inventory(self) -> None:
        if not self._pending and not self._closing:
            self.watch(self.session.inventory(), self.show_inventory)

    def show_inventory(self, content: PanelContent) -> None:
        try:
            self.panels.open(content.reference, content)
        except ValueError as exc:
            self.append("Painel", str(exc))

    def cancel(self) -> None:
        if self._pending and not self._closing:
            self.session.cancel()
            self.status.set("Cancelamento solicitado; aguardando a aplicação.")

    def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        self.watch(self.session.close(), self.destroy)
        self.status.set("Encerrando: aguardando cancelamento e liberação do runtime…")

    def destroy(self, value: None) -> None:
        self._destroyed = True
        self.root.destroy()

    def poll(self) -> None:
        if self._pending and self._pending[0].done():
            future, done, error = self._pending
            self._pending = None
            self.send.configure(state="normal")
            self.status.set("Pronto • sessão READ_ONLY")
            try:
                value = future.result()
            except Exception as exc:
                error(str(exc))
            else:
                done(value)
            if self._destroyed:
                return
        self.root.after(100, self.poll)
