"""Compatibility facade for the shared interactive core."""

from typing import Any

from llm_agent.interactive import ui_plane as _core
from llm_agent.interactive.ui_plane import (
    MailboxStats as MailboxStats,
)
from llm_agent.interactive.ui_plane import (
    RunSnapshot as RunSnapshot,
)
from llm_agent.interactive.ui_plane import (
    TaskActivityUISink as TaskActivityUISink,
)
from llm_agent.interactive.ui_plane import (
    UIEventEnvelope as UIEventEnvelope,
)
from llm_agent.interactive.ui_plane import (
    UIEventMailbox as UIEventMailbox,
)


def __getattr__(name: str) -> Any:
    return getattr(_core, name)


class RunViewModel(_core.RunViewModel):
    """Terminal rendering adapter over the shared state projection."""

    def render_toolbar(self, *, width: int = 120, mode: str = "FULL") -> str:
        snap = self.snapshot()
        if snap.state == "IDLE" or snap.generation is None:
            return f"Ready | mode={mode}"
        run = snap.short_run_id or f"gen-{snap.generation}"
        activity = snap.current_tool or snap.current_step or (snap.milestones[-1] if snap.milestones else "working")
        attention = " | ATTENTION" if snap.attention_pending else ""
        pending = f" | pending={snap.pending_count}" if snap.pending_count else ""
        full = f"RUN {run} | {snap.state} {activity}{attention}{pending} | mode={mode}"
        if width >= 120:
            return full
        if width >= 80:
            return f"RUN {run} | {snap.state} {activity}{attention}"
        return f"{snap.state} {activity}{attention}"[: max(1, width)]
