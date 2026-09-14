"""Resource ownership and teardown for the interactive session."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Any, Callable, cast

from agent.interfaces.cli import first_run, interactive_rendering, workspace_entry
from agent.interfaces.cli.attention import ApprovalBroker
from agent.interfaces.cli.controller import InteractiveExecutionController, WorkerSettlementTimeout
from agent.interfaces.cli.query_plane import BoundedQueryExecutor, ReadOnlyWorkspaceQueryService
from agent.interfaces.cli.ui import console
from agent.interfaces.cli.ui_plane import RuntimeEventUISink, RunViewModel, UIEventMailbox
from agent.runtime.config_errors import ConfigNotFound


@dataclass
class _SessionResources:
    controller: Any | None = None
    approval_broker: Any | None = None
    event_mailbox: Any | None = None
    view_model: Any | None = None
    query_service: Any | None = None
    query_executor: Any | None = None
    event_sink: Any | None = None
    event_dispatcher: Any | None = None


@dataclass(frozen=True, slots=True)
class ShutdownStatus:
    """Single typed owner for interactive shutdown truth at the CLI boundary."""

    settled: bool = True
    reason: str | None = None

    @classmethod
    def failed(cls, reason: str) -> "ShutdownStatus":
        return cls(False, reason)

    @property
    def exit_code(self) -> int:
        return 0 if self.settled else 1


def get_shell(
    shell_holder: dict[str, Any],
    view_holder: dict[str, Any],
    application_holder: dict[str, Any],
    controller_holder: dict[str, Any],
) -> Any:
    if shell_holder["shell"] is None:
        from agent.interfaces.cli.interactive_shell import InteractiveShell
        from agent.interfaces.cli.manifest import DEFAULT_COMMAND_REGISTRY

        def toolbar() -> str:
            view = view_holder["view"]
            if view is not None:
                return str(view.render_toolbar(
                    width=120,
                    mode=getattr(getattr(application_holder.get("application"), "orchestrator", None), "operational_mode_label", "FULL"),
                ))
            controller = controller_holder["controller"]
            return f"state={controller.state.value}" if controller is not None else "state=STARTING"

        shell_holder["shell"] = InteractiveShell(registry=DEFAULT_COMMAND_REGISTRY, toolbar=toolbar)
    return shell_holder["shell"]


def prompt_line(get_shell: Callable[[], Any], enabled: bool) -> Callable[..., str] | None:
    if not enabled:
        return None

    def read_prompt(message: str, default: str = "") -> str:
        value = get_shell().prompt_line(message, default=default)
        return "" if value is None else str(value)

    return read_prompt


def recover_missing_config(
    error: ConfigNotFound,
    args: argparse.Namespace,
    *,
    value: Callable[..., Any],
    interactive: bool,
    shell_enabled: bool,
    shell_holder: dict[str, Any],
    console_prompt: Callable[..., str] | None,
    app_paths: Callable[[argparse.Namespace], Any],
) -> tuple[bool, int]:
    if value(args, "config") is not None or not interactive:
        raise error
    result = cast(
        int,
        first_run.recover_first_run_config(
            args,
            console=console,
            app_paths=app_paths(args),
            prompt=console_prompt,
        ),
    )
    shell = shell_holder["shell"]
    if shell is not None:
        shell.close()
    guided = shell_enabled and bool(getattr(args, "_first_run_guided", False))
    return guided, result


def configure(
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
) -> tuple[Any, _SessionResources]:
    resources = _SessionResources(controller=InteractiveExecutionController() if interactive else None)
    controller_holder["controller"] = resources.controller
    application_holder["application"] = application
    if interactive:
        resources.approval_broker = ApprovalBroker()
        application.approval_policy = resources.approval_broker
        gateway = getattr(application, "tool_invocation_gateway", None)
        if gateway is not None:
            gateway.approval_port = resources.approval_broker
        resources.event_mailbox = UIEventMailbox()
        resources.view_model = RunViewModel()
        workspace_value = getattr(application.workspace, "root", application.workspace)
        resources.query_service = ReadOnlyWorkspaceQueryService(workspace_value)
        resources.query_executor = BoundedQueryExecutor(workspace_id=str(getattr(application.workspace, "workspace_id", "workspace")))
        resources.event_sink = RuntimeEventUISink(resources.event_mailbox)
        resources.event_dispatcher = getattr(application.orchestrator, "event_dispatcher", None)
        if resources.event_dispatcher is not None:
            resources.event_dispatcher.add_sink(resources.event_sink)
        view_holder["view"] = resources.view_model
    context = context_from_application(
        application,
        config_path=value(args, "config"),
        shell=get_shell() if shell_enabled else None,
        controller=resources.controller,
        approval_broker=resources.approval_broker,
        event_mailbox=resources.event_mailbox,
        view_model=resources.view_model,
        query_service=resources.query_service,
        query_executor=resources.query_executor,
    )
    if interactive:
        workspace_entry.remember_workspace(application.paths, context.workspace.root)
    return context, resources


def _settle_query(
    resources: _SessionResources,
    context: Any,
    active_shell: Any,
    timeout_seconds: float | None,
) -> ShutdownStatus:
    executor = resources.query_executor
    if executor is None:
        return ShutdownStatus()
    if timeout_seconds is None:
        result = executor.cancel_and_wait()
    else:
        result = executor.cancel_and_wait(timeout_seconds=timeout_seconds)
    if result is None:
        return ShutdownStatus()
    if active_shell is not None and context is not None:
        interactive_rendering.render_query_result(context, result)
    if getattr(result, "error", None) == "QUERY_SHUTDOWN_TIMEOUT":
        return ShutdownStatus.failed("QUERY_SHUTDOWN_TIMEOUT")
    return ShutdownStatus()


def _settle_controller(
    resources: _SessionResources,
    context: Any,
    active_shell: Any,
    timeout_seconds: float | None,
) -> ShutdownStatus:
    controller = resources.controller
    if controller is None:
        return ShutdownStatus()
    if timeout_seconds is None:
        message = controller.shutdown()
    else:
        message = controller.shutdown(timeout_seconds=timeout_seconds)
    if message is None:
        return ShutdownStatus()
    if active_shell is not None and context is not None:
        interactive_rendering.render_worker_message(context, message)
    if isinstance(getattr(message, "error", None), WorkerSettlementTimeout):
        return ShutdownStatus.failed("WORKER_SHUTDOWN_TIMEOUT")
    return ShutdownStatus()


def _close_settled_resources(
    resources: _SessionResources,
    active_shell: Any,
    application: Any,
) -> None:
    # cancel_and_wait()/poll() have completed the bounded query settlement and
    # discarded any result from a stale workspace generation.  Keep the UI
    # sink attached until that barrier so late canonical events remain
    # observable; detach it immediately before application teardown.
    if resources.event_dispatcher is not None and resources.event_sink is not None:
        resources.event_dispatcher.remove_sink(resources.event_sink)

    # The canonical application owns the runtime lock.  Close it before
    # releasing the prompt resources, and only after worker/query settlement.
    application.close()
    if active_shell is not None:
        active_shell.close()


def settle(
    resources: _SessionResources,
    context: Any,
    active_shell: Any,
    application: Any,
    *,
    timeout_seconds: float | None = None,
) -> ShutdownStatus:
    if resources.approval_broker is not None:
        resources.approval_broker.shutdown()

    status = _settle_query(resources, context, active_shell, timeout_seconds)
    controller_status = _settle_controller(resources, context, active_shell, timeout_seconds)
    if not controller_status.settled:
        status = controller_status
    if not status.settled:
        if active_shell is not None:
            active_shell.print_background("[interactive] shutdown não concluído; operação ativa permanece em settlement")
        return status

    _close_settled_resources(resources, active_shell, application)
    return status


__all__ = [
    "ShutdownStatus",
    "_SessionResources",
    "configure",
    "get_shell",
    "prompt_line",
    "recover_missing_config",
    "settle",
]
