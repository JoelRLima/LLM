"""Private canonical-event projection and subscription for task activity."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Generic, Literal, TypeVar

from llm_agent.agent.runtime.event_kinds import RuntimeEventKind as _RuntimeEventKind
from llm_agent.agent.runtime.events import RuntimeEvent as _RuntimeEvent

_Update = TypeVar("_Update")


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


def _project_task_activity_update(
    event: _RuntimeEvent, *, make_update: Callable[..., _Update],
) -> _Update:
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
    return make_update(
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


class _TaskActivityAdapter(Generic[_Update]):
    """Private dispatcher sink that exposes only finite activity effects."""

    def __init__(self, callback: Callable[[_Update], None], make_update: Callable[..., _Update]) -> None:
        self._callback = callback
        self._make_update = make_update

    def emit(self, event: _RuntimeEvent) -> None:
        self._callback(_project_task_activity_update(event, make_update=self._make_update))


def _bind_task_activity(
    orchestrator: Any, event_sink: Callable[[_Update], None], *, make_update: Callable[..., _Update],
) -> Callable[[], None]:
    dispatcher = getattr(orchestrator, "event_dispatcher", None)
    adapter = _TaskActivityAdapter(event_sink, make_update)
    if dispatcher is not None:
        dispatcher.add_sink(adapter)

    def detach() -> None:
        if dispatcher is not None:
            dispatcher.remove_sink(adapter)

    return detach
