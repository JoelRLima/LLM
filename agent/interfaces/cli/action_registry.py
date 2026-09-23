"""CLI projection of the UI-neutral W19 action catalog."""
from __future__ import annotations

import importlib
import re
from dataclasses import dataclass
from typing import Any, Iterable

from agent.actions import DEFAULT_ACTION_CATALOG, ActionCatalog, ActionPayloadKind

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
_TOKEN = re.compile(r"\S+")
@dataclass(frozen=True, slots=True)
class CliActionBinding:
    action_id: str
    preferred_path: tuple[str, ...]
    aliases: tuple[tuple[str, ...], ...]
    advertised: bool
    prefix_payload: bool
    routing_kind: str
    busy_policy: str
    busy_submit: str
    model_use: str
    mutation_policy: str
    handler_owner: str | None
    agentic_owner: str | None = None
    agentic_boundary: str | None = None
    def __post_init__(self) -> None:
        if not self.preferred_path or not self._valid_path(self.preferred_path, preferred=True):
            raise ValueError(f"invalid preferred path for {self.action_id}")
        if any(not self._valid_path(alias, preferred=False) for alias in self.aliases):
            raise ValueError(f"invalid compatibility alias for {self.action_id}")
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
            raise ValueError(f"agentic action has no owner: {self.action_id}")
        if not self.advertised and self.preferred_path:
            raise ValueError("every binding with a preferred path must be advertised")
    @staticmethod
    def _valid_path(path: tuple[str, ...], *, preferred: bool) -> bool:
        if not path or any(not isinstance(token, str) or not token for token in path):
            return False
        if preferred and not path[0].startswith("/"):
            return False
        if not preferred and path[0].startswith("/") is False and len(path) > 1:
            return False
        if any(token.startswith("/") for token in path[1:]):
            return False
        return all(token.casefold() == token for token in path)
@dataclass(frozen=True, slots=True)
class CliActionMatch:
    action_id: str
    binding: CliActionBinding
    matched_path: tuple[str, ...]
    used_compatibility_alias: bool
    raw_payload: str
class CliActionRegistry:
    def __init__(self, catalog: ActionCatalog, bindings: Iterable[CliActionBinding]) -> None:
        self.catalog = catalog
        values = tuple(bindings)
        by_action: dict[str, CliActionBinding] = {}
        paths: dict[tuple[str, ...], tuple[str, bool]] = {}
        for binding in values:
            definition = catalog.maybe_get(binding.action_id)
            if definition is None:
                raise ValueError(f"binding references unknown action: {binding.action_id}")
            if binding.action_id in by_action:
                raise ValueError(f"duplicate action binding: {binding.action_id}")
            by_action[binding.action_id] = binding
            for path, is_alias in ((binding.preferred_path, False), *[(alias, True) for alias in binding.aliases]):
                key = tuple(token.casefold() for token in path)
                if key in paths:
                    raise ValueError(f"duplicate CLI path: {' '.join(path)}")
                paths[key] = (binding.action_id, is_alias)
        missing = [definition.action_id for definition in catalog if definition.action_id not in by_action]
        if missing:
            raise ValueError("missing CLI bindings: " + ", ".join(sorted(missing)))
        self._bindings = values
        self._by_action = by_action
        self._paths = paths
        self._path_items = tuple(
            sorted(
                (
                    (path, action_id, alias)
                    for path, (action_id, alias) in paths.items()
                ),
                key=lambda item: (-len(item[0]), item[0]),
            )
        )
    @staticmethod
    def _tokens(text: str) -> tuple[re.Match[str], ...]:
        return tuple(_TOKEN.finditer(text))
    def match(self, text: str) -> CliActionMatch | None:
        if not isinstance(text, str):
            return None
        value = text.lstrip()
        if not value:
            return None
        tokens = self._tokens(value)
        if not tokens:
            return None
        token_values = tuple(match.group(0).casefold() for match in tokens)
        for path, action_id, is_alias in self._path_items:
            if len(token_values) < len(path) or token_values[: len(path)] != path:
                continue
            end = tokens[len(path) - 1].end()
            raw_payload = value[end:].lstrip()
            binding = self._by_action[action_id]
            return CliActionMatch(action_id, binding, path, is_alias, raw_payload)
        return None
    def preferred_commands(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                " ".join(binding.preferred_path)
                for binding in self._bindings
                if binding.advertised
            )
        )
    def binding_for_action(self, action_id: str) -> CliActionBinding:
        try:
            return self._by_action[action_id]
        except KeyError as exc:
            raise KeyError(f"unknown action binding: {action_id}") from exc
    def resolve_handler(self, binding: CliActionBinding) -> Any:
        if binding.handler_owner is None:
            return None
        module_name, function_name = binding.handler_owner.rsplit(".", 1)
        return getattr(importlib.import_module(module_name), function_name)
    def completion_items(self, text_before_cursor: str) -> tuple[str, ...]:
        if not isinstance(text_before_cursor, str) or not text_before_cursor.startswith("/"):
            return ()
        needle = text_before_cursor.casefold()
        preferred = self.preferred_commands()
        if needle.endswith(" "):
            family = needle.rstrip()
            return tuple(
                item for item in preferred
                if item.startswith(family + " ")
            )
        if " " in needle:
            return tuple(item for item in preferred if item.startswith(needle))
        return tuple(
            item for item in preferred
            if item.casefold().startswith(needle)
            and (" " not in item or item.split(" ", 1)[0].casefold() == needle)
        )
def _binding(
    action_id: str,
    preferred: str,
    aliases: tuple[str, ...],
    routing: str,
    busy: str,
    submit: str,
    model_use: str,
    mutation: str,
    owner: str | None,
    *,
    boundary: str | None = None,
) -> CliActionBinding:
    preferred_path = tuple(preferred.split())
    alias_paths = tuple(tuple(alias.split()) for alias in aliases)
    payload = DEFAULT_ACTION_CATALOG.get(action_id).payload_kind is not ActionPayloadKind.NONE
    return CliActionBinding(
        action_id=action_id,
        preferred_path=preferred_path,
        aliases=alias_paths,
        advertised=True,
        prefix_payload=payload,
        routing_kind=routing,
        busy_policy=busy,
        busy_submit=submit,
        model_use=model_use,
        mutation_policy=mutation,
        handler_owner=owner,
        agentic_owner=owner if routing == "AGENTIC" else None,
        agentic_boundary=boundary,
    )
def _default_bindings() -> tuple[CliActionBinding, ...]:
    control = "ALWAYS_LOCAL", "NOT_APPLICABLE", "NEVER", "NONE"
    idle_local = "IDLE_ONLY", "REQUIRE_IDLE_PRESERVE", "NEVER", "UI_SESSION"
    idle_canonical = "IDLE_ONLY", "REQUIRE_IDLE_PRESERVE", "NEVER", "CANONICAL_LOCAL"
    query = "ALWAYS_LOCAL", "NOT_APPLICABLE", "NEVER", "NONE"
    agentic = "AGENTIC_SUBMIT", "PENDING_TYPED_PAYLOAD", "AGENTIC_ONLY", "CANONICAL_AGENTIC"
    def c(
        action: str,
        path: str,
        aliases: tuple[str, ...],
        owner: str | None,
        policy: tuple[str, str, str, str] = control,
    ) -> CliActionBinding:
        return _binding(action, path, aliases, "CONTROL", *policy, owner)
    def q(action: str, path: str, aliases: tuple[str, ...], owner: str | None) -> CliActionBinding:
        return _binding(action, path, aliases, "QUERY", *query, owner)
    def a(action: str, path: str, aliases: tuple[str, ...], owner: str, boundary: str) -> CliActionBinding:
        return _binding(action, path, aliases, "AGENTIC", *agentic, owner, boundary=boundary)
    values = (
        c("discovery.help", "/help", ("/ajuda",), "agent.interfaces.cli.ui.exibir_menu"),
        _binding(
            "discovery.commands",
            "/commands",
            (),
            "CONTROL",
            "ALWAYS_LOCAL",
            "NOT_APPLICABLE",
            "NEVER",
            "UI_SESSION",
            "agent.interfaces.cli.discovery_ui.commands_action",
        ),
        c("run.status", "/status", (), "agent.interfaces.cli.interactive_commands.status"),
        c("run.where", "/where", (), "agent.interfaces.cli.interactive_commands.where"),
        c("run.timeline", "/timeline", ("/events",), "agent.interfaces.cli.interactive_commands.timeline"),
        c("run.details", "/details", (), "agent.interfaces.cli.interactive_commands.details"),
        c("run.cancel", "/cancel", (), "agent.interfaces.cli.interactive_commands.cancel"),
        c("run.pending_list", "/pending", (), "agent.interfaces.cli.interactive_commands.pending"),
        c("run.pending_send", "/pending send", (), "agent.interfaces.cli.interactive_commands.pending"),
        c("run.pending_edit", "/pending edit", (), "agent.interfaces.cli.interactive_commands.pending"),
        c("run.pending_discard", "/pending discard", (), "agent.interfaces.cli.interactive_commands.pending"),
        _binding("run.attention_show", "/attention", (), "ATTENTION_ACTION", "ATTENTION_ONLY", "NOT_APPLICABLE", "NEVER", "UI_SESSION", "agent.interfaces.cli.interactive_commands.attention"),
        _binding("run.attention_details", "/attention details", ("/attention detail", "/attention full", "/attention diff"), "ATTENTION_ACTION", "ATTENTION_ONLY", "NOT_APPLICABLE", "NEVER", "UI_SESSION", "agent.interfaces.cli.interactive_commands.attention"),
        _binding("run.attention_approve", "/attention approve", ("/attention aprovar", "/attention yes", "/attention sim"), "ATTENTION_ACTION", "ATTENTION_ONLY", "NOT_APPLICABLE", "NEVER", "UI_SESSION", "agent.interfaces.cli.interactive_commands.attention"),
        _binding("run.attention_deny", "/attention deny", ("/attention negar", "/attention no", "/attention nao"), "ATTENTION_ACTION", "ATTENTION_ONLY", "NOT_APPLICABLE", "NEVER", "UI_SESSION", "agent.interfaces.cli.interactive_commands.attention"),
        c("model.show", "/model", (), "agent.interfaces.cli.interactive_commands.model", idle_canonical),
        c("model.select", "/model select", ("/model usar", "/model use"), "agent.interfaces.cli.interactive_commands.model", idle_canonical),
        c("configuration.system_show", "/system show", ("/prompt",), "agent.interfaces.cli.command_handlers.show_prompt"),
        c("configuration.system_set", "/system set", ("/system", "/sistema"), "agent.interfaces.cli.command_handlers.system_prompt", idle_local),
        c("configuration.thinking_toggle", "/think", ("/pensar",), "agent.interfaces.cli.command_handlers.toggle_thinking", idle_local),
        c("history.clear", "/history clear", ("/clear", "/limpar"), "agent.interfaces.cli.command_handlers.clear_history", idle_local),
        c("history.save", "/history save", ("/save", "/salvar"), "agent.interfaces.cli.command_handlers.save_history", idle_local),
        c("history.load", "/history load", ("/load", "/carregar"), "agent.interfaces.cli.command_handlers.load_history", idle_local),
        c("configuration.debug_toggle", "/debug", ("debug", "/diagnostico", "diagnostico"), "agent.interfaces.cli.command_handlers.toggle_debug"),
        c("memory.show", "/memory show", ("/memory", "/memoria"), "agent.interfaces.cli.command_handlers.show_memory", idle_local[:-1] + ("NONE",)),
        c("memory.remember", "/memory remember", ("/remember",), "agent.interfaces.cli.command_handlers.remember", idle_canonical),
        c("memory.forget", "/memory forget", ("/forget", "/esquecer"), "agent.interfaces.cli.command_handlers.forget", idle_canonical),
        c("memory.clear", "/memory clear", ("/clearmemory", "/limpamemoria"), "agent.interfaces.cli.command_handlers.clear_memory", idle_canonical),
        c("memory.save", "/memory save", ("/save_memory", "/salvarmemoria"), "agent.interfaces.cli.command_handlers.save_memory", idle_canonical),
        c("memory.load", "/memory load", ("/load_memory", "/carregarmemoria"), "agent.interfaces.cli.command_handlers.load_memory", idle_canonical),
        c("maintenance.doctor", "/doctor", (), "agent.interfaces.cli.command_handlers.doctor", idle_canonical),
        q("query.list_files", "/ls", ("/list",), "agent.interfaces.cli.command_handlers.list_files"),
        c("workspace.show", "/workspace", ("/diretorio", "/pwd"), "agent.interfaces.cli.interactive_commands.show_workspace", idle_canonical),
        c("workspace.switch", "/workspace switch", ("/workspace choose", "/workspace selecionar"), "agent.interfaces.cli.interactive_commands.show_workspace", idle_canonical),
        c("configuration.mode_show", "/mode", ("/modo", "/authority"), "agent.interfaces.cli.command_handlers.mode_command", idle_canonical),
        c("configuration.mode_set", "/mode set", (), "agent.interfaces.cli.command_handlers.mode_command", idle_canonical),
        a("run.retry", "/retry", ("/retomar",), "agent.interfaces.cli.command_handlers.retry", "task"),
        q("inspection.open", "/inspect", (), "agent.interfaces.cli.commands.inspect_command"),
        a("interaction.agent_submit", "/agent", ("/agente",), "agent.interfaces.cli.command_handlers.agent_command", "task"),
        a("interaction.code_submit", "/code", (), "agent.interfaces.cli.command_handlers.code_command", "typed_code_request"),
        q("query.read", "/read", (), "agent.interfaces.cli.command_handlers.read_file"),
        q("query.find", "/find", (), "agent.interfaces.cli.command_handlers.find_text"),
        q("query.git_status", "/git status", ("/git-status",), None),
        q("query.git_diff", "/git diff", ("/diff",), None),
        a("query.web_search", "/search", (), "agent.interfaces.cli.command_handlers.web_search", "web_search_tool"),
        c("session.exit", "/exit", ("exit", "sair"), None),
    )
    return values
DEFAULT_CLI_ACTION_REGISTRY = CliActionRegistry(DEFAULT_ACTION_CATALOG, _default_bindings())
__all__ = [
    "BUSY_POLICIES",
    "BUSY_SUBMITS",
    "CliActionBinding",
    "CliActionMatch",
    "CliActionRegistry",
    "DEFAULT_CLI_ACTION_REGISTRY",
    "MODEL_POLICIES",
    "MUTATION_POLICIES",
    "ROUTING_KINDS",
]
