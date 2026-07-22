from __future__ import annotations

from typing import Any, Callable

from rich.panel import Panel
from rich.table import Table

from agent.interfaces.cli.ui import ConsoleChangeApprover, console, render_code_result
from agent.runtime import paths
from agent.runtime.logging import set_debug_level

Handler = Callable[[str, Any], None]


def system_prompt(_: str, ctx: Any) -> None:
    value = console.input("[bold cyan]Digite o novo System Prompt:[/bold cyan] ")
    if value.strip():
        ctx.session.set_system_prompt(value)
        console.print("[bold green]System Prompt atualizado![/bold green]")


def show_prompt(_: str, ctx: Any) -> None:
    console.print(Panel(ctx.session.get_effective_system_prompt(), title="[bold blue]Prompt ativo[/bold blue]"))


def toggle_thinking(_: str, ctx: Any) -> None:
    if ctx.session.thinking_budget:
        ctx.session.thinking_budget = 0
        console.print("[bold yellow]Thinking OFF[/bold yellow]")
        return
    choice = console.input("[bold cyan]Tokens (B=baixo, M=médio, A=alto, ou número):[/bold cyan] ").strip().upper()
    budgets = {"B": 512, "M": 1024, "A": 2048}
    if choice in budgets:
        ctx.session.thinking_budget = budgets[choice]
    else:
        try:
            ctx.session.thinking_budget = int(choice)
        except ValueError:
            ctx.session.thinking_budget = 1024
    console.print(f"[bold green]Thinking ON (teto: {ctx.session.thinking_budget} tokens)[/bold green]")


def clear_history(_: str, ctx: Any) -> None:
    ctx.session.clear_history()
    console.print("[bold green]Histórico limpo![/bold green]")


def _history_path(prompt: str) -> str:
    entered = console.input(f"[bold cyan]{prompt} (Enter para '{paths.CHAT_HISTORY_FILE}'):[/bold cyan] ").strip()
    return str(entered or paths.CHAT_HISTORY_FILE)


def save_history(_: str, ctx: Any) -> None:
    path = _history_path("Caminho do arquivo")
    success, error = ctx.session.save_to_file(path)
    console.print(f"[bold green]Histórico salvo em '{path}'.[/bold green]" if success else f"[bold red]Erro ao salvar: {error}[/bold red]")


def load_history(_: str, ctx: Any) -> None:
    path = _history_path("Caminho do arquivo")
    success, error = ctx.session.load_from_file(path)
    console.print(f"[bold green]Histórico carregado de '{path}'.[/bold green]" if success else f"[bold red]Erro ao carregar: {error}[/bold red]")


def toggle_debug(_: str, ctx: Any) -> None:
    ctx.modo_diagnostico = (ctx.modo_diagnostico + 1) % 3
    set_debug_level(0 if ctx.modo_diagnostico == 0 else 1)
    labels = ("DESLIGADO", "LIGADO", "VERBOSE")
    console.print(f"[bold yellow]Diagnóstico {labels[ctx.modo_diagnostico]}.[/bold yellow]")
    ctx.orchestrator.verbose = ctx.modo_diagnostico >= 1
    ctx.orchestrator.context_manager.verbose = ctx.orchestrator.verbose


def agent_command(text: str, ctx: Any) -> None:
    parts = text.strip().split(maxsplit=1)
    if len(parts) == 1:
        ctx.modo_agente = not ctx.modo_agente
        console.print(f"Modo agente {'LIGADO' if ctx.modo_agente else 'DESLIGADO'}.")
        return
    objective = parts[1]
    console.print(f"[bold magenta]Executando objetivo avulso:[/bold magenta] {objective}")
    try:
        answer = ctx.orchestrator.run(objective)
        console.print(Panel(answer, title="[bold blue]Agente[/bold blue]"))
        ctx.session.add_assistant_message(answer)
    except KeyboardInterrupt:
        console.print("\n[bold red]Agente interrompido.[/bold red]")


def code_command(text: str, ctx: Any) -> None:
    from agent.code.application import CodeRequest, CodingApplicationService, build_code_context
    from agent.code.commands import CODE_COMMAND_HELP, CodeCommandError, parse_code_command

    try:
        parsed = parse_code_command(text)
    except CodeCommandError as exc:
        console.print(f"[bold red]{exc}[/bold red]")
        return
    if parsed.action == "help":
        console.print(Panel(CODE_COMMAND_HELP, title="[bold blue]/code[/bold blue]"))
        return
    request = CodeRequest(
        action=parsed.action,
        objective=parsed.objective,
        targets=parsed.targets,
        include_tests=parsed.include_tests,
        template=parsed.template,
    )
    service_context = build_code_context(ctx.config, ctx.session.gateway)
    result = CodingApplicationService(".", service_context, ctx.config).execute(
        request, approver=ConsoleChangeApprover(parsed.assume_yes)
    )
    render_code_result(result)


def remember(text: str, ctx: Any) -> None:
    parts = text.strip().split(maxsplit=2)
    if len(parts) < 3:
        console.print("[bold red]Uso: /remember chave valor[/bold red]")
        return
    ctx.orchestrator.remember(parts[1], parts[2])
    console.print(f"[bold green]Lembrei:[/bold green] {parts[1]} = {parts[2]}")


def show_memory(_: str, ctx: Any) -> None:
    table = Table(title="Memória da Sessão")
    table.add_column("Seção", style="cyan")
    table.add_column("Conteúdo")
    for section, content in ctx.orchestrator.agent_state.memory.state.items():
        if content:
            table.add_row(section, str(content))
    console.print(table)


def show_events(_: str, ctx: Any) -> None:
    events = ctx.orchestrator.agent_state.events
    if not events:
        console.print("[yellow]Nenhum evento registrado.[/yellow]")
    for event in events:
        console.print(f"[dim]Passo {event['step']}:[/dim] {event['type']} {event['data']}")


def forget(_: str, ctx: Any) -> None:
    key = console.input("[bold cyan]Chave a esquecer:[/bold cyan] ").strip()
    ctx.orchestrator.forget(key)
    console.print(f"[bold green]Chave '{key}' removida (se existia).[/bold green]")


def clear_memory(_: str, ctx: Any) -> None:
    ctx.orchestrator.clear_memory()
    console.print("[bold green]Memória da sessão limpa.[/bold green]")


def _memory_path() -> str:
    entered = console.input(f"[bold cyan]Caminho (Enter para '{paths.MEMORY_FILE}'):[/bold cyan] ").strip()
    return str(entered or paths.MEMORY_FILE)


def save_memory(_: str, ctx: Any) -> None:
    console.print(f"[bold green]{ctx.orchestrator.save_memory_to_file(_memory_path())}[/bold green]")


def load_memory(_: str, ctx: Any) -> None:
    console.print(f"[bold green]{ctx.orchestrator.load_memory_from_file(_memory_path())}[/bold green]")


def doctor(_: str, __: Any) -> None:
    from agent.health_check import run_health_check
    run_health_check()


def _skill_result(ctx: Any, name: str, args: dict[str, Any], *, empty: str = "") -> None:
    skill = ctx.orchestrator.skills.get(name)
    if not skill:
        console.print(f"[red]Skill '{name}' não disponível.[/red]")
        return
    result = skill.execute(args)
    if not result.get("ok"):
        console.print(f"[red]Erro: {result.get('error', 'desconhecido')}[/red]")
        return
    console.print(result.get("data") or empty)


def list_files(_: str, ctx: Any) -> None:
    _skill_result(ctx, "directory_lister", {"path": "."}, empty="[yellow]Diretório vazio.[/yellow]")


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
        _skill_result(ctx, "grep", {"pattern": pattern, "path": "."}, empty="[yellow]Nenhuma ocorrência encontrada.[/yellow]")


def web_search(text: str, ctx: Any) -> None:
    query = _argument(text, "/search <consulta>")
    if query:
        _skill_result(ctx, "web_search", {"query": query})


def retry(_: str, ctx: Any) -> None:
    console.print("[bold yellow]Verificando checkpoint...[/bold yellow]")
    answer = ctx.orchestrator.run(None, stream_callback=None)
    console.print(Panel(answer, title="[bold blue]Agente[/bold blue]"))
    ctx.session.add_assistant_message(answer)
