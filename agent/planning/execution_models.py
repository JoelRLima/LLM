"""Small value objects shared by plan execution helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from agent.tools.contracts import ToolResult


@dataclass
class StepLoopResult:
    next_index: int
    result: Optional[ToolResult] = None
    answer: Optional[str] = None
    stop: bool = False
