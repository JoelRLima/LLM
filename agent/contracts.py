from __future__ import annotations

from typing import Any, Dict, List, Optional, TypedDict

ToolArgs = Dict[str, Any]
EventData = Dict[str, Any]


class PlanStep(TypedDict, total=False):
    tool: str
    args: ToolArgs
    _step_id: str


class ToolResult(TypedDict, total=False):
    ok: bool
    done: bool
    data: Any
    error: Optional[str]
    message: Optional[str]
    total_lines: int


class ToolHistoryEntry(TypedDict, total=False):
    step_id: Optional[str]
    tool: str
    args: ToolArgs
    result: ToolResult


class AgentEvent(TypedDict, total=False):
    type: str
    step: int
    data: EventData


class ModelDecision(TypedDict, total=False):
    action: str
    tool: str
    args: ToolArgs
    answer: str
    message: str
    reason: str


class StepRecordData(TypedDict, total=False):
    step_id: str
    status: str
    attempts: int
    last_error: str


class CheckpointData(TypedDict, total=False):
    schema_version: int
    objective: Optional[str]
    plan: List[PlanStep]
    plan_step: int
    current_step_id: Optional[str]
    step_records: List[StepRecordData]
    last_tool: Optional[str]
    last_args: Optional[ToolArgs]
    last_result: Optional[ToolResult]
    tool_history: List[ToolHistoryEntry]
    events: List[AgentEvent]
    conversation_history: List[Dict[str, str]]
    memory_state: Dict[str, Any]
