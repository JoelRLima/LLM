"""Command-line adapter for the standalone assistant."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence, cast

from agent.interfaces.cli import (
    first_run,
    interactive_admission,
    interactive_rendering,
    interactive_resources,
    interactive_session,
    workspace_entry,
)
from agent.interfaces.cli.parser import build_parser
from agent.interfaces.cli.ui import console
from agent.interfaces.task_directives import (
    ParsedTaskRequest,
    TaskDirectiveParseError,
    TaskRequestAction,
    parse_task_request,
)
from agent.runtime.config_errors import ConfigError, ConfigNotFound
from agent.runtime.task_directives import TaskRunDirective


def _sync_console() -> None:
    for module in (interactive_admission, interactive_rendering, interactive_resources, interactive_session):
        module.console = console  # type: ignore[attr-defined]
def _prompt(ctx: Any) -> str | None:
    _sync_console()
    return cast(str | None, interactive_rendering.prompt(ctx))
def _handle_input(text: str, ctx: Any) -> bool:
    _sync_console()
    return cast(bool, interactive_admission.handle_input(text, ctx))
def _context_from_application(application: Any, *, config_path: str | Path | None = None, shell: Any | None = None, controller: Any | None = None, approval_broker: Any | None = None, event_mailbox: Any | None = None, view_model: Any | None = None, query_service: Any | None = None, query_executor: Any | None = None) -> Any:
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
        shell=shell,
        controller=controller,
        approval_broker=approval_broker,
        event_mailbox=event_mailbox,
        view_model=view_model,
        query_service=query_service,
        query_executor=query_executor,
    )
def _chat_loop(ctx: Any) -> None:
    _sync_console()
    interactive_session.chat_loop(ctx, prompt_fn=_prompt, handle_input_fn=_handle_input)
def _value(args: argparse.Namespace, name: str, default: Any = None) -> Any:
    return getattr(args, name, default)
def _app_paths(args: argparse.Namespace) -> Any:
    from agent.runtime.paths import AppPaths
    return AppPaths.discover(app_home=_value(args, "home"))
def _create_application(args: argparse.Namespace, *, configure_logging: bool) -> Any:
    from agent.interfaces.cli.bootstrap import create_application
    return create_application(args, configure_logging=configure_logging)
def _run_application_task(application: Any, request: ParsedTaskRequest, *, visible_text: str | None = None) -> Any:
    """Call the typed application boundary without dropping W11 state."""
    directive = request.directive_state
    if not isinstance(directive, TaskRunDirective) or not isinstance(request.subject, str):
        raise ValueError("RUN requires a TaskRunDirective")
    interact = getattr(application, "interact", None)
    if callable(interact):
        surface = visible_text if visible_text is not None else request.subject
        return interact(request.subject, boundary="task", visible_user_text=surface, task_payload=surface)
    return application.run(request.subject, task_run_directive=directive)
def _run_chat(args: argparse.Namespace) -> int:
    _sync_console()
    if not first_run.is_interactive_terminal():
        raise first_run.InteractiveTTYRequiredError()
    return cast(int, interactive_session.run_chat(
        args,
        value=_value,
        app_paths=_app_paths,
        create_application=_create_application,
        context_from_application=_context_from_application,
        chat_loop_fn=_chat_loop,
    ))
def _print_json(document: Any) -> None:
    print(json.dumps(document, ensure_ascii=False, sort_keys=True))
def _print_operational_receipt(result: Any) -> None:
    from agent.interfaces.cli.operational_receipt import print_operational_receipt
    print_operational_receipt(result)
def _run_once(args: argparse.Namespace) -> int:
    json_output = bool(_value(args, "json_output", False))
    objective = " ".join(args.objective) if workspace_entry.require_task_workspace(args) else ""
    try:
        request = parse_task_request(objective)
    except TaskDirectiveParseError as exc:
        _emit_error(exc.detail, json_output=json_output, reason_code=exc.reason_code)
        return 2
    if request.action is TaskRequestAction.CONTINUE:
        from agent.interfaces.cli.task_continuity import run_task_resume
        return cast(
            int,
            run_task_resume(
                args,
                create_application=_create_application,
                print_json=_print_json,
                print_receipt=_print_operational_receipt,
            ),
        )
    application = _create_application(args, configure_logging=not json_output)
    try:
        result = _run_application_task(application, request, visible_text=objective)
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
    return cast(
        int,
        run_doctor(
            app_paths=_app_paths(args),
            workspace=workspace_entry.argument_workspace(args),
            config_path=_value(args, "config"),
            profile=_value(args, "profile"),
            json_output=json_output,
            write_report=bool(_value(args, "write_report", False)),
            online=bool(_value(args, "online", False)),
        ),
    )
def _config_repository(args: argparse.Namespace) -> Any:
    from agent.interfaces.cli.maintenance import config_repository
    return config_repository(_app_paths(args), _value(args, "config"))
def _run_config(args: argparse.Namespace) -> int:
    from agent.interfaces.cli.maintenance import run_config
    return cast(
        int,
        run_config(
            args, app_paths=_app_paths(args), config_path=_value(args, "config"), profile=_value(args, "profile")
        ),
    )
def _run_state(args: argparse.Namespace) -> int:
    from agent.interfaces.cli.maintenance import run_state
    return cast(int, run_state(args, app_paths=_app_paths(args), workspace=workspace_entry.argument_workspace(args)))
def _run_tools(args: argparse.Namespace) -> int:
    from agent.interfaces.cli.maintenance import run_tools
    return cast(int, run_tools(args, app_paths=_app_paths(args), workspace=workspace_entry.argument_workspace(args)))
def _run_extensions(args: argparse.Namespace) -> int:
    from agent.interfaces.cli.extensions import run_extensions
    return cast(
        int, run_extensions(args, app_paths=_app_paths(args), workspace=workspace_entry.argument_workspace(args))
    )
def _run_task_context(args: argparse.Namespace) -> int:
    from agent.interfaces.cli.task_context import run_task_context
    return cast(
        int,
        run_task_context(
            args, app_paths=_app_paths(args), workspace=workspace_entry.argument_workspace(args), print_json=_print_json
        ),
    )
def _run_inspect(args: argparse.Namespace) -> int:
    from agent.interfaces.cli.inspector import run_inspect
    return cast(int, run_inspect(args))
def _run_engineering(args: argparse.Namespace) -> int:
    """Bridge ``test`` to the neutral Engineering adapter."""
    from agent.engineering.cli import run_engineering_cli
    return cast(int, run_engineering_cli(args, print_json=_print_json))
def _run_mcp(args: argparse.Namespace) -> int:
    """Lazy, base-safe MCP entrypoint; the SDK is never imported by the parser."""
    from agent.interfaces.cli.mcp import run_mcp_engineering
    return cast(int, run_mcp_engineering(args))
def _run_commands(args: argparse.Namespace) -> int:
    from agent.discovery.contracts import DiscoveryResultV1
    from agent.engineering.cli import discovery_operation_views
    from agent.interfaces.cli.discovery_projection import discover_commands
    from agent.runtime.workspace_context import WorkspaceContext

    query = getattr(args, "query", "")
    semantic = bool(getattr(args, "semantic", False))
    paths = _app_paths(args)
    workspace = WorkspaceContext.create(workspace_entry.require_task_workspace(args)) if _value(args, "workspace") is not None else None
    result = cast(
        DiscoveryResultV1,
        discover_commands(
            query,
            app_paths=paths,
            workspace_bound=workspace is not None,
            engineering_views=discovery_operation_views(paths, workspace),
            semantic=semantic,
            semantic_allowed=semantic,
            config_path=_value(args, "config"),
            profile=_value(args, "profile"),
            home=_value(args, "home"),
        ),
    )
    payload = result.to_dict()
    if getattr(args, "json_output", False):
        _print_json(payload)
    else:
        candidates = cast(list[dict[str, object]], payload["candidates"])
        for index, item in enumerate(candidates, start=1):
            entry = cast(dict[str, object], item["entry"])
            suffix = "" if item["available"] else f" [{item['disabled_reason'] or 'unavailable'}]"
            print(f"{index}. {entry['preferred_invocation']} - {entry['description']}{suffix}")
    return 0
def _run_completion(args: argparse.Namespace) -> int:
    from agent.engineering.registry import production_registry
    from agent.interfaces.cli.completion import powershell_completion_script
    if getattr(args, "completion_command", None) == "powershell":
        operation_ids = tuple(item.operation_id for item in production_registry().descriptors())
        print(powershell_completion_script(engineering_operation_ids=operation_ids))
        return 0
    raise ValueError("completion command is required")
def _dispatch_task(args: argparse.Namespace) -> int:
    from agent.interfaces.cli.task_continuity import dispatch_task
    return cast(int, dispatch_task(args, run_context=_run_task_context))
def _dispatch(args: argparse.Namespace) -> int:
    if getattr(args, "command", None) == "task":
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
        "test": _run_engineering,
        "mcp": _run_mcp,
        "commands": _run_commands,
        "completion": _run_completion,
    }
    handler = handlers.get(command)
    if handler is None:
        raise ValueError(f"Comando desconhecido: {command}")
    return handler(args)
def _emit_error(message: str, *, json_output: bool, reason_code: str | None = None) -> None:
    if json_output:
        document: dict[str, Any] = {"error": message, "status": "failed", "success": False}
        if reason_code is not None:
            document["reason_code"] = reason_code
        _print_json(document)
    else:
        prefix = f"Erro [{reason_code}]: " if reason_code is not None else "Erro: "
        print(f"{prefix}{message}", file=sys.stderr)
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
            _emit_error(str(exc), json_output=json_output, reason_code=getattr(exc, "reason_code", None))
            return 2
        if isinstance(exc, (ConfigError, StateMigrationError)):
            _emit_error(str(exc), json_output=json_output)
            return 2
        _emit_error(f"{type(exc).__name__}: {exc}", json_output=json_output)
        return 1
if __name__ == "__main__":
    raise SystemExit(main())
