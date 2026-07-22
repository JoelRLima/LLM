from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Protocol

from agent.contracts import EventData, PlanStep, ToolArgs, ToolHistoryEntry, ToolResult


class StepOutcomeKind(str, Enum):
    COMPLETED = "completed"
    SKIPPED = "skipped"
    FAILED = "failed"
    REPLAN = "replan"
    FINAL = "final"
    CANCELLED = "cancelled"


@dataclass
class StepExecutionOutcome:
    kind: StepOutcomeKind
    result: Optional[ToolResult] = None
    error: str = ""
    final_answer: Optional[str] = None


class MemoryPort(Protocol):
    state: Dict[str, Any]


class ExecutionStatePort(Protocol):
    plan: List[PlanStep]
    tool_history: List[ToolHistoryEntry]
    memory: MemoryPort

    def get_step_id(self, index: int) -> str: ...
    def mark_step_running(self, index: int) -> None: ...
    def mark_step_completed(self, index: int) -> None: ...
    def mark_step_failed(self, index: int, error: str = "") -> None: ...
    def mark_step_skipped(self, index: int, reason: str = "") -> None: ...
    def record_tool_result(self, tool_name: str, args: ToolArgs, result: ToolResult, step_id: Optional[str] = None) -> None: ...
    def add_conversation_turn(self, user: str, agent: str) -> None: ...


class SkillPort(Protocol):
    def get_schema(self) -> Dict[str, Any]: ...


class WorkspacePort(Protocol):
    def show_diff(self, file_path: str, content: str) -> None: ...
    def lint_check(self, file_path: str) -> Optional[str]: ...


class ContextManagerPort(Protocol):
    def maybe_compress_context(self) -> None: ...


class CancellationPort(Protocol):
    @property
    def cancelled(self) -> bool: ...


class StepRuntimePort(Protocol):
    def _emit(self, event_type: str, data: Optional[EventData] = None) -> None: ...
    def _run_tool(self, tool_name: str, args: ToolArgs) -> ToolResult: ...
    def _handle_step_failure(self, step_index: int, reason: str, tool: str = "", args: Optional[ToolArgs] = None) -> str: ...
    def _purge_stale_context(self) -> None: ...
    def _generate_content(self, tool: str, args: ToolArgs, objective: str) -> Optional[str]: ...
    def _test_and_correct(self, file_path: str, objective: str) -> bool: ...
    def _maybe_summarize_and_store(self, tool_name: str, args: ToolArgs, result: ToolResult) -> None: ...
    def fail_task(self) -> None: ...


class ExecutionContext(StepRuntimePort, Protocol):
    agent_state: ExecutionStatePort
    skills: Dict[str, SkillPort]
    active_skills: list[str]
    verbose: bool
    workspace: WorkspacePort
    context_manager: ContextManagerPort
    cancellation_token: CancellationPort
