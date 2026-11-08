"""Compatibility facade for the shared interactive core."""

from typing import Any

from llm_agent.interactive import query_executor as _core
from llm_agent.interactive.query_executor import (
    CliQueryCompletion as CliQueryCompletion,
)
from llm_agent.interactive.query_executor import (
    CliQuerySubmission as CliQuerySubmission,
)
from llm_agent.interactive.query_executor import (
    QueryFunction as QueryFunction,
)


def __getattr__(name: str) -> Any:
    return getattr(_core, name)


from datetime import datetime, timezone  # noqa: E402


def _live_marker(task_active: bool) -> str | None:
    if not task_active:
        return None
    return f"LIVE SNAPSHOT | task active | {datetime.now(timezone.utc).isoformat()}"


class BoundedQueryExecutor(_core.BoundedQueryExecutor):
    """Keep the historical CLI live-marker presentation."""

    def __init__(self, *, workspace_id: str, workspace_generation: int = 1):
        super().__init__(
            workspace_id=workspace_id, workspace_generation=workspace_generation, live_marker_factory=_live_marker
        )
