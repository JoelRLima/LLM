"""Native read-only views: no web engine, extension imports or active Markdown."""

from __future__ import annotations

import json
from collections.abc import Callable
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText

from llm_agent.application.workbench.contracts import PanelContent, PanelReference

_COMPLETENESS = {"partial": "parcial", "final": "final", "unknown": "completude não informada"}


class ArtifactPanels:
    def __init__(self, parent: ttk.Frame, load: Callable[[PanelReference, int], None]) -> None:
        self.load = load
        self.tabs = ttk.Notebook(parent)
        self.tabs.pack(fill="both", expand=True)
        self._frames: dict[tuple[str, str | None, str], ttk.Frame] = {}
        self._references: dict[str, PanelReference] = {}
        self._content: dict[str, ScrolledText] = {}
        self._next: dict[str, ttk.Button] = {}

    def open(self, reference: PanelReference, content: PanelContent | None = None) -> None:
        existing = self._frames.get(reference.key)
        if existing is not None:
            self.tabs.select(existing)
            if content is not None:
                self.show(content)
            return
        if len(self._frames) >= 12:
            raise ValueError("Feche uma aba antes de abrir mais de 12 painéis.")
        frame = ttk.Frame(self.tabs, padding=8)
        self._frames[reference.key] = frame
        self._references[str(frame)] = reference
        self.tabs.add(frame, text=reference.title[:40])
        self.tabs.select(frame)
        identity = (f"Workspace: {reference.workspace_id}\n"
                    f"Execução: {reference.run_id or 'não informada'}\n"
                    f"Origem: {reference.source} • {_COMPLETENESS[reference.completeness]}\n"
                    f"Artefato: {reference.artifact_id}")
        ttk.Label(frame, text=identity, wraplength=600).pack(fill="x")
        toolbar = ttk.Frame(frame)
        toolbar.pack(fill="x", pady=5)
        ttk.Button(toolbar, text="Fechar aba", command=lambda: self.close(reference)).pack(side="right")
        text = ScrolledText(frame, wrap="word", font="TkFixedFont", state="disabled")
        text.pack(fill="both", expand=True)
        text.tag_configure("added", foreground="#126b24")
        text.tag_configure("removed", foreground="#a82b2b")
        self._content[str(frame)] = text
        if content is None:
            self.load(reference, 0)
        else:
            self.show(content)

    def show(self, content: PanelContent) -> None:
        frame = self._frames.get(content.reference.key)
        if frame is None:
            return
        text = self._content[str(frame)]
        value = content.text
        if content.renderer == "json":
            try:
                json.loads(value)
            except (ValueError, RecursionError):
                value = "JSON inválido; conteúdo original abaixo:\n\n" + value
        text.configure(state="normal")
        text.delete("1.0", "end")
        if content.renderer == "diff":
            for line in value.splitlines(keepends=True):
                tag = "added" if line.startswith("+") else "removed" if line.startswith("-") else ""
                text.insert("end", line, tag)
        else:
            text.insert("end", value)
        if content.truncated:
            text.insert("end", "\n\n[Conteúdo parcial: limite da prévia ou truncamento na origem.]")
        text.insert("end", f"\n\nSHA-256 do artefato: {content.sha256 or 'não disponível nesta projeção'}")
        text.configure(state="disabled")
        previous = self._next.pop(str(frame), None)
        if previous is not None:
            previous.destroy()
        if content.next_offset is not None:
            next_offset = content.next_offset
            button = ttk.Button(frame, text="Próximo trecho", command=lambda: self.load(content.reference, next_offset))
            button.pack()
            self._next[str(frame)] = button

    def error(self, reference: PanelReference, message: str) -> None:
        frame = self._frames.get(reference.key)
        if frame is not None:
            text = self._content[str(frame)]
            text.configure(state="normal")
            text.delete("1.0", "end")
            text.insert("end", "Falha ao carregar o artefato:\n" + message)
            text.configure(state="disabled")

    def close(self, reference: PanelReference) -> None:
        frame = self._frames.pop(reference.key, None)
        if frame is not None:
            self._references.pop(str(frame), None)
            self._content.pop(str(frame), None)
            self._next.pop(str(frame), None)
            self.tabs.forget(frame)
            frame.destroy()
