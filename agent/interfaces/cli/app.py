"""Command-line adapter for the standalone assistant."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from rich.console import Console

from agent.interfaces.cli import first_run
from agent.interfaces.cli.parser import build_parser
from agent.interfaces.cli.workspace_entry import argument_workspace, remember_workspace, render_active_workspace
from agent.runtime.config_errors import ConfigError, ConfigNotFound

console = Console()
NIVEIS_THINKING = {512: "BAIXO", 1024: "MÉDIO", 2048: "ALTO"}
def obter_status_think(session: Any) -> str:
    if session.thinking_budget > 0:
        level = NIVEIS_THINKING.get(session.thinking_budget, "?")
        return f"[green]LIGADO ({level}, {session.thinking_budget})[/green]"
    return "[yellow]OFF[/yellow]"


def _prompt(ctx: Any) -> str | None:
    thinking = obter_status_think(ctx.session)
    diagnostic = ("", " [yellow][DIAG NORMAL][/yellow]", " [yellow][DIAG VERBOSE][/yellow]")[ctx.modo_diagnostico]
    agent = " [green][AGENTE][/green]" if ctx.modo_agente else ""
    mode = getattr(ctx.orchestrator, "operational_mode_label", "FULL")
    try:
        return str(console.input(f"\n[cyan][Pensar: {thinking}][/cyan] [{mode}]{diagnostic}{agent} > "))
    except (EOFError, KeyboardInterrupt):
        console.print("\n[bold yellow]Encerrando...[/bold yellow]")
        return None


def _handle_input(text: str, ctx: Any) -> bool:
    from agent.interfaces.cli.chat import run_agent_turn, run_chat_turn
    from agent.interfaces.cli.commands import handle_command

    handled, should_exit = handle_command(text, ctx)
    if handled:
        return bool(should_exit)
    if ctx.modo_agente and not text.startswith("/"):
        run_agent_turn(console, ctx, text)
    else:
        run_chat_turn(console, ctx.session, text, ctx.modo_diagnostico)
    return False


def _context_from_application(application: Any, *, config_path: str | Path | None = None) -> Any:
    from agent.interfaces.cli.commands import CommandContext

    return CommandContext(
        application.session,
        application.orchestrator,
        application.config,
        application=application,
        app_paths=application.paths,
        workspace=application.workspace,
        workspace_paths=application.workspace_paths,
        config_path=config_path,
    )


def _chat_loop(ctx: Any) -> None:
    from agent.interfaces.cli.commands import exibir_menu

    console.rule("[bold cyan]=== CHAT INICIADO ===[/bold cyan]")
    exibir_menu()
    while True:
        text = _prompt(ctx)
        if text is None:
            return
        if text.strip() and _handle_input(text, ctx):
            return


def _value(args: argparse.Namespace, name: str, default: Any = None) -> Any:
    return getattr(args, name, default)


def _app_paths(args: argparse.Namespace) -> Any:
    from agent.runtime.paths import AppPaths

    return AppPaths.discover(app_home=_value(args, "home"))


def _create_application(args: argparse.Namespace, *, configure_logging: bool) -> Any:
    from agent.interfaces.cli.bootstrap import create_application

    return create_application(args, configure_logging=configure_logging)


def _run_chat(args: argparse.Namespace) -> int:
    first_run.prepare_chat_workspace(args, console=console, app_paths=_app_paths(args))
    interactive = first_run.is_interactive_terminal()
    try:
        application = _create_application(args, configure_logging=True)
    except ConfigNotFound:
        if _value(args, "config") is None and interactive:
            return first_run.recover_first_run_config(args, console=console, app_paths=_app_paths(args))
        raise
    try:
        context = _context_from_application(
            application,
            config_path=_value(args, "config"),
        )
        if interactive:
            remember_workspace(application.paths, context.workspace.root)
            render_active_workspace(console, context.workspace, show_mode_hint=True)
        _chat_loop(context)
    finally:
        application.close()
    return 0
def _print_json(document: Any) -> None:
    print(json.dumps(document, ensure_ascii=False, sort_keys=True))


def _print_operational_receipt(result: Any) -> None:
    from agent.interfaces.cli.operational_receipt import print_operational_receipt

    print_operational_receipt(result)


def _run_once(args: argparse.Namespace) -> int:
    json_output = bool(_value(args, "json_output", False))
    application = _create_application(args, configure_logging=not json_output)
    try:
        result = application.run(" ".join(args.objective))
    finally:
        application.close()

    if json_output:
        _print_json(result.to_dict())
    elif result.success:
        print(result.answer)
        _print_operational_receipt(result)
    elif getattr(result, "receipt", None):
        if result.answer:
            print(result.answer)
        _print_operational_receipt(result)
        if result.error:
            print(result.error, file=sys.stderr)
    else:
        print(result.error or result.answer or "A tarefa falhou.", file=sys.stderr)
    return 0 if result.success else 1


def _run_doctor(args: argparse.Namespace) -> int:
    from agent.interfaces.cli.maintenance import run_doctor

    json_output = bool(_value(args, "json_output", False))
    return run_doctor(
        app_paths=_app_paths(args),
        workspace=argument_workspace(args),
        config_path=_value(args, "config"),
        profile=_value(args, "profile"),
        json_output=json_output,
        write_report=bool(_value(args, "write_report", False)),
    )


def _config_repository(args: argparse.Namespace) -> Any:
    from agent.interfaces.cli.maintenance import config_repository

    return config_repository(_app_paths(args), _value(args, "config"))


def _run_config(args: argparse.Namespace) -> int:
    from agent.interfaces.cli.maintenance import run_config

    return run_config(
        args,
        app_paths=_app_paths(args),
        config_path=_value(args, "config"),
        profile=_value(args, "profile"),
    )


def _run_state(args: argparse.Namespace) -> int:
    from agent.interfaces.cli.maintenance import run_state

    return run_state(
        args,
        app_paths=_app_paths(args),
        workspace=argument_workspace(args),
    )


def _run_tools(args: argparse.Namespace) -> int:
    from agent.interfaces.cli.maintenance import run_tools

    return run_tools(
        args,
        app_paths=_app_paths(args),
        workspace=argument_workspace(args),
    )


def _run_extensions(args: argparse.Namespace) -> int:
    from agent.interfaces.cli.extensions import run_extensions

    return run_extensions(
        args,
        app_paths=_app_paths(args),
        workspace=argument_workspace(args),
    )


def _run_task_context(args: argparse.Namespace) -> int:
    from agent.interfaces.cli.task_context import run_task_context

    return run_task_context(
        args,
        app_paths=_app_paths(args),
        workspace=argument_workspace(args),
        print_json=_print_json,
    )


def _run_inspect(args: argparse.Namespace) -> int:
    from agent.interfaces.cli.inspector import run_inspect

    return run_inspect(args)


def _dispatch_task(args: argparse.Namespace) -> int:
    from agent.interfaces.cli.task_continuity import dispatch_task

    return dispatch_task(args, run_context=_run_task_context)


def _dispatch(args: argparse.Namespace) -> int:
    if getattr(args, 'command', None) == 'task':
        return _dispatch_task(args)
    command = args.command or "chat"
    handlers = {
        "chat": _run_chat,
        "run": _run_once,
        "doctor": _run_doctor,
        "config": _run_config,
        "state": _run_state,
        "tools": _run_tools,
        "extensions": _run_extensions,
        "inspect": _run_inspect,
    }
    handler = handlers.get(command)
    if handler is None:
        raise ValueError(f"Comando desconhecido: {command}")
    return handler(args)


def _emit_error(message: str, *, json_output: bool) -> None:
    if json_output:
        _print_json({"error": message, "status": "failed", "success": False})
    else:
        print(f"Erro: {message}", file=sys.stderr)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return a process exit code."""

    parser = build_parser()
    args = parser.parse_args(argv)
    json_output = bool(_value(args, "json_output", False))
    try:
        return _dispatch(args)
    except KeyboardInterrupt:
        _emit_error("Operação cancelada pelo usuário.", json_output=json_output)
        return 1
    except Exception as exc:
        from agent.observability.trace_store import TraceCorruptError, TraceUnavailableError
        from agent.runtime.state_migration import StateMigrationError
        from agent.task_definition.errors import TaskDefinitionError

        if isinstance(exc, ConfigNotFound):
            _emit_error(first_run.actionable_missing_config(args, exc), json_output=json_output)
            return 2
        if isinstance(exc, TaskDefinitionError):
            _emit_error(str(exc), json_output=json_output)
            return 2
        if isinstance(exc, (TraceUnavailableError, TraceCorruptError)):
            _emit_error(str(exc), json_output=json_output)
            return 2
        if isinstance(exc, (FileNotFoundError, NotADirectoryError, PermissionError, ValueError)):
            _emit_error(str(exc), json_output=json_output)
            return 2
        if isinstance(exc, (ConfigError, StateMigrationError)):
            _emit_error(str(exc), json_output=json_output)
            return 2
        _emit_error(f"{type(exc).__name__}: {exc}", json_output=json_output)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
