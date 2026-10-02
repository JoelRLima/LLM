"""Finite task and session use cases over one privately retained Agent runtime.

Approval/event collaborators are temporary opaque composition inputs until their
own cohorts. Settlements retain Agent results privately until C11; observations
are copied, validated primitive values already consumed by the CLI.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import TYPE_CHECKING, Any, Literal, cast

from llm_agent.agent.application import AgentApplication as _AgentApplication
from llm_agent.agent.approval import ApprovalPort as _ApprovalPort
from llm_agent.agent.approval import AutoApprove as _AutoApprove
from llm_agent.agent.approval import RequireExplicitApproval as _RequireExplicitApproval
from llm_agent.agent.interaction.task_directives import parse_task_request as _parse_task_request
from llm_agent.agent.interaction.types import ParsedTaskRequest as _ParsedTaskRequest
from llm_agent.agent.observability.trace_store import TraceCorruptError as _TraceCorruptError
from llm_agent.agent.observability.trace_store import TraceUnavailableError as _TraceUnavailableError
from llm_agent.agent.runtime.event_kinds import RuntimeEventKind as _RuntimeEventKind
from llm_agent.agent.runtime.events import RuntimeEvent as _RuntimeEvent
from llm_agent.agent.tools.authority import OperationalMode as _OperationalMode
from llm_agent.application.configuration_errors import translate_configuration_errors
from llm_agent.application.context import AppPaths, WorkspaceContext, WorkspacePaths
from llm_agent.application.inspection.errors import InspectionCorruptDataError, InspectionUnavailableError

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


_MILESTONE_KINDS = frozenset(
    {
        _RuntimeEventKind.PLAN_CREATED,
        _RuntimeEventKind.PLAN_EXTENDED,
        _RuntimeEventKind.PLAN_PREVIEW_READY,
        _RuntimeEventKind.STEP_COMPLETED,
        _RuntimeEventKind.STEP_FAILED,
        _RuntimeEventKind.STEP_BLOCKED,
        _RuntimeEventKind.STEP_CANCELLED,
        _RuntimeEventKind.STEP_SKIPPED,
        _RuntimeEventKind.STEP_UNVERIFIED,
        _RuntimeEventKind.REPLAN,
        _RuntimeEventKind.REPLAN_BLOCKED,
        _RuntimeEventKind.CONVERGENCE_REPLAN_REQUESTED,
        _RuntimeEventKind.CONVERGENCE_REPLAN_DENIED,
        _RuntimeEventKind.VALIDATION_REPAIR,
        _RuntimeEventKind.TASK_RESUMED,
        _RuntimeEventKind.EXECUTION_FRONTIER_PROJECTED,
        _RuntimeEventKind.PROGRESS_RECEIPT_ADVANCED,
    }
)
_TERMINAL_KINDS = frozenset({_RuntimeEventKind.TASK_OUTCOME, _RuntimeEventKind.FINAL})
_NON_ACTIVITY_KINDS = frozenset(
    {
        _RuntimeEventKind.WARNING,
        _RuntimeEventKind.CONTEXT_REFRESH,
        _RuntimeEventKind.OBSERVATION_REUSE,
        _RuntimeEventKind.OBSERVATION_REHYDRATION,
    }
)
_STEP_KINDS = frozenset(
    {
        _RuntimeEventKind.STEP_COMPLETED,
        _RuntimeEventKind.STEP_FAILED,
        _RuntimeEventKind.STEP_BLOCKED,
        _RuntimeEventKind.STEP_CANCELLED,
        _RuntimeEventKind.STEP_SKIPPED,
        _RuntimeEventKind.STEP_UNVERIFIED,
    }
)


def _project_task_activity_update(event: _RuntimeEvent) -> TaskActivityUpdate:
    """Translate the current CLI event branches into finite presentation effects."""

    kind = event.kind
    data = event.data
    if kind is _RuntimeEventKind.ERROR:
        delivery: Literal["latest", "milestone", "error", "terminal"] = "error"
    elif kind in _TERMINAL_KINDS:
        delivery = "terminal"
    elif kind in _MILESTONE_KINDS:
        delivery = "milestone"
    else:
        delivery = "latest"

    activity = next(
        (str(data[key]) for key in ("activity", "phase", "tool", "step", "message")
         if data.get(key) is not None and str(data[key]).strip()),
        kind.value,
    )
    tool_transition: Literal["start", "end"] | None = None
    tool_name = None
    invocation_id = None
    step_label = None
    model_active = None
    if kind is _RuntimeEventKind.MODEL_CALL_STARTED:
        model_active = True
    elif kind is _RuntimeEventKind.MODEL_CALL_COMPLETED:
        model_active = False
    elif kind is _RuntimeEventKind.TOOL_START:
        tool_transition = "start"
        tool_name = str(data.get("tool") or "unknown")
        invocation_id = event.invocation_id or str(data.get("invocation_id") or "unknown")
    elif kind is _RuntimeEventKind.TOOL_END:
        tool_transition = "end"
        invocation_id = event.invocation_id or str(data.get("invocation_id") or "")
    elif kind in _STEP_KINDS:
        step_label = event.step_id or str(data.get("step_id") or data.get("step") or "unknown")

    terminal_outcome = None
    if kind in _TERMINAL_KINDS:
        terminal_outcome = str(data.get("status") or data.get("outcome") or kind.value)
    return TaskActivityUpdate(
        run_id=event.run_id,
        timestamp=event.timestamp,
        coalescing_key=kind.value,
        delivery=delivery,
        activity=activity,
        advances_activity=kind not in _NON_ACTIVITY_KINDS,
        model_active=model_active,
        tool_transition=tool_transition,
        tool_name=tool_name,
        invocation_id=invocation_id,
        step_label=step_label,
        warning=kind is _RuntimeEventKind.WARNING,
        terminal_outcome=terminal_outcome,
    )


class _TaskActivityAdapter:
    """Private dispatcher sink that exposes only finite activity effects."""

    def __init__(self, callback: Callable[[TaskActivityUpdate], None]) -> None:
        self._callback = callback

    def emit(self, event: _RuntimeEvent) -> None:
        self._callback(_project_task_activity_update(event))


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


def _start(start: TaskExecutionStart, approval: _ApprovalPort, *, interactive: bool) -> TaskExecutionRuntime:
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


def start_interactive_session(start: TaskExecutionStart, approval: object) -> TaskExecutionRuntime:
    return _start(start, cast(_ApprovalPort, approval), interactive=True)


def start_headless_task(start: TaskExecutionStart, *, automatic_approval: bool = False) -> TaskExecutionRuntime:
    return _start(start, _AutoApprove() if automatic_approval else _RequireExplicitApproval(), interactive=False)


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
    owner = runtime._owner
    interact = getattr(owner, "interact", None)
    visible = text if visible_text is None else visible_text
    if entry == "headless-resume":
        resume = getattr(owner, "resume", None)
        return _settle(resume() if callable(resume) else owner.run(None, explicit_resume=True))
    if entry == "retry" and stream_callback is not None:
        resume = getattr(owner, "resume", None)
        if callable(resume):
            return _settle(resume(stream_callback=stream_callback))
    if entry == "headless-run":
        request = (dispatch or prepare_task_dispatch(text))._request
        if request.action.value == "continue" or request.directive is None or request.subject is None:
            raise ValueError("RUN requires a TaskRunDirective")
        if callable(interact):
            return _settle(interact(request.subject, boundary="task", visible_user_text=visible or request.subject, task_payload=visible or request.subject))
        return _settle(owner.run(request.subject, task_run_directive=request.directive), legacy=True)
    if entry not in {"natural", "agent", "retry"}:
        raise ValueError("unsupported task entry")
    payload = "/continue" if entry == "retry" else text
    if callable(interact):
        kwargs: dict[str, Any] = {"boundary": "natural" if entry == "natural" else "task"}
        if entry != "natural" or visible_text is not None:
            kwargs.update(visible_user_text=visible, task_payload=payload)
        if stream_callback is not None:
            kwargs["stream_callback"] = stream_callback
        return _settle(interact(payload, **kwargs))
    if entry == "natural":
        value = owner.run(text)
    else:
        request = (dispatch or prepare_task_dispatch(payload))._request
        value = owner.resume() if request.action.value == "continue" else owner.run(
            request.subject, task_run_directive=request.directive,
        )
    return _settle(value, legacy=True)


def _primitive(value: Any) -> Any:
    """Copy only primitives; exact scalar types reject str/int Agent enums."""
    if value is None or type(value) in (bool, int, float, str):
        return value
    if any(base.__module__.startswith("llm_agent.agent") for base in type(value).__mro__):
        raise TypeError("public observation contains an Agent owner")
    if isinstance(value, Mapping):
        if any(type(key) is not str for key in value):
            raise TypeError("public observation keys must be strings")
        return {key: _primitive(item) for key, item in value.items()}
    if type(value) is list:
        return [_primitive(item) for item in value]
    if type(value) is tuple:
        return tuple(_primitive(item) for item in value)
    raise TypeError("public observation contains a non-primitive value")


def _receipt(value: Any) -> dict[str, Any]:
    receipt = getattr(value, "receipt", None)
    if isinstance(receipt, Mapping) and receipt:
        return cast(dict[str, Any], _primitive(receipt))
    snapshot = getattr(value, "canonical_snapshot", None) or getattr(value, "snapshot", None)
    if snapshot is None:
        return {}
    data = snapshot.to_dict()
    facts = data.get("projection_facts") or {}
    outcome = data.get("operational_outcome") or {}
    result = {key: facts[key] for key in ("tools", "validation", "rollback") if key in facts}
    if "files_affected" in outcome:
        result["files_affected"] = outcome["files_affected"]
    if "validation_status" in outcome and "validation" not in result:
        status = outcome["validation_status"]
        result["validation"] = {"ran": status is not None, "outcome": status}
    count = facts.get("replan_count")
    if type(count) is int:
        result["replan"] = {"occurred": count > 0, "count": count}
    if data.get("status") is not None:
        result["status"] = data["status"]
    if data.get("failure_fact"):
        result["error"] = data["failure_fact"]
    if getattr(value, "report_path", None):
        result["report_path"] = value.report_path
    return cast(dict[str, Any], _primitive(result))


def observe_task_settlement(
    settlement: TaskSettlement, *, channel: Literal["headless", "interactive"],
) -> dict[str, Any]:
    value = settlement._value
    if channel == "headless":
        return cast(dict[str, Any], _primitive(value.to_dict()))
    if channel != "interactive":
        raise ValueError("unsupported settlement channel")
    result: dict[str, Any] = {}
    for name in ("answer", "error", "status", "success", "summary", "reason_code", "interaction_usage"):
        item = getattr(value, name, None)
        if item is not None:
            result[name] = item
    resolution = getattr(value, "resolution", None)
    if resolution is not None:
        result["resolution"] = {key: getattr(getattr(resolution, key, None), "value", getattr(resolution, key, None))
                                for key in ("action", "boundary", "provenance", "ambiguity", "directive",
                                            "deliberation_profile", "reason_code")}
    run_result = getattr(value, "run_result", None)
    if run_result is not None:
        result["receipt"] = _receipt(run_result)
        result["report_path"] = getattr(run_result, "report_path", None)
    result["legacy_transcript"] = settlement._legacy
    return cast(dict[str, Any], _primitive(result))


def bind_interactive_services(
    runtime: TaskExecutionRuntime,
    approval: object,
    event_sink: Callable[[TaskActivityUpdate], None],
) -> Callable[[], None]:
    owner = runtime._owner
    broker = cast(_ApprovalPort, approval)
    owner.approval_policy = broker
    gateway = getattr(owner, "tool_invocation_gateway", None)
    if gateway is not None:
        gateway.approval_port = broker
    dispatcher = getattr(owner.orchestrator, "event_dispatcher", None)
    adapter = _TaskActivityAdapter(event_sink)
    if dispatcher is not None:
        dispatcher.add_sink(adapter)

    def detach() -> None:
        if dispatcher is not None:
            dispatcher.remove_sink(adapter)

    return detach


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
    orchestrator = runtime._owner.orchestrator
    if action == "remember":
        orchestrator.remember(key, value)
    elif action == "forget":
        orchestrator.forget(key)
    elif action == "clear":
        orchestrator.clear_memory()
    elif action == "show":
        return {"rows": [(str(section), str(content)) for section, content in
                         orchestrator.agent_state.memory.state.items() if content]}
    elif action == "save":
        return {"message": str(orchestrator.save_memory_to_file(path))}
    elif action == "load":
        return {"message": str(orchestrator.load_memory_from_file(path))}
    else:
        raise ValueError("unsupported memory command")
    return {}


def execute_workspace_command(
    runtime: TaskExecutionRuntime, action: Literal["list", "read", "find", "search"], value: str = "",
    *, cancellation_event: Event | None = None,
) -> dict[str, Any]:
    commands = {"list": ("directory_lister", {"path": "."}),
                "read": ("file_reader", {"file_path": value}),
                "find": ("grep", {"pattern": value, "path": "."}),
                "search": ("web_search", {"query": value})}
    if action not in commands:
        raise ValueError("unsupported workspace command")
    name, arguments = commands[action]
    orchestrator = runtime._owner.orchestrator
    gateway = getattr(orchestrator, "tool_invocation_gateway", None)
    if gateway is None:
        return {"status": "unavailable", "error": f"{name} indisponÃ­vel", "data": None}
    kwargs: dict[str, Any] = {"active_skills": None,
                              "allowed_capabilities": getattr(orchestrator, "allowed_capabilities", None)}
    if cancellation_event is not None:
        kwargs["cancellation_token"] = cancellation_event
    return cast(dict[str, Any], _primitive(gateway.run(name, arguments, **kwargs).to_legacy_dict()))


def set_session_diagnostics(runtime: TaskExecutionRuntime, enabled: bool) -> None:
    orchestrator = runtime._owner.orchestrator
    orchestrator.verbose = enabled
    orchestrator.context_manager.verbose = orchestrator.verbose


def read_runtime_inspection(runtime: TaskExecutionRuntime) -> dict[str, Any]:
    """Only the fields already rendered by interactive /inspect; no query/reader API."""
    try:
        snapshot = runtime._owner.inspection_service().snapshot(limit=100)
        data = snapshot.to_dict()
    except _TraceCorruptError as exc:
        raise InspectionCorruptDataError(str(exc)) from exc
    except _TraceUnavailableError as exc:
        raise InspectionUnavailableError(str(exc)) from exc
    fields = ("run", "heartbeat", "timeline", "current", "plan_steps", "model_calls", "tools",
              "validation", "recovery", "changes", "metrics", "convergence", "warnings", "selected_detail", "issues")
    observation = {key: data[key] for key in fields}
    observation["run"] = {key: data["run"][key] for key in ("run_id", "status", "completeness", "liveness", "mode")}
    observation["timeline"] = [{key: item[key] for key in ("sequence", "timestamp", "source", "category", "title", "status")} for item in data["timeline"]]
    observation["warnings"] = [{key: item[key] for key in ("sequence", "title", "summary")} for item in data["warnings"]]
    observation["heartbeat"] = {key: data["heartbeat"].get(key) for key in ("observer_heartbeat", "semantic_activity", "silence")}
    return cast(dict[str, Any], _primitive(observation))


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
