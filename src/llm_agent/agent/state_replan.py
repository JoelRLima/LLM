"""Checkpoint support for task-scoped requirement replan fingerprints."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

_FINGERPRINT = re.compile(r"[0-9a-f]{64}\Z")
_MAX_FINGERPRINTS = 64


def checkpoint_replan_fingerprints(state: Any) -> list[str]:
    """Return the bounded canonical representation stored in checkpoints."""

    return list(validate_replan_fingerprints(
        getattr(state, "requirement_replan_fingerprints", ()),
        "AgentState requirement replan fingerprints",
    ))


def restore_replan_fingerprints(state: Any, data: Mapping[str, Any]) -> None:
    """Restore fingerprints, accepting older checkpoints without the field."""

    if "requirement_replan_fingerprints" not in data:
        state.requirement_replan_fingerprints = ()
        return
    state.requirement_replan_fingerprints = validate_replan_fingerprints(
        data["requirement_replan_fingerprints"],
        "Checkpoint requirement replan fingerprints",
    )


def validate_replan_fingerprints(value: Any, name: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or len(value) > _MAX_FINGERPRINTS:
        raise ValueError(f"{name} are invalid.")
    if any(not isinstance(item, str) or _FINGERPRINT.fullmatch(item) is None for item in value):
        raise ValueError(f"{name} are invalid.")
    return tuple(value)
