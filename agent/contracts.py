from __future__ import annotations

from typing import Any, Dict, List, Optional, TypedDict

ToolArgs = Dict[str, Any]
EventData = Dict[str, Any]


class ResultBinding(TypedDict):
    """Model-facing structural reference to an earlier plan result."""

    from_step: int | str
    path: List[str | int]


class PlanStep(TypedDict, total=False):
    tool: str
    args: ToolArgs
    # Optional additive result-binding declarations.  Bindings are normalized
    # to producer step IDs before persistence; model plans may use ordinals.
    bindings: Dict[str, ResultBinding]
    _step_id: str
    kind: str
    observation_ref: int | str
    predicate: Dict[str, Any]
    on_true: Dict[str, Any]
    on_false: Dict[str, Any]


class ToolResult(TypedDict, total=False):
    invocation_id: str
    ok: bool
    done: bool
    status: str
    executed: Optional[bool]
    data: Any
    error: Optional[str]
    message: Optional[str]
    artifacts: List[Any]
    total_lines: int


class ToolHistoryEntry(TypedDict, total=False):
    step_id: Optional[str]
    invocation_id: str
    status: str
    logical_slot: int
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
    persona: Optional[str]
    persona_prompt: Optional[str]
    reasoning_turns_used: int
    reasoning_last_history_count: int
    reasoning_last_progress_token: Optional[str]
    continue_after_plan: bool
