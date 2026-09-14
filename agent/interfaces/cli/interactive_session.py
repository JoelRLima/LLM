"""Interactive session lifecycle with one worker/query ownership boundary."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from agent.interfaces.cli import (
    first_run,
    interactive_admission,
    interactive_rendering,
    interactive_resources,
    workspace_entry,
)
from agent.interfaces.cli.ui import console
from agent.runtime.config_errors import ConfigNotFound


@dataclass(frozen=True, slots=True)
class InteractiveSessionResult:
    rebootstrap_workspace: Path | None
    rebootstrap_profile: str | None
    shutdown: interactive_resources.ShutdownStatus


def _refresh_view(ctx: Any) -> None:
    mailbox = getattr(ctx, "event_mailbox", None)
    view_model = getattr(ctx, "view_model", None)
    if mailbox is None or view_model is None:
        return
    for envelope in mailbox.drain():
        view_model.apply(envelope.event)
    controller = getattr(ctx, "controller", None)
    if controller is not None:
        view_model.set_pending_count(len(controller.pending.list()))


def _poll_outputs(ctx: Any) -> None:
    controller = getattr(ctx, "controller", None)
    if controller is not None:
        interactive_rendering.drain_worker_stream(ctx)
        message = controller.poll_result()
        if message is not None:
            interactive_rendering.render_worker_message(ctx, message)
    query_executor = getattr(ctx, "query_executor", None)
    if query_executor is not None:
        result = query_executor.poll()
        if result is not None:
            interactive_rendering.render_query_result(ctx, result)


def _clear_draft_if_present(ctx: Any, shell: Any) -> bool:
    if shell is None:
        return False
    current_draft = shell.current_draft() if callable(getattr(shell, "current_draft", None)) else ""
    if not current_draft:
        return False
    if callable(getattr(shell, "clear_draft", None)):
        shell.clear_draft()
    ctx.draft_text = ""
    shell.print_background("[interactive] rascunho limpo")
    return True


def _cancel_controller_if_busy(ctx: Any, shell: Any) -> bool:
    controller = getattr(ctx, "controller", None)
    if controller is None or not controller.is_busy():
        return False
    outcome = controller.request_cancel()
    if shell is not None:
        shell.print_background(f"[interactive] {outcome.disposition.lower()}; aguardando settlement")
    return True


def _cancel_attention_if_active(ctx: Any, shell: Any) -> bool:
    broker = getattr(ctx, "approval_broker", None)
    if broker is None or not callable(getattr(broker, "current", None)):
        return False
    current = broker.current()
    if current is None:
        return False
    broker.invalidate(attention_id=current.identity.attention_id, generation=current.identity.run_generation)
    if shell is not None:
        shell.print_background("[interactive] atenção cancelada")
    return True


def _cancel_query_if_busy(ctx: Any, shell: Any) -> None:
    query_executor = getattr(ctx, "query_executor", None)
    if query_executor is None or not query_executor.is_busy():
        return
    request_cancel = getattr(query_executor, "request_cancel", None)
    if callable(request_cancel):
        request_cancel()
    if shell is not None:
        shell.print_background("[interactive] query cancelada; aguardando settlement")


def _handle_ctrl_c(ctx: Any) -> None:
    """Context-sensitive composer escape/cancel without shutting down chat."""

    shell = getattr(ctx, "shell", None)
    if _clear_draft_if_present(ctx, shell):
        return
    if _cancel_controller_if_busy(ctx, shell):
        return

    if _cancel_attention_if_active(ctx, shell):
        return

    _cancel_query_if_busy(ctx, shell)


def _close_settled(ctx: Any) -> None:
    query_executor = getattr(ctx, "query_executor", None)
    if query_executor is not None:
        result = query_executor.cancel_and_wait()
        if result is not None:
            interactive_rendering.render_query_result(ctx, result)
    controller = getattr(ctx, "controller", None)
    if controller is not None:
        message = controller.shutdown()
        if message is not None:
            interactive_rendering.render_worker_message(ctx, message)


def chat_loop(
    ctx: Any,
    *,
    prompt_fn: Callable[[Any], str | None] | None = None,
    handle_input_fn: Callable[[str, Any], bool] | None = None,
    confirm_exit_fn: Callable[[Any], bool] | None = None,
) -> None:
    from agent.interfaces.cli import turn_rendering

    prompt_reader = prompt_fn or interactive_rendering.prompt
    input_handler = handle_input_fn or interactive_admission.handle_input
    exit_confirmer = confirm_exit_fn or interactive_admission.confirm_exit
    shell = getattr(ctx, "shell", None)
    if shell is not None:
        set_pump = getattr(shell, "set_background_pump", None)
        if callable(set_pump):
            def background_pump() -> None:
                _refresh_view(ctx)
                _poll_outputs(ctx)
            set_pump(background_pump)
        set_interrupt = getattr(shell, "set_interrupt_handler", None)
        if callable(set_interrupt):
            set_interrupt(lambda: _handle_ctrl_c(ctx))
    turn_rendering.render_startup_status(console, ctx)
    while True:
        _refresh_view(ctx)
        _poll_outputs(ctx)
        text = prompt_reader(ctx)
        if text is None:
            _close_settled(ctx)
            return
        if text.strip() and input_handler(text, ctx):
            if exit_confirmer(ctx):
                _close_settled(ctx)
                return
            continue
        if getattr(ctx, "rebootstrap_workspace", None) is not None or getattr(ctx, "rebootstrap_profile", None) is not None:
            _close_settled(ctx)
            return


def _run_application_session(
    application: Any,
    args: argparse.Namespace,
    *,
    interactive: bool,
    shell_enabled: bool,
    get_shell: Callable[[], Any],
    value: Callable[..., Any],
    context_from_application: Callable[..., Any],
    controller_holder: dict[str, Any],
    application_holder: dict[str, Any],
    view_holder: dict[str, Any],
    chat_loop_fn: Callable[[Any], None] | None = None,
) -> InteractiveSessionResult:
    context: Any = None
    resources = interactive_resources._SessionResources()
    shutdown = interactive_resources.ShutdownStatus()
    try:
        context, resources = interactive_resources.configure(
            application,
            args,
            interactive=interactive,
            shell_enabled=shell_enabled,
            get_shell=get_shell,
            value=value,
            context_from_application=context_from_application,
            controller_holder=controller_holder,
            application_holder=application_holder,
            view_holder=view_holder,
        )
        (chat_loop_fn or chat_loop)(context)
        rebootstrap_workspace = getattr(context, "rebootstrap_workspace", None)
        rebootstrap_profile = getattr(context, "rebootstrap_profile", None)
    finally:
        shutdown = interactive_resources.settle(
            resources,
            context,
            get_shell() if shell_enabled else None,
            application,
        )
    return InteractiveSessionResult(rebootstrap_workspace, rebootstrap_profile, shutdown)


def run_chat(
    args: argparse.Namespace,
    *,
    value: Callable[..., Any],
    app_paths: Callable[[argparse.Namespace], Any],
    create_application: Callable[..., Any],
    context_from_application: Callable[..., Any],
    chat_loop_fn: Callable[[Any], None] | None = None,
) -> int:
    if not first_run.is_interactive_terminal():
        raise first_run.InteractiveTTYRequiredError()
    real_tty = bool(getattr(sys.stdin, "isatty", lambda: False)() and getattr(sys.stdout, "isatty", lambda: False)())
    interactive = getattr(args, "workspace", None) is None or workspace_entry.require_task_workspace(args) is not None
    shell_enabled = interactive and real_tty
    shell_holder: dict[str, Any] = {"shell": None}
    controller_holder: dict[str, Any] = {"controller": None}
    view_holder: dict[str, Any] = {"view": None}
    application_holder: dict[str, Any] = {"application": None}

    def get_shell() -> Any:
        return interactive_resources.get_shell(shell_holder, view_holder, application_holder, controller_holder)

    prompt_line = interactive_resources.prompt_line(get_shell, shell_enabled)
    first_run.prepare_chat_workspace(args, console=console, app_paths=app_paths(args), prompt=prompt_line)
    try:
        application = create_application(args, configure_logging=True)
    except ConfigNotFound as error:
        guided, result = interactive_resources.recover_missing_config(
            error,
            args,
            value=value,
            interactive=interactive,
            shell_enabled=shell_enabled,
            shell_holder=shell_holder,
            console_prompt=prompt_line,
            app_paths=app_paths,
        )
        if guided:
            return run_chat(
                args,
                value=value,
                app_paths=app_paths,
                create_application=create_application,
                context_from_application=context_from_application,
                chat_loop_fn=chat_loop_fn,
            )
        return result
    session_result = _run_application_session(
        application,
        args,
        interactive=interactive,
        shell_enabled=shell_enabled,
        get_shell=get_shell,
        value=value,
        context_from_application=context_from_application,
        controller_holder=controller_holder,
        application_holder=application_holder,
        view_holder=view_holder,
        chat_loop_fn=chat_loop_fn,
    )
    if not session_result.shutdown.settled:
        return session_result.shutdown.exit_code
    rebootstrap_workspace = session_result.rebootstrap_workspace
    rebootstrap_profile = session_result.rebootstrap_profile
    if rebootstrap_workspace is not None:
        args.workspace = str(rebootstrap_workspace)
        return run_chat(
            args,
            value=value,
            app_paths=app_paths,
            create_application=create_application,
            context_from_application=context_from_application,
            chat_loop_fn=chat_loop_fn,
        )
    if rebootstrap_profile is not None:
        args.profile = rebootstrap_profile
        return run_chat(
            args,
            value=value,
            app_paths=app_paths,
            create_application=create_application,
            context_from_application=context_from_application,
            chat_loop_fn=chat_loop_fn,
        )
    return 0

__all__ = ["InteractiveSessionResult", "chat_loop", "run_chat"]
