"""Command-line adapter for the standalone assistant."""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Sequence, cast

from llm_agent.application.configuration_errors import ConfigurationError, ConfigurationNotFound
from llm_agent.application.task_directives import (
    TaskDirectiveParseError,
)
from llm_agent.application.task_execution import (
    TaskDispatch,
    TaskExecutionRuntime,
    TaskSettlement,
    close_task_execution,
    execute_submission,
    observe_task_settlement,
    prepare_task_dispatch,
)
from llm_agent.interfaces.cli import (
    first_run,
    interactive_admission,
    interactive_rendering,
    interactive_resources,
    interactive_session,
    workspace_entry,
)
from llm_agent.interfaces.cli.parser import build_parser
from llm_agent.interfaces.cli.ui import console


def _sync_console() -> None:
    for module in (interactive_admission, interactive_rendering, interactive_resources, interactive_session):
        module.console = console  # type: ignore[attr-defined,union-attr]
def _prompt(ctx: Any) -> str | None:
    _sync_console()
    return cast(str | None, interactive_rendering.prompt(ctx))
def _handle_input(text: str, ctx: Any) -> bool:
    _sync_console()
    return cast(bool, interactive_admission.handle_input(text, ctx))
def _chat_loop(ctx: Any) -> None:
    _sync_console()
    interactive_session.chat_loop(ctx, prompt_fn=_prompt, handle_input_fn=_handle_input)
def _value(args: argparse.Namespace, name: str, default: Any = None) -> Any:
    return getattr(args, name, default)
def _app_paths(args: argparse.Namespace) -> Any:
    from llm_agent.application.context import AppPaths
    return AppPaths.discover(app_home=_value(args, "home"))
def _create_application(args: argparse.Namespace, *, configure_logging: bool) -> Any:
    from llm_agent.interfaces.cli.bootstrap import create_application
    return create_application(args, configure_logging=configure_logging)
def _run_application_task(application: TaskExecutionRuntime, request: TaskDispatch, *, visible_text: str | None = None) -> TaskSettlement:
    return execute_submission(application, visible_text or "", entry="headless-run", dispatch=request, visible_text=visible_text)

def _run_chat(args: argparse.Namespace) -> int:
    _sync_console()
    if not first_run.is_interactive_terminal():
        raise first_run.InteractiveTTYRequiredError()
    from llm_agent.interfaces.cli.bootstrap import context_from_application

    return cast(int, interactive_session.run_chat(
        args,
        value=_value,
        app_paths=_app_paths,
        create_application=_create_application,
        context_from_application=context_from_application,
        chat_loop_fn=_chat_loop,
    ))
def _print_json(document: Any) -> None:
    print(json.dumps(document, ensure_ascii=False, sort_keys=True))
def _print_operational_receipt(result: Any) -> None:
    from llm_agent.interfaces.cli.operational_receipt import print_operational_receipt
    print_operational_receipt(result)
def _run_once(args: argparse.Namespace) -> int:
    json_output = bool(_value(args, "json_output", False))
    objective = " ".join(args.objective) if workspace_entry.require_task_workspace(args) else ""
    try:
        request = prepare_task_dispatch(objective)
    except TaskDirectiveParseError as exc:
        _emit_error(exc.detail, json_output=json_output, reason_code=exc.reason_code)
        return 2
    if request.continues_task:
        from llm_agent.interfaces.cli.task_continuity import run_task_resume
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
        settlement = _run_application_task(application, request, visible_text=objective)
    finally:
        close_task_execution(application)
    result = observe_task_settlement(settlement, channel="headless")
    if json_output:
        _print_json(result)
    elif result["success"]:
        print(result["answer"])
        _print_operational_receipt(result)
    elif result.get("receipt"):
        if result["answer"]:
            print(result["answer"])
        _print_operational_receipt(result)
        if result["error"]:
            print(result["error"], file=sys.stderr)
    else:
        print(result["error"] or result["answer"] or "A tarefa falhou.", file=sys.stderr)
    return 0 if result["success"] else 1
def _run_doctor(args: argparse.Namespace) -> int:
    from llm_agent.interfaces.cli.maintenance import run_doctor
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
def _run_config(args: argparse.Namespace) -> int:
    from llm_agent.interfaces.cli.maintenance import run_config
    return cast(
        int,
        run_config(
            args, app_paths=_app_paths(args), config_path=_value(args, "config"), profile=_value(args, "profile")
        ),
    )
def _run_state(args: argparse.Namespace) -> int:
    from llm_agent.interfaces.cli.maintenance import run_state
    return cast(int, run_state(args, app_paths=_app_paths(args), workspace=workspace_entry.argument_workspace(args)))
def _run_tools(args: argparse.Namespace) -> int:
    from llm_agent.interfaces.cli.maintenance import run_tools
    return cast(int, run_tools(args, app_paths=_app_paths(args), workspace=workspace_entry.argument_workspace(args)))
def _run_extensions(args: argparse.Namespace) -> int:
    from llm_agent.interfaces.cli.extensions import run_extensions
    return cast(
        int, run_extensions(args, app_paths=_app_paths(args), workspace=workspace_entry.argument_workspace(args))
    )
def _run_task_context(args: argparse.Namespace) -> int:
    from llm_agent.interfaces.cli.task_context import run_task_context
    return cast(
        int,
        run_task_context(
            args, app_paths=_app_paths(args), workspace=workspace_entry.argument_workspace(args), print_json=_print_json
        ),
    )
def _run_inspect(args: argparse.Namespace) -> int:
    from llm_agent.interfaces.cli.inspector import run_inspect
    return cast(int, run_inspect(args))
def _run_engineering(args: argparse.Namespace) -> int:
    """Bridge ``test`` to the neutral Engineering adapter."""
    from llm_agent.interfaces.cli.engineering import run_engineering_cli
    return cast(int, run_engineering_cli(args, print_json=_print_json))
def _run_mcp(args: argparse.Namespace) -> int:
    """Lazy, base-safe MCP entrypoint; the SDK is never imported by the parser."""
    from llm_agent.interfaces.cli.mcp import run_mcp_engineering
    return cast(int, run_mcp_engineering(args))
def _run_commands(args: argparse.Namespace) -> int:
    from llm_agent.application.context import WorkspaceContext
    from llm_agent.discovery.contracts import DiscoveryResultV1
    from llm_agent.interfaces.cli.discovery_projection import discover_commands
    from llm_agent.interfaces.cli.engineering import discovery_operation_views

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
    from llm_agent.application.agent_boundary import production_registry
    from llm_agent.interfaces.cli.completion import powershell_completion_script
    if getattr(args, "completion_command", None) == "powershell":
        operation_ids = tuple(item.operation_id for item in production_registry().descriptors())
        print(powershell_completion_script(engineering_operation_ids=operation_ids))
        return 0
    raise ValueError("completion command is required")
def _dispatch_task(args: argparse.Namespace) -> int:
    from llm_agent.interfaces.cli.task_continuity import dispatch_task
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
        from llm_agent.application.agent_boundary import (
            TraceCorruptError,
            TraceUnavailableError,
        )
        from llm_agent.application.inspection import (
            InspectionCorruptDataError,
            InspectionUnavailableError,
        )
        from llm_agent.application.state_migration import StateMigrationFailedError
        from llm_agent.application.task_context import TaskContextReadError
        if isinstance(exc, ConfigurationNotFound):
            _emit_error(first_run.actionable_missing_config(args, exc), json_output=json_output)
            return 2
        if isinstance(exc, TaskContextReadError):
            _emit_error(str(exc), json_output=json_output)
            return 2
        if isinstance(
            exc,
            (
                InspectionUnavailableError,
                InspectionCorruptDataError,
                TraceUnavailableError,
                TraceCorruptError,
            ),
        ):
            _emit_error(str(exc), json_output=json_output)
            return 2
        if isinstance(exc, (FileNotFoundError, NotADirectoryError, PermissionError, ValueError)):
            _emit_error(str(exc), json_output=json_output, reason_code=getattr(exc, "reason_code", None))
            return 2
        if isinstance(exc, StateMigrationFailedError):
            _emit_error(str(exc), json_output=json_output)
            return 2
        if isinstance(exc, ConfigurationError):
            _emit_error(str(exc), json_output=json_output)
            return 2
        _emit_error(f"{type(exc).__name__}: {exc}", json_output=json_output)
        return 1
if __name__ == "__main__":
    raise SystemExit(main())
