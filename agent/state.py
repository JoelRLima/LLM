import copy
from typing import Any, Dict, List, Mapping, Optional

from agent.contracts import (
    AgentEvent,
    PlanStep,
    ToolArgs,
    ToolHistoryEntry,
)
from agent.execution_state import StepExecutionRecord
from agent.memory.memory import AgentMemory
from agent.planning.task_semantics import TaskSemantics
from agent.runtime.budget import TaskBudgetLedger
from agent.state_checkpointing import StateCheckpointMixin
from agent.state_failure_recovery import StateFailureRecoveryMixin
from agent.state_incidents import StateIncidentMixin
from agent.state_plan_execution import StatePlanExecutionMixin
from agent.state_semantics import TaskSemanticsStateMixin
from agent.tools.contracts import ToolResult as CanonicalToolResult
from agent.tools.result_adapter import ensure_canonical_result


class AgentState(
    StatePlanExecutionMixin,
    TaskSemanticsStateMixin,
    StateFailureRecoveryMixin,
    StateCheckpointMixin,
    StateIncidentMixin,
):
    """Estado completo e unificado do agente."""

    def __init__(
        self,
        memory: AgentMemory | None = None,
        budget_ledger: TaskBudgetLedger | None = None,
    ) -> None:
        # Dados da execução atual
        self.objective: Optional[str] = None
        self.plan: List[PlanStep] = []
        # Scope for causal observations. Step IDs are stable within a plan,
        # but old plans may remain in memory during hierarchical execution or
        # checkpoint migration.
        self.plan_identity: Optional[str] = None
        self.plan_step: int = 0
        self.current_step_id: Optional[str] = None
        self.step_records: Dict[str, StepExecutionRecord] = {}
        # Canonical runtime result.  Checkpoint/reporting code owns the
        # explicit legacy projection; planning/policy never needs to
        # round-trip through a serialized result merely to inspect it.
        self.last_result: Optional[CanonicalToolResult] = None
        self.last_tool: Optional[str] = None
        self.last_args: Optional[ToolArgs] = None
        self.tool_history: List[ToolHistoryEntry] = []
        self.execution_incidents: List[Dict[str, Any]] = []
        self.persona: Optional[str] = None
        self.persona_prompt: Optional[str] = None
        self._task_semantics = TaskSemantics.empty()
        self.continuation_attempts: int = 0
        # Task-owned retry ledger.  ReplanContext instances are projections
        # and cannot reset these counters by being recreated.
        self.replan_counts: Dict[str, int] = {"total": 0, "heuristic": 0, "llm": 0}
        self._task_rollback_occurred: bool = False
        self._task_rollback_succeeded: bool | None = None
        self.reasoning_turns_used: int = 0
        self.reasoning_last_history_count: int = -1
        self.reasoning_last_progress_token: Optional[str] = None
        self.continue_after_plan: bool = False
        self._terminal_disposition: Optional[str] = None
        self.budget_ledger = budget_ledger

        # Componentes de memória e histórico
        self.memory = memory or AgentMemory()
        self.events: List[AgentEvent] = []
        self.conversation_history: List[Dict[str, str]] = []
        self.max_history_turns: int = 6

    def record_tool_result(
        self,
        tool_name: str,
        args: ToolArgs,
        result: CanonicalToolResult | Mapping[str, Any],
        step_id: Optional[str] = None,
        logical_slot: Optional[int] = None,
        *,
        plan_id: Optional[str] = None,
    ) -> None:
        """Registra o resultado de uma execução de ferramenta no estado global.

        Centraliza a mutação de last_tool, last_args, last_result e tool_history,
        evitando que múltiplos componentes escrevam diretamente nesses atributos.
        """
        canonical_result = ensure_canonical_result(result)
        entry: ToolHistoryEntry = {
            "step_id": step_id or self.current_step_id,
            "tool": tool_name,
            "args": args,
            "result": canonical_result,
        }
        active_plan_id = self.plan_identity if plan_id is None else plan_id
        if active_plan_id is not None:
            entry["plan_id"] = str(active_plan_id)
        if canonical_result.invocation_id is not None:
            entry["invocation_id"] = str(canonical_result.invocation_id)
        if canonical_result.status is not None:
            entry["status"] = str(getattr(canonical_result.status, "value", canonical_result.status))
        if logical_slot is not None:
            entry["logical_slot"] = logical_slot

        # Semantic observation is the validation boundary for the complete
        # canonical record.  Validate against a detached owner first so a
        # collision or rejected terminal transition cannot leave last_*,
        # history, and the evidence catalog at different points in time.
        candidate_semantics = copy.deepcopy(self._task_semantics)
        evidence_ref = len(self.tool_history) + 1
        candidate_semantics.observe_tool(
            tool_name,
            canonical_result,
            evidence_ref=evidence_ref,
            args=args,
        )

        next_history = [*self.tool_history, entry]
        self._task_semantics = candidate_semantics
        self.last_tool = tool_name
        self.last_args = args
        self.last_result = canonical_result
        self.tool_history = next_history

    def project_last_result(
        self,
        tool_name: str,
        args: ToolArgs,
        result: CanonicalToolResult | Mapping[str, Any],
    ) -> None:
        """Project a canonical terminal result without appending history again."""
        canonical_result = ensure_canonical_result(result)
        self.last_tool = tool_name
        self.last_args = args
        self.last_result = canonical_result

    def reset_runtime_observation(self, *, clear_events: bool = False) -> None:
        """Clear the task's last-result/history projection at one boundary."""

        self.last_result = None
        self.last_tool = None
        self.last_args = None
        self.tool_history = []
        if clear_events:
            self.events.clear()

    def add_event(self, event: AgentEvent) -> None:
        """Adiciona um evento ao histórico de telemetria."""
        self.events.append(event)

    def add_conversation_turn(self, user: str, agent: str) -> None:
        """Adiciona uma nova entrada ao histórico de conversa."""
        self.conversation_history.append({"user": user, "agent": agent})
