"""Execution presentation/route preferences; never a source of authority."""
from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from typing import Any


class ExecutionProfile(str, Enum):
    STANDARD = "standard"
    COMPACT = "compact"


def resolve_execution_profile(config: Mapping[str, Any]) -> ExecutionProfile:
    value = config.get("agent_execution_profile", ExecutionProfile.STANDARD.value)
    if not isinstance(value, str):
        raise ValueError("agent_execution_profile must be standard or compact")
    return ExecutionProfile(value)
