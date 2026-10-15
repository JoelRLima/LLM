"""Finite task and session use cases over one privately retained Agent runtime.

Approval/event collaborators are temporary opaque composition inputs until their
own cohorts. Settlements retain Agent results privately until C11; observations
are copied, validated primitive values already consumed by the CLI.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import TYPE_CHECKING, Any, Literal, cast

from llm_agent.agent import approval as _agent_approval
from llm_agent.agent.application import AgentApplication as _AgentApplication
from llm_agent.agent.approval import RequireExplicitApproval as _RequireExplicitApproval
from llm_agent.agent.interaction.task_directives import parse_task_request as _parse_task_request
from llm_agent.agent.interaction.types import ParsedTaskRequest as _ParsedTaskRequest
from llm_agent.agent.tools.authority import OperationalMode as _OperationalMode
from llm_agent.application import _task_activity, _task_commands, _task_observation, _task_submission
from llm_agent.application.approval import ApprovalPresenter, _bind_approval_presenter
from llm_agent.application.configuration_errors import translate_configuration_errors
from llm_agent.application.context import AppPaths, WorkspaceContext, WorkspacePaths

if TYPE_CHECKING:
    from llm_agent.application.conversation import ConversationRuntime


@dataclass(frozen=True, slots=True)
class TaskActivityUpdate:
    """Finite presentation effects projected from one canonical runtime event."""

    run_id: str
    timestamp: str
    coalescing_key: str
    delivery: Literal["latest", "milestone", "error", "terminal"]
    activity: str
    advances_activity: bool
    model_active: bool | None
    tool_transition: Literal["start", "end"] | None
    tool_name: str | None
    invocation_id: str | None
    step_label: str | None
    warning: bool
    terminal_outcome: str | None


@dataclass(frozen=True)
class TaskExecutionStart:
    workspace: str | Path
    app_paths: AppPaths
    config_path: str | Path | None = None
    profile: str | None = None
    startup_capabilities: tuple[str, ...] | None = None
    observability_mode: str | None = None
    configure_logging: bool = True


class TaskExecutionRuntime:
    """Opaque identity handle, constructed only by Application startup."""

    __slots__ = ("_owner", "_config_path", "_conversation")
    _owner: _AgentApplication
    _config_path: str | Path | None
    _conversation: ConversationRuntime | None

    def __init__(self) -> None:
        raise TypeError("use an Application startup use case")


def _retain_runtime(owner: object, config_path: str | Path | None = None) -> TaskExecutionRuntime:
    runtime = object.__new__(TaskExecutionRuntime)
    runtime._owner = cast(_AgentApplication, owner)
    runtime._config_path = config_path
    runtime._conversation = None
    return runtime


def _start(start: TaskExecutionStart, approval: _agent_approval.ApprovalPort, *, interactive: bool) -> TaskExecutionRuntime:
    with translate_configuration_errors():
        owner = _AgentApplication.create(
            workspace=Path(start.workspace).expanduser(), paths=start.app_paths,
            config_path=start.config_path, profile=start.profile, approval_policy=approval,
            task_authority_capabilities=None if start.startup_capabilities is None else list(start.startup_capabilities),
            observability_mode=start.observability_mode,
            operational_mode=_OperationalMode.READ_ONLY if interactive else None,
            configure_logging=start.configure_logging,
        )
    return _retain_runtime(owner, start.config_path)


def start_interactive_session(start: TaskExecutionStart, approval: ApprovalPresenter) -> TaskExecutionRuntime:
    return _start(start, _bind_approval_presenter(approval), interactive=True)


def start_headless_task(start: TaskExecutionStart, *, automatic_approval: bool = False) -> TaskExecutionRuntime:
    return _start(start, _agent_approval.AutoApprove() if automatic_approval else _RequireExplicitApproval(), interactive=False)


@dataclass(frozen=True)
class TaskExecutionContext:
    conversation: ConversationRuntime
    config: dict[str, Any]
    app_paths: AppPaths
    workspace: WorkspaceContext
    workspace_paths: WorkspacePaths
    config_path: str | Path | None


def read_execution_context(runtime: TaskExecutionRuntime) -> TaskExecutionContext:
    from llm_agent.application.conversation import bind_conversation

    owner = runtime._owner
    if runtime._conversation is None:
        runtime._conversation = bind_conversation(runtime)
    return TaskExecutionContext(runtime._conversation, owner.config, owner.paths,
                                owner.workspace, owner.workspace_paths, runtime._config_path)


@dataclass(frozen=True)
class TaskExecutionCapabilities:
    label: str
    allows_write_validate: bool
    is_full_mode: bool


def read_execution_capabilities(runtime: TaskExecutionRuntime) -> TaskExecutionCapabilities:
    orchestrator = runtime._owner.orchestrator
    mode_allows = getattr(orchestrator, "mode_allows", None)
    return TaskExecutionCapabilities(
        getattr(orchestrator, "operational_mode_label", "FULL"),
        callable(mode_allows) and bool(mode_allows({"write", "validate"})),
        getattr(orchestrator, "operational_mode", None) is _OperationalMode.FULL,
    )


@dataclass(frozen=True)
class ModeSelection:
    status: Literal["selected", "invalid", "unavailable"]
    label: str | None = None


def select_execution_mode(runtime: TaskExecutionRuntime, text: str) -> ModeSelection:
    mode = _OperationalMode.parse(text)
    if mode is None:
        return ModeSelection("invalid")
    setter = getattr(runtime._owner.orchestrator, "set_operational_mode", None)
    if not callable(setter):
        return ModeSelection("unavailable")
    setter(mode)
    return ModeSelection("selected", mode.display_name)


class TaskDispatch:
    __slots__ = ("_request",)
    _request: _ParsedTaskRequest

    def __init__(self) -> None:
        raise TypeError("use prepare_task_dispatch")

    @property
    def continues_task(self) -> bool:
        return bool(self._request.action.value == "continue")


def prepare_task_dispatch(text: str) -> TaskDispatch:
    dispatch = object.__new__(TaskDispatch)
    dispatch._request = _parse_task_request(text)
    return dispatch


class TaskSettlement:
    __slots__ = ("_value", "_legacy")
    _value: Any
    _legacy: bool

    def __init__(self) -> None:
        raise TypeError("settlements are produced by execution")


def _settle(value: object, *, legacy: bool = False) -> TaskSettlement:
    settlement = object.__new__(TaskSettlement)
    settlement._value = value
    settlement._legacy = legacy
    return settlement


_Entry = Literal["headless-run", "headless-resume", "natural", "agent", "retry"]


def execute_submission(
    runtime: TaskExecutionRuntime, text: str, *, entry: _Entry,
    visible_text: str | None = None, dispatch: TaskDispatch | None = None,
    stream_callback: Callable[[str], None] | None = None,
) -> TaskSettlement:
    return _task_submission._execute_submission(
        runtime._owner, text, entry=entry, visible_text=visible_text, dispatch=dispatch,
        stream_callback=stream_callback, settle=_settle, prepare_dispatch=prepare_task_dispatch,
    )


def observe_task_settlement(
    settlement: TaskSettlement, *, channel: Literal["headless", "interactive"],
) -> dict[str, Any]:
    return _task_observation._observe_task_settlement(settlement, channel=channel)


def bind_interactive_services(
    runtime: TaskExecutionRuntime,
    approval: ApprovalPresenter,
    event_sink: Callable[[TaskActivityUpdate], None],
) -> Callable[[], None]:
    owner = runtime._owner
    broker = _bind_approval_presenter(approval)
    owner.approval_policy = broker
    gateway = getattr(owner, "tool_invocation_gateway", None)
    if gateway is not None:
        gateway.approval_port = broker
    return _task_activity._bind_task_activity(owner.orchestrator, event_sink, make_update=TaskActivityUpdate)


def bind_submission_cancellation(
    runtime: TaskExecutionRuntime, event: Event, register: Callable[[Callable[[], None]], None],
) -> Callable[[], None]:
    bind = getattr(runtime._owner, "bind_interactive_cancellation", None)
    request = getattr(runtime._owner, "request_cancel_only", None)
    cleanup = bind(event) if callable(bind) else (lambda: None)
    if callable(request):
        register(request)
    return cast(Callable[[], None], cleanup)


def execute_memory_command(
    runtime: TaskExecutionRuntime, action: Literal["remember", "show", "forget", "clear", "save", "load"],
    *, key: str = "", value: str = "", path: str | None = None,
) -> dict[str, Any]:
    return _task_commands._execute_memory_command(
        runtime._owner.orchestrator, action, key=key, value=value, path=path,
    )


def execute_workspace_command(
    runtime: TaskExecutionRuntime, action: Literal["list", "read", "find", "search"], value: str = "",
    *, cancellation_event: Event | None = None,
) -> dict[str, Any]:
    name, arguments = _task_commands._prepare_workspace_command(action, value)
    return _task_commands._execute_workspace_command(
        runtime._owner.orchestrator, name, arguments,
        cancellation_event=cancellation_event, primitive=_task_observation._primitive,
    )


def set_session_diagnostics(runtime: TaskExecutionRuntime, enabled: bool) -> None:
    _task_commands._set_session_diagnostics(runtime._owner.orchestrator, enabled)


def read_runtime_inspection(runtime: TaskExecutionRuntime) -> dict[str, Any]:
    return _task_observation._read_runtime_inspection(runtime._owner)


def close_task_execution(runtime: TaskExecutionRuntime) -> None:
    runtime._owner.close()


__all__ = [
    "TaskActivityUpdate", "TaskExecutionRuntime", "TaskExecutionStart", "TaskDispatch", "TaskSettlement", "TaskExecutionContext",
    "TaskExecutionCapabilities", "ModeSelection", "start_interactive_session", "start_headless_task",
    "prepare_task_dispatch", "execute_submission", "observe_task_settlement", "read_execution_context",
    "read_execution_capabilities", "select_execution_mode", "bind_interactive_services",
    "bind_submission_cancellation", "execute_memory_command", "execute_workspace_command",
    "set_session_diagnostics", "read_runtime_inspection", "close_task_execution",
]
