from __future__ import annotations

from typing import Any, Dict, List, Optional, TypedDict

from agent.tools.contracts import ToolResult as CanonicalToolResult

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


class LegacyToolResult(TypedDict, total=False):
    """Serialized compatibility shape, not the canonical runtime result."""

    invocation_id: str
    ok: bool
    done: bool
    status: str
    executed: Optional[bool]
    data: Any
    error: Optional[str]
    error_code: Optional[str]
    message: Optional[str]
    artifacts: List[Any]
    total_lines: int
    complete: bool
    truncated: bool
    evidence_provenance: str
    source_identity: str
    source_hash: str
    source_extent: Dict[str, Any]


# ``ToolResult`` remains available only through the module compatibility hook
# below. New runtime code names this serialized shape explicitly.


class ToolHistoryEntry(TypedDict, total=False):
    """Canonical live runtime history entry."""

    step_id: Optional[str]
    plan_id: Optional[str]
    invocation_id: str
    run_id: str
    root_task_id: str
    task_id: str
    parent_task_id: Optional[str]
    node_id: Optional[str]
    status: str
    logical_slot: int
    tool: str
    args: ToolArgs
    result: CanonicalToolResult


class SerializedToolHistoryEntry(TypedDict, total=False):
    """Checkpoint/public compatibility projection of a history entry."""

    step_id: Optional[str]
    plan_id: Optional[str]
    invocation_id: str
    run_id: str
    root_task_id: str
    task_id: str
    parent_task_id: Optional[str]
    node_id: Optional[str]
    status: str
    logical_slot: int
    tool: str
    args: ToolArgs
    result: LegacyToolResult


class AgentEvent(TypedDict, total=False):
    type: str
    step: int
    timestamp: str
    run_id: str
    root_task_id: str
    task_id: Optional[str]
    parent_task_id: Optional[str]
    node_id: Optional[str]
    plan_id: Optional[str]
    step_id: Optional[str]
    invocation_id: Optional[str]
    data: EventData


class ModelDecision(TypedDict, total=False):
    action: str
    tool: str
    args: ToolArgs
    bindings: Dict[str, ResultBinding]
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
    root_task_id: Optional[str]
    plan: List[PlanStep]
    plan_identity: Optional[str]
    plan_step: int
    current_step_id: Optional[str]
    step_records: List[StepRecordData]
    last_tool: Optional[str]
    last_args: Optional[ToolArgs]
    last_result: Optional[LegacyToolResult]
    tool_history: List[SerializedToolHistoryEntry]
    execution_incidents: List[Dict[str, Any]]
    events: List[AgentEvent]
    conversation_history: List[Dict[str, str]]
    memory_state: Dict[str, Any]
    persona: Optional[str]
    persona_prompt: Optional[str]
    reasoning_turns_used: int
    reasoning_last_history_count: int
    reasoning_last_progress_token: Optional[str]
    continue_after_plan: bool
    recovery_budget: Dict[str, Any]
    # Compact identity/binding only; bodies remain in the durable repository.
    task_definition: Dict[str, Any]


def __getattr__(name: str) -> Any:
    """Preserve the old import without making it an internal type name."""

    if name == "ToolResult":
        return LegacyToolResult
    raise AttributeError(name)
