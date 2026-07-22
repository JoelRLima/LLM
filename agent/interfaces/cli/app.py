import sys
from typing import Any

from rich.console import Console

from agent.interfaces.cli.chat import run_agent_turn, run_chat_turn
from agent.llm.session import ChatSession
from agent.runtime import paths
from agent.runtime.config import carregar_config

console = Console()
NIVEIS_THINKING = {512: "BAIXO", 1024: "MÉDIO", 2048: "ALTO"}


def obter_status_think(session: ChatSession) -> str:
    if session.thinking_budget > 0:
        level = NIVEIS_THINKING.get(session.thinking_budget, "?")
        return f"[green]LIGADO ({level}, {session.thinking_budget})[/green]"
    return "[yellow]OFF[/yellow]"


def _build_context(config: dict[str, Any]) -> Any:
    from agent.interfaces.cli.commands import CommandContext
    from agent.orchestrator import Orchestrator
    from agent.skills import load_all_skills

    session = ChatSession(config["default_system_prompt"], config)
    skills = load_all_skills(model_gateway=session.gateway, config=config)
    orchestrator = Orchestrator(session, skills, verbose=False)
    orchestrator.load_memory_from_file(paths.MEMORY_FILE)
    for skill in skills:
        if hasattr(skill, "orchestrator"):
            skill.orchestrator = orchestrator
    return CommandContext(session, orchestrator, config)


def _prompt(ctx: Any) -> str | None:
    thinking = obter_status_think(ctx.session)
    diagnostic = ("", " [yellow][DIAG NORMAL][/yellow]", " [yellow][DIAG VERBOSE][/yellow]")[ctx.modo_diagnostico]
    agent = " [green][AGENTE][/green]" if ctx.modo_agente else ""
    try:
        return str(console.input(f"\n[cyan][Pensar: {thinking}][/cyan]{diagnostic}{agent} > "))
    except (EOFError, KeyboardInterrupt):
        console.print("\n[bold yellow]Encerrando...[/bold yellow]")
        return None


def _handle_input(text: str, ctx: Any) -> bool:
    from agent.interfaces.cli.commands import handle_command

    handled, should_exit = handle_command(text, ctx)
    if handled:
        return should_exit
    if ctx.modo_agente and not text.startswith("/"):
        run_agent_turn(console, ctx, text)
    else:
        run_chat_turn(console, ctx.session, text, ctx.modo_diagnostico)
    return False


def main() -> None:
    from agent.interfaces.cli.commands import exibir_menu

    try:
        ctx = _build_context(carregar_config())
    except FileNotFoundError:
        sys.exit(1)
    console.rule("[bold cyan]=== CHAT INICIADO ===[/bold cyan]")
    exibir_menu()
    while True:
        text = _prompt(ctx)
        if text is None:
            break
        if text.strip() and _handle_input(text, ctx):
            break


if __name__ == "__main__":
    main()
