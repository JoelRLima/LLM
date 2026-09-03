"""Small application-boundary adapters for the live observer lifecycle."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent.application_result import AgentRunResult
from agent.observability.bookmarks import BookmarkStore
from agent.observability.live import ObservationSession
from agent.presentation import InspectionService


def start_observation_session(application: Any, correlation: Any, *, resumed: bool = False) -> None:
    """Attach one observer after the canonical runtime correlation exists."""

    del resumed
    previous = getattr(application, "observation_session", None)
    if previous is not None and not previous.closed:
        try:
            previous.finish({"status": "unknown", "summary": "observation session replaced"})
        except Exception:
            pass
    application.observation_session = None
    try:
        session = ObservationSession.for_correlation(
            application.workspace_paths,
            correlation,
            mode=application.observability_mode,
        )
        session.start(application.orchestrator.event_dispatcher)
        application.observation_session = session
    except Exception:
        # Trace persistence is an observer concern; the canonical task remains
        # governed by its existing dispatcher/state path.
        application.observation_session = None


def finish_observation(application: Any, result: AgentRunResult | None = None) -> None:
    """Finalize observation without changing the public application result."""

    session = getattr(application, "observation_session", None)
    if session is None:
        return
    application.observation_session = None
    outcome: dict[str, Any] | None = None
    if result is not None:
        outcome = {
            "status": result.status,
            "success": result.success,
            "error": result.error,
        }
    try:
        session.finish(outcome)
    except Exception:
        # An observer failure must not rewrite the canonical outcome or block
        # resource cleanup.
        return


def build_inspection_service(application: Any) -> InspectionService:
    """Build the shared read-only service from canonical application sources."""

    def canonical_reader() -> Mapping[str, Any]:
        snapshot = getattr(application.orchestrator, "_canonical_run_snapshot", None)
        if snapshot is not None and callable(getattr(snapshot, "to_dict", None)):
            projected = snapshot.to_dict()
            raw_facts = projected.get("projection_facts")
            facts = raw_facts if isinstance(raw_facts, Mapping) else {}
            return {
                "model_calls": projected.get("metrics", {}),
                "tools": {"items": facts.get("tools", [])},
                "validation": facts.get("validation", {}),
                "recovery": {"items": facts.get("replan_events", [])},
                "changes": facts.get("executed", {}),
                "metrics": projected.get("metrics", {}),
            }
        metrics_reader = getattr(application.orchestrator, "_get_metrics_for_task", None)
        metrics = metrics_reader() if callable(metrics_reader) else None
        state = getattr(application.orchestrator, "agent_state", None)
        history = getattr(state, "tool_history", None) if state is not None else None
        return {
            "model_calls": {"items": metrics[:64]} if isinstance(metrics, list) else None,
            "tools": {"items": history[:64]} if isinstance(history, list) else None,
            "validation": {"status": "unknown"},
            "recovery": {"status": "unknown"},
            "changes": {"status": "unknown"},
            "metrics": {"items": metrics[:64]} if isinstance(metrics, list) else None,
        }

    def silence_reader(metadata: Any) -> Any:
        session = getattr(application, "observation_session", None)
        return session.silence_status(metadata) if session is not None else None

    return InspectionService(
        application.workspace_paths,
        canonical_reader=canonical_reader,
        silence_reader=silence_reader,
        bookmark_reader=BookmarkStore(application.workspace_paths).reader,
    )


__all__ = ["build_inspection_service", "finish_observation", "start_observation_session"]
