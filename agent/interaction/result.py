"""Public interaction result and task-response projection helpers."""

from __future__ import annotations

from .transcript import visible_text_for_run_result
from .types import AgentInteractionResult

__all__ = ["AgentInteractionResult", "visible_text_for_run_result"]
