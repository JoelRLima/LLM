"""Closed interactive command metadata used by completion and dispatch."""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any, Iterable

ROUTING_KINDS = {"CONTROL", "QUERY", "AGENTIC", "ATTENTION_ACTION"}
BUSY_POLICIES = {"ALWAYS_LOCAL", "IDLE_ONLY", "ATTENTION_ONLY", "AGENTIC_SUBMIT"}
BUSY_SUBMITS = {
    "NOT_APPLICABLE",
    "PENDING_EXACT_TEXT",
    "PENDING_TYPED_PAYLOAD",
    "REQUIRE_IDLE_PRESERVE",
    "REJECT_PRESERVE",
}
MODEL_POLICIES = {"NEVER", "AGENTIC_ONLY"}
MUTATION_POLICIES = {"NONE", "UI_SESSION", "CANONICAL_LOCAL", "CANONICAL_AGENTIC"}


@dataclass(frozen=True)
class CommandSpec:
    canonical_command_id: str
    preferred_name: str
    aliases: tuple[str, ...]
    description: str
    routing_kind: str
    busy_policy: str
    busy_submit: str
    model_use: str
    mutation_policy: str
    handler_owner: str | None
    agentic_owner: str | None = None
    agentic_boundary: str | None = None
    prefix: bool = False
    advertised: bool = True

    def __post_init__(self) -> None:
        if self.routing_kind not in ROUTING_KINDS:
            raise ValueError(f"invalid routing kind: {self.routing_kind}")
        if self.busy_policy not in BUSY_POLICIES:
            raise ValueError(f"invalid busy policy: {self.busy_policy}")
        if self.busy_submit not in BUSY_SUBMITS:
            raise ValueError(f"invalid busy submit: {self.busy_submit}")
        if self.model_use not in MODEL_POLICIES:
            raise ValueError(f"invalid model-use policy: {self.model_use}")
        if self.mutation_policy not in MUTATION_POLICIES:
            raise ValueError(f"invalid mutation policy: {self.mutation_policy}")
        if self.routing_kind == "AGENTIC" and not self.agentic_owner:
            raise ValueError(f"agentic command has no owner: {self.canonical_command_id}")


class CommandRegistry:
    """A closed-world registry; aliases cannot acquire different policies."""

    def __init__(self, entries: Iterable[CommandSpec]) -> None:
        self.entries = tuple(entries)
        self._exact: dict[str, CommandSpec] = {}
        self._prefix: dict[str, CommandSpec] = {}
        for entry in self.entries:
            target = self._prefix if entry.prefix else self._exact
            for alias in entry.aliases:
                key = alias.casefold()
                if key in target:
                    raise ValueError(f"duplicate command alias: {alias}")
                if key in self._exact or key in self._prefix:
                    raise ValueError(f"command alias overlaps exact/prefix: {alias}")
                target[key] = entry

    def lookup(self, text: str) -> tuple[CommandSpec | None, str]:
        command = text.strip().casefold()
        exact = self._exact.get(command)
        if exact is not None:
            return exact, "exact"
        for alias, entry in self._prefix.items():
            if command == alias or command.startswith(alias + " "):
                return entry, "prefix"
        return None, "unknown"

    def resolve_handler(self, entry: CommandSpec) -> Any:
        if entry.handler_owner is None:
            return None
        module_name, function_name = entry.handler_owner.rsplit(".", 1)
        return getattr(importlib.import_module(module_name), function_name)

    def exact_handler_map(self) -> dict[str, Any]:
        return {alias: self.resolve_handler(entry) for alias, entry in self._exact.items() if entry.handler_owner}

    def prefix_handler_items(self) -> tuple[tuple[str, Any], ...]:
        return tuple(
            (alias, self.resolve_handler(entry))
            for alias, entry in self._prefix.items()
            if entry.handler_owner
        )

    def completion_items(self, prefix: str = "") -> tuple[str, ...]:
        needle = prefix.casefold()
        values = {
            entry.preferred_name
            for entry in self.entries
            if entry.advertised and entry.preferred_name.casefold().startswith(needle)
        }
        return tuple(sorted(values))

    def advertised_entries(self) -> tuple[CommandSpec, ...]:
        return tuple(entry for entry in self.entries if entry.advertised)


def _control(
    command_id: str,
    preferred: str,
    aliases: tuple[str, ...],
    description: str,
    owner: str,
    *,
    busy: str = "ALWAYS_LOCAL",
    submit: str = "NOT_APPLICABLE",
    mutation: str = "NONE",
    prefix: bool = False,
) -> CommandSpec:
    return CommandSpec(command_id, preferred, aliases, description, "CONTROL", busy, submit, "NEVER", mutation, owner, prefix=prefix)


def _agentic(
    command_id: str,
    preferred: str,
    aliases: tuple[str, ...],
    description: str,
    owner: str,
    boundary: str,
    *,
    prefix: bool = False,
) -> CommandSpec:
    return CommandSpec(
        command_id,
        preferred,
        aliases,
        description,
        "AGENTIC",
        "AGENTIC_SUBMIT",
        "PENDING_TYPED_PAYLOAD",
        "AGENTIC_ONLY",
        "CANONICAL_AGENTIC",
        owner,
        agentic_owner=owner,
        agentic_boundary=boundary,
        prefix=prefix,
    )


def _query(
    command_id: str,
    preferred: str,
    aliases: tuple[str, ...],
    description: str,
    owner: str,
    *,
    prefix: bool = False,
) -> CommandSpec:
    return CommandSpec(command_id, preferred, aliases, description, "QUERY", "ALWAYS_LOCAL", "NOT_APPLICABLE", "NEVER", "NONE", owner, prefix=prefix)


def _attention(command_id: str, preferred: str, aliases: tuple[str, ...], description: str, owner: str) -> CommandSpec:
    return CommandSpec(command_id, preferred, aliases, description, "ATTENTION_ACTION", "ATTENTION_ONLY", "NOT_APPLICABLE", "NEVER", "UI_SESSION", owner, prefix=True)


DEFAULT_COMMAND_REGISTRY = CommandRegistry(
    (
        _control("help", "/help", ("/help", "/ajuda"), "Mostra esta ajuda", "agent.interfaces.cli.ui.exibir_menu"),
        _control("status", "/status", ("/status",), "Mostra o estado atual", "agent.interfaces.cli.command_handlers.status"),
        _control("where", "/where", ("/where",), "Mostra orientação da execução", "agent.interfaces.cli.command_handlers.where"),
        _control("timeline", "/timeline", ("/timeline",), "Mostra marcos da execução", "agent.interfaces.cli.command_handlers.timeline"),
        _control("details", "/details", ("/details",), "Mostra detalhes técnicos bounded", "agent.interfaces.cli.command_handlers.details"),
        _control("cancel", "/cancel", ("/cancel",), "Solicita cancelamento", "agent.interfaces.cli.command_handlers.cancel"),
        _control("pending", "/pending", ("/pending",), "Lista ou envia pendências", "agent.interfaces.cli.command_handlers.pending", prefix=True),
        _attention("attention", "/attention", ("/attention",), "Inspeciona ou resolve a atenção atual", "agent.interfaces.cli.command_handlers.attention"),
        _control("model", "/model", ("/model",), "Mostra o perfil de modelo", "agent.interfaces.cli.command_handlers.model", busy="IDLE_ONLY", submit="REQUIRE_IDLE_PRESERVE", mutation="CANONICAL_LOCAL", prefix=True),
        _control("system_prompt", "/system", ("/system", "/sistema"), "Altera o system prompt", "agent.interfaces.cli.command_handlers.system_prompt", busy="IDLE_ONLY", submit="REQUIRE_IDLE_PRESERVE", mutation="UI_SESSION"),
        _control("show_prompt", "/prompt", ("/prompt",), "Mostra o system prompt", "agent.interfaces.cli.command_handlers.show_prompt"),
        _control("thinking", "/think", ("/think", "/pensar"), "Altera o orçamento de pensamento", "agent.interfaces.cli.command_handlers.toggle_thinking", busy="IDLE_ONLY", submit="REQUIRE_IDLE_PRESERVE", mutation="UI_SESSION"),
        _control("clear_history", "/clear", ("/clear", "/limpar"), "Limpa o histórico", "agent.interfaces.cli.command_handlers.clear_history", busy="IDLE_ONLY", submit="REQUIRE_IDLE_PRESERVE", mutation="UI_SESSION"),
        _control("save_history", "/save", ("/save", "/salvar"), "Salva o histórico", "agent.interfaces.cli.command_handlers.save_history", busy="IDLE_ONLY", submit="REQUIRE_IDLE_PRESERVE", mutation="UI_SESSION"),
        _control("load_history", "/load", ("/load", "/carregar"), "Carrega o histórico", "agent.interfaces.cli.command_handlers.load_history", busy="IDLE_ONLY", submit="REQUIRE_IDLE_PRESERVE", mutation="UI_SESSION"),
        _control("debug", "/debug", ("/debug", "debug", "/diagnostico", "diagnostico"), "Alterna diagnóstico", "agent.interfaces.cli.command_handlers.toggle_debug", mutation="UI_SESSION"),
        _control("memory", "/memory", ("/memory", "/memoria"), "Mostra a memória", "agent.interfaces.cli.command_handlers.show_memory", busy="IDLE_ONLY", submit="REQUIRE_IDLE_PRESERVE"),
        _control("events", "/events", ("/events",), "Mostra eventos", "agent.interfaces.cli.command_handlers.show_events"),
        _control("forget", "/forget", ("/forget", "/esquecer"), "Remove uma memória", "agent.interfaces.cli.command_handlers.forget", busy="IDLE_ONLY", submit="REQUIRE_IDLE_PRESERVE", mutation="CANONICAL_LOCAL"),
        _control("clear_memory", "/clearmemory", ("/clearmemory", "/limpamemoria"), "Limpa a memória", "agent.interfaces.cli.command_handlers.clear_memory", busy="IDLE_ONLY", submit="REQUIRE_IDLE_PRESERVE", mutation="CANONICAL_LOCAL"),
        _control("save_memory", "/save_memory", ("/save_memory", "/salvarmemoria"), "Salva a memória", "agent.interfaces.cli.command_handlers.save_memory", busy="IDLE_ONLY", submit="REQUIRE_IDLE_PRESERVE", mutation="CANONICAL_LOCAL"),
        _control("load_memory", "/load_memory", ("/load_memory", "/carregarmemoria"), "Carrega a memória", "agent.interfaces.cli.command_handlers.load_memory", busy="IDLE_ONLY", submit="REQUIRE_IDLE_PRESERVE", mutation="CANONICAL_LOCAL"),
        _control("doctor", "/doctor", ("/doctor",), "Executa diagnóstico de saúde", "agent.interfaces.cli.command_handlers.doctor", busy="IDLE_ONLY", submit="REQUIRE_IDLE_PRESERVE", mutation="CANONICAL_LOCAL"),
        _query("list_files", "/ls", ("/ls", "/list"), "Lista arquivos", "agent.interfaces.cli.command_handlers.list_files", prefix=True),
        _control("workspace", "/workspace", ("/workspace", "/diretorio", "/pwd"), "Mostra o workspace", "agent.interfaces.cli.command_handlers.show_workspace", busy="IDLE_ONLY", submit="REQUIRE_IDLE_PRESERVE", mutation="CANONICAL_LOCAL", prefix=True),
        _control("mode", "/mode", ("/modo", "/mode", "/authority"), "Consulta ou altera o modo", "agent.interfaces.cli.command_handlers.mode_command", busy="IDLE_ONLY", submit="REQUIRE_IDLE_PRESERVE", mutation="CANONICAL_LOCAL", prefix=True),
        _agentic("retry", "/retry", ("/retry", "/retomar"), "Retoma a tarefa", "agent.interfaces.cli.command_handlers.retry", "task"),
        _query("inspect", "/inspect", ("/inspect",), "Inspeciona uma execução", "agent.interfaces.cli.commands.inspect_command"),
        _agentic("agent", "/agent", ("/agent", "/agente"), "Submete uma tarefa", "agent.interfaces.cli.command_handlers.agent_command", "task", prefix=True),
        _agentic("code", "/code", ("/code",), "Executa workflow de código; use /code help", "agent.interfaces.cli.command_handlers.code_command", "typed_code_request", prefix=True),
        _control("remember", "/remember", ("/remember",), "Registra memória", "agent.interfaces.cli.command_handlers.remember", busy="IDLE_ONLY", submit="REQUIRE_IDLE_PRESERVE", mutation="CANONICAL_LOCAL", prefix=True),
        _query("read", "/read", ("/read",), "Lê um arquivo", "agent.interfaces.cli.command_handlers.read_file", prefix=True),
        _query("find", "/find", ("/find",), "Busca texto", "agent.interfaces.cli.command_handlers.find_text", prefix=True),
        _query("git_status", "/git-status", ("/git-status",), "Mostra Git status", "agent.interfaces.cli.command_handlers.git_status"),
        _query("diff", "/diff", ("/diff",), "Mostra diff Git", "agent.interfaces.cli.command_handlers.diff", prefix=True),
        _agentic("search", "/search", ("/search",), "Pesquisa na web", "agent.interfaces.cli.command_handlers.web_search", "web_search_tool", prefix=True),
        CommandSpec("exit", "/exit", ("/exit", "exit", "sair"), "Encerra a sessão", "CONTROL", "ALWAYS_LOCAL", "NOT_APPLICABLE", "NEVER", "UI_SESSION", None),
    )
)


__all__ = ["CommandRegistry", "CommandSpec", "DEFAULT_COMMAND_REGISTRY"]
