from __future__ import annotations

from typing import Any, Callable, Literal, cast

from rich.panel import Panel

from llm_agent.application.code_commands import execute_code_command
from llm_agent.application.conversation import configure_conversation, execute_history_command, read_conversation
from llm_agent.application.session_diagnostics import apply_interactive_diagnostic_mode
from llm_agent.application.task_directives import parse_task_request as parse_task_request
from llm_agent.application.task_execution import (
    execute_workspace_command,
    read_execution_capabilities,
    select_execution_mode,
    set_session_diagnostics,
)
from llm_agent.interfaces.cli import interactive_commands as _interactive_commands
from llm_agent.interfaces.cli import task_commands as _task_commands
from llm_agent.interfaces.cli.interactive_input import prompt_value as _prompt_value
from llm_agent.interfaces.cli.memory_commands import (
    clear_memory,
    forget,
    load_memory,
    remember,
    save_memory,
    show_memory,
)
from llm_agent.interfaces.cli.thinking_presets import DEFAULT_THINKING_BUDGET, THINKING_PRESET_BY_KEY
from llm_agent.interfaces.cli.ui import ConsoleChangeApprover, console, render_code_result
from llm_agent.interfaces.cli.workspace_entry import workspace_storage_path

Handler = Callable[[str, Any], None]


def mode_command(text: str, ctx: Any) -> None:
    parts = text.strip().split(maxsplit=1)
    if len(parts) == 1:
        label = read_execution_capabilities(ctx.task_execution).label
        console.print(
            f"Modo ativo: {label}\n\n"
            "OpÃ§Ãµes:\n"
            "  /modo read-only   Somente leitura; sem mutaÃ§Ãµes\n"
            "  /modo editor      Leitura e ediÃ§Ã£o controlada no workspace\n"
            "  /modo full        Usa toda a autoridade jÃ¡ concedida\n\n"
            "FULL continua sujeito a grants, approvals e confinement."
        )
        return
    remainder = parts[1].strip()
    if remainder.casefold().startswith("set "):
        remainder = remainder[4:].strip()
    if remainder.casefold() in {"help", "ajuda"}:
        mode_command("/modo", ctx)
        return
    selection = select_execution_mode(ctx.task_execution, remainder)
    if selection.status == "invalid":
        console.print("[yellow]Uso: /modo [read-only|editor|full][/yellow]")
        return
    if selection.status == "unavailable":
        console.print("[red]Modos operacionais indispon?veis nesta sess?o.[/red]")
        return
    console.print(f"Modo ativo: {selection.label}")


def system_prompt(_: str, ctx: Any) -> None:
    value = _prompt_value(ctx, "[bold cyan]Digite o novo System Prompt:[/bold cyan] ")
    if value.strip():
        configure_conversation(ctx.conversation, system_prompt=value)
        console.print("[bold green]System Prompt atualizado![/bold green]")


def show_prompt(_: str, ctx: Any) -> None:
    console.print(Panel(read_conversation(ctx.conversation).effective_system_prompt, title="[bold blue]Prompt ativo[/bold blue]"))


def toggle_thinking(_: str, ctx: Any) -> None:
    if read_conversation(ctx.conversation).thinking_budget:
        configure_conversation(ctx.conversation, thinking_budget=0)
        console.print("[bold yellow]Thinking OFF[/bold yellow]")
        return
    choice = _prompt_value(ctx, "[bold cyan]Tokens (B=baixo, M=mÃ©dio, A=alto, ou nÃºmero):[/bold cyan] ").upper()
    budget = THINKING_PRESET_BY_KEY.get(choice)
    if budget is not None:
        configure_conversation(ctx.conversation, thinking_budget=budget)
    else:
        try:
            configure_conversation(ctx.conversation, thinking_budget=int(choice))
        except ValueError:
            configure_conversation(ctx.conversation, thinking_budget=DEFAULT_THINKING_BUDGET)
    console.print(f"[bold green]Thinking ON (teto: {read_conversation(ctx.conversation).thinking_budget} tokens)[/bold green]")


def clear_history(_: str, ctx: Any) -> None:
    execute_history_command(ctx.conversation, "clear")
    console.print("[bold green]HistÃ³rico limpo![/bold green]")


def _history_path(prompt: str, ctx: Any) -> str:
    default = workspace_storage_path(ctx, "chat_history_file", "chat_history.json")
    entered = _prompt_value(ctx, f"[bold cyan]{prompt} (Enter para '{default}'):[/bold cyan] ", default=str(default))
    return str(entered or default)


def save_history(_: str, ctx: Any) -> None:
    path = _history_path("Caminho do arquivo", ctx)
    outcome = execute_history_command(ctx.conversation, "save", path)
    assert outcome is not None
    success, error = outcome.success, outcome.message
    console.print(f"[bold green]HistÃ³rico salvo em '{path}'.[/bold green]" if success else f"[bold red]Erro ao salvar: {error}[/bold red]")


def load_history(_: str, ctx: Any) -> None:
    path = _history_path("Caminho do arquivo", ctx)
    outcome = execute_history_command(ctx.conversation, "load", path)
    assert outcome is not None
    success, error = outcome.success, outcome.message
    console.print(f"[bold green]HistÃ³rico carregado de '{path}'.[/bold green]" if success else f"[bold red]Erro ao carregar: {error}[/bold red]")


def toggle_debug(_: str, ctx: Any) -> None:
    ctx.modo_diagnostico = (ctx.modo_diagnostico + 1) % 3
    apply_interactive_diagnostic_mode(
        ctx.task_execution,
        ctx.modo_diagnostico,
        session_diagnostics_enabled=(
            None if getattr(ctx, "controller", None) is not None else ctx.modo_diagnostico >= 1
        ),
    )
    labels = ("DESLIGADO", "LIGADO", "VERBOSE")
    console.print(f"[bold yellow]DiagnÃ³stico {labels[ctx.modo_diagnostico]}.[/bold yellow]")
    if getattr(ctx, "controller", None) is None:
        set_session_diagnostics(ctx.task_execution, ctx.modo_diagnostico >= 1)
    else:
        ctx.diagnostic_level = labels[ctx.modo_diagnostico]


def code_command(text: str, ctx: Any) -> None:
    def allows_write_validate() -> bool:
        return cast(bool, read_execution_capabilities(ctx.task_execution).allows_write_validate)

    workspace = getattr(ctx, "workspace", None)
    outcome = execute_code_command(
        text,
        config=ctx.config,
        conversation=ctx.conversation,
        workspace_root=workspace.root if workspace is not None else ".",
        allows_write_validate=allows_write_validate,
        is_full_mode=lambda: read_execution_capabilities(ctx.task_execution).is_full_mode,
        approval_factory=lambda assume_yes: ConsoleChangeApprover(assume_yes).approve,
    )
    if outcome.kind in {"parse_error", "mode_denied", "tests_denied"}:
        console.print(f"[bold red]{outcome.error}[/bold red]")
        return
    if outcome.kind == "help":
        console.print(Panel(outcome.help_text, title="[bold blue]/code[/bold blue]"))
        return
    render_code_result(outcome)


def doctor(text: str, ctx: Any) -> None:
    from llm_agent.application.health import (
        HealthDiagnosticsRequest,
        run_health_diagnostics,
    )

    result = run_health_diagnostics(
        HealthDiagnosticsRequest(
            write_report="--write-report" in text.split(),
            app_paths=getattr(ctx, "app_paths", None),
            workspace=getattr(ctx, "workspace", None),
            config_path=getattr(ctx, "config_path", None),
            profile=getattr(ctx, "config", {}).get("default_model_profile"),
        )
    )
    print(result.rendered_report)


def _skill_result(ctx: Any, name: str, args: dict[str, Any], *, empty: str = "") -> None:
    actions: dict[str, Literal["list", "read", "find", "search"]] = {"directory_lister": "list", "file_reader": "read", "grep": "find", "web_search": "search"}
    value = str(args.get("file_path") or args.get("pattern") or args.get("query") or "")
    result = execute_workspace_command(ctx.task_execution, actions[name], value)
    if result.get("status") == "unavailable":
        console.print(f"[red]Skill '{name}' nÃ£o disponÃ­vel.[/red]")
        return
    if not result.get("ok"):
        console.print(f"[red]Erro: {result.get('error', 'desconhecido')}[/red]")
        return
    console.print(result.get("data") or empty)


def list_files(_: str, ctx: Any) -> None:
    _skill_result(ctx, "directory_lister", {"path": "."}, empty="[yellow]DiretÃ³rio vazio.[/yellow]")


def _argument(text: str, usage: str) -> str:
    parts = text.strip().split(maxsplit=1)
    if len(parts) == 1 or not parts[1].strip():
        console.print(f"[red]Uso: {usage}[/red]")
        return ""
    return parts[1].strip()


def read_file(text: str, ctx: Any) -> None:
    path = _argument(text, "/read <arquivo>")
    if path:
        _skill_result(ctx, "file_reader", {"file_path": path})


def find_text(text: str, ctx: Any) -> None:
    pattern = _argument(text, "/find <texto>")
    if pattern:
        _skill_result(ctx, "grep", {"pattern": pattern, "path": "."}, empty="[yellow]Nenhuma ocorrÃªncia encontrada.[/yellow]")


def web_search(text: str, ctx: Any) -> None:
    query = _argument(text, "/search <consulta>")
    if query:
        _skill_result(ctx, "web_search", {"query": query})


def agent_command(text: str, ctx: Any) -> None:
    _task_commands.agent_command(text, ctx, output=console)


def retry(text: str, ctx: Any) -> None:
    _task_commands.retry(text, ctx, output=console)


def __getattr__(name: str) -> Any:
    return getattr(_interactive_commands, name)


__all__ = [
    "agent_command",
    "clear_history",
    "clear_memory",
    "code_command",
    "doctor",
    "find_text",
    "forget",
    "list_files",
    "load_history",
    "load_memory",
    "mode_command",
    "read_file",
    "remember",
    "retry",
    "save_history",
    "save_memory",
    "show_memory",
    "show_prompt",
    "system_prompt",
    "toggle_debug",
    "toggle_thinking",
    "web_search",
]
