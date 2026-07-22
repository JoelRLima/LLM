from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from agent.interfaces.cli.command_handlers import (
    agent_command,
    clear_history,
    clear_memory,
    code_command,
    doctor,
    find_text,
    forget,
    list_files,
    load_history,
    load_memory,
    read_file,
    remember,
    retry,
    save_history,
    save_memory,
    show_events,
    show_memory,
    show_prompt,
    system_prompt,
    toggle_debug,
    toggle_thinking,
    web_search,
)
from agent.interfaces.cli.ui import ConsoleChangeApprover, exibir_menu
from agent.llm.session import ChatSession
from agent.orchestrator import Orchestrator

__all__ = ["CommandContext", "ConsoleChangeApprover", "exibir_menu", "handle_command"]


class CommandContext:
    def __init__(
        self,
        session: ChatSession,
        orchestrator: Orchestrator,
        config: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.session = session
        self.orchestrator = orchestrator
        self.config = config or session.config
        self.modo_diagnostico = 0
        self.modo_agente = True


EXACT_HANDLERS = {
    "/system": system_prompt, "/sistema": system_prompt, "/prompt": show_prompt,
    "/think": toggle_thinking, "/pensar": toggle_thinking,
    "/clear": clear_history, "/limpar": clear_history,
    "/save": save_history, "/salvar": save_history,
    "/load": load_history, "/carregar": load_history,
    "debug": toggle_debug, "/debug": toggle_debug,
    "diagnostico": toggle_debug, "/diagnostico": toggle_debug,
    "/memory": show_memory, "/memoria": show_memory, "/events": show_events,
    "/forget": forget, "/esquecer": forget,
    "/clearmemory": clear_memory, "/limpamemoria": clear_memory,
    "/save_memory": save_memory, "/salvarmemoria": save_memory,
    "/load_memory": load_memory, "/carregarmemoria": load_memory,
    "/doctor": doctor, "/ls": list_files, "/list": list_files,
    "/retry": retry, "/retomar": retry,
}
PREFIX_HANDLERS = (
    ("/agent", agent_command), ("/agente", agent_command), ("/code", code_command),
    ("/remember", remember), ("/read", read_file), ("/find", find_text),
    ("/search", web_search),
)


def handle_command(texto: str, ctx: CommandContext) -> Tuple[bool, bool]:
    """Processa comandos da CLI e informa `(tratado, deve_sair)`."""
    command = texto.strip().lower()
    if command in {"sair", "exit"}:
        return True, True
    if command in {"/help", "/ajuda"}:
        exibir_menu()
        return True, False
    handler = EXACT_HANDLERS.get(command)
    if handler is not None:
        handler(texto, ctx)
        return True, False
    for prefix, prefix_handler in PREFIX_HANDLERS:
        if command == prefix or command.startswith(prefix + " "):
            prefix_handler(texto, ctx)
            return True, False
    return False, False
