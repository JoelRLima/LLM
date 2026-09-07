"""W14 semantic continuation checkpoint projection."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent.planning.intent_continuation import W14IntentContinuation
from agent.runtime.task_directives import ABSENT


def project_w14_continuation(state: Any, checkpoint: dict[str, Any]) -> None:
    if getattr(state, "w14_semantic_task", False) is not True:
        return
    continuation = getattr(state, "w14_intent_continuation", None)
    if not isinstance(continuation, W14IntentContinuation):
        raise ValueError("W14 semantic task lacks its continuation projection")
    checkpoint["w14_semantic_task"] = True
    checkpoint["w14_intent_continuation"] = continuation.to_checkpoint_dict()


def restore_w14_continuation(state: Any, data: Mapping[str, Any]) -> None:
    raw_marker = data.get("w14_semantic_task", False)
    if type(raw_marker) is not bool:
        raise ValueError("Checkpoint W14 semantic marker is invalid")
    raw_projection = data.get("w14_intent_continuation", ABSENT)
    if raw_marker is False:
        if raw_projection is not ABSENT:
            raise ValueError("Checkpoint contains an unmarked W14 continuation")
        state.w14_semantic_task = False
        state.w14_intent_continuation = None
        return
    if raw_projection is ABSENT:
        raise ValueError("W14 checkpoint lacks semantic continuation projection")
    try:
        projection = W14IntentContinuation.from_checkpoint_dict(raw_projection)
    except (TypeError, ValueError) as exc:
        raise ValueError("Checkpoint W14 continuation is invalid") from exc
    state.w14_semantic_task = True
    state.w14_intent_continuation = projection


__all__ = ["project_w14_continuation", "restore_w14_continuation"]
