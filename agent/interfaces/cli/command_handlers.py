from __future__ import annotations

from typing import Any, Callable

from rich.panel import Panel
from rich.table import Table

from agent.interfaces.cli.ui import ConsoleChangeApprover, console, render_code_result
from agent.interfaces.cli.workspace_entry import render_active_workspace, workspace_storage_path
from agent.interfaces.task_directives import (
    TaskDirectiveParseError,
    parse_task_request,
)
from agent.runtime.logging import set_debug_level
from agent.tools.authority import OperationalMode
from agent.tools.invocation_semantics import CODE_TASK_ACTIONS
from agent.tools.mode_enforcement import requests_test_execution

Handler = Callable[[str, Any], None]


def show_workspace(_: str, ctx: Any) -> None:
    render_active_workspace(console, ctx.workspace)


def mode_command(text: str, ctx: Any) -> None:
    parts = text.strip().split(maxsplit=1)
    if len(parts) == 1:
        mode = getattr(ctx.orchestrator, "operational_mode", None)
        label = mode.display_name if isinstance(mode, OperationalMode) else "FULL"
        console.print(
            f"Modo ativo: {label}\n\n"
            "Opções:\n"
            "  /modo read-only   Somente leitura; sem mutações\n"
            "  /modo editor      Leitura e edição controlada no workspace\n"
            "  /modo full        Usa toda a autoridade já concedida\n\n"
            "FULL continua sujeito a grants, approvals e confinement."
        )
        return
    if parts[1].strip().casefold() in {"help", "ajuda"}:
        mode_command("/modo", ctx)
        return
    mode = OperationalMode.parse(parts[1])
    if mode is None:
        console.print("[yellow]Uso: /modo [read-only|editor|full][/yellow]")
        return
    setter = getattr(ctx.orchestrator, "set_operational_mode", None)
    if not callable(setter):
        console.print("[red]Modos operacionais indisponíveis nesta sessão.[/red]")
        return
    setter(mode)
    console.print(f"Modo ativo: {mode.display_name}")


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


def _history_path(prompt: str, ctx: Any) -> str:
    default = workspace_storage_path(ctx, "chat_history_file", "chat_history.json")
    entered = console.input(f"[bold cyan]{prompt} (Enter para '{default}'):[/bold cyan] ").strip()
    return str(entered or default)


def save_history(_: str, ctx: Any) -> None:
    path = _history_path("Caminho do arquivo", ctx)
    success, error = ctx.session.save_to_file(path)
    console.print(f"[bold green]Histórico salvo em '{path}'.[/bold green]" if success else f"[bold red]Erro ao salvar: {error}[/bold red]")


def load_history(_: str, ctx: Any) -> None:
    path = _history_path("Caminho do arquivo", ctx)
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
        console.print(
            "Modo agente: unificado. Use /agent <objetivo> para abrir uma tarefa "
            "ou /agent /continue para retomar a tarefa anterior."
        )
        return
    interact = getattr(ctx.application, "interact", None)
    if callable(interact):
        try:
            result = interact(
                parts[1],
                boundary="task",
                visible_user_text=text,
                task_payload=parts[1],
            )
            answer = str(getattr(result, "answer", ""))
            console.print(Panel(answer, title="[bold blue]Agente[/bold blue]"))
        except KeyboardInterrupt:
            console.print("\n[bold red]Agente interrompido.[/bold red]")
        return
    try:
        request = parse_task_request(parts[1])
    except TaskDirectiveParseError as exc:
        console.print(f"[bold red]Erro [{exc.reason_code}]: {exc.detail}[/bold red]")
        return
    try:
        from agent.interfaces.cli.legacy_compat import dispatch_task_facade

        result = dispatch_task_facade(ctx, request)
        answer = getattr(result, "answer", result)
        answer_text = str(answer)
        console.print(Panel(answer_text, title="[bold blue]Agente[/bold blue]"))
        from agent.interfaces.cli.legacy_compat import append_legacy_answer

        append_legacy_answer(ctx, answer_text)
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
    if parsed.action in CODE_TASK_ACTIONS - {"analyze", "review"}:
        mode_allows = getattr(ctx.orchestrator, "mode_allows", None)
        if not callable(mode_allows) or not mode_allows({"write", "validate"}):
            console.print("[bold red]Ação negada pelo modo operacional ativo.[/bold red]")
            return
        mode = getattr(ctx.orchestrator, "operational_mode", None)
        if mode is not OperationalMode.FULL and requests_test_execution(
            {"include_tests": parsed.include_tests}
        ):
            console.print("[bold red]Execução de testes exige modo FULL.[/bold red]")
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
    workspace = getattr(ctx, "workspace", None)
    base_dir = workspace.root if workspace is not None else "."
    result = CodingApplicationService(base_dir, service_context, ctx.config).execute(
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


def _memory_path(ctx: Any) -> str:
    default = workspace_storage_path(ctx, "memory_file", "agent_memory.json")
    entered = console.input(f"[bold cyan]Caminho (Enter para '{default}'):[/bold cyan] ").strip()
    return str(entered or default)


def save_memory(_: str, ctx: Any) -> None:
    console.print(f"[bold green]{ctx.orchestrator.save_memory_to_file(_memory_path(ctx))}[/bold green]")


def load_memory(_: str, ctx: Any) -> None:
    console.print(f"[bold green]{ctx.orchestrator.load_memory_from_file(_memory_path(ctx))}[/bold green]")


def doctor(text: str, ctx: Any) -> None:
    from agent.health_check import run_health_check

    run_health_check(
        write_report="--write-report" in text.split(),
        verbose=True,
        app_paths=getattr(ctx, "app_paths", None),
        workspace=getattr(ctx, "workspace", None),
        config_path=getattr(ctx, "config_path", None),
        profile=getattr(ctx, "config", {}).get("default_model_profile"),
    )


def _skill_result(ctx: Any, name: str, args: dict[str, Any], *, empty: str = "") -> None:
    gateway = getattr(ctx.orchestrator, "tool_invocation_gateway", None)
    if gateway is not None:
        result = gateway.run(
            name,
            args,
            # Explicit slash commands select a fixed, public capability.  The
            # planner/persona visibility projection applies only to model
            # selection; passing the fresh-session empty projection here
            # would deny every otherwise-authorized explicit read/search.
            active_skills=None,
            allowed_capabilities=getattr(ctx.orchestrator, "allowed_capabilities", None),
        ).to_legacy_dict()
    else:
        console.print(f"[red]Skill '{name}' não disponível.[/red]")
        return
    if result.get("status") == "unavailable":
        console.print(f"[red]Skill '{name}' não disponível.[/red]")
        return
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


def retry(text: str, ctx: Any) -> None:
    console.print("[bold yellow]Verificando checkpoint...[/bold yellow]")
    interact = getattr(ctx.application, "interact", None)
    if callable(interact):
        result = interact(
            "/continue",
            boundary="task",
            visible_user_text=text or "/retry",
            task_payload="/continue",
        )
    else:
        from agent.interfaces.cli.legacy_compat import dispatch_task_facade

        result = dispatch_task_facade(ctx, parse_task_request("/continue"))
    answer = result.answer
    console.print(Panel(answer, title="[bold blue]Agente[/bold blue]"))
    if not callable(interact):
        from agent.interfaces.cli.legacy_compat import append_legacy_answer

        append_legacy_answer(ctx, answer)
