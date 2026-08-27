"""Plan identity and step-progression operations owned by AgentState."""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Mapping, Optional, Sequence, cast

from agent.contracts import PlanStep, ToolHistoryEntry
from agent.execution_state import TERMINAL_STEP_STATUSES, StepExecutionRecord, StepStatus
from agent.state_plan import canonicalize_plan_steps
from agent.state_progression import (
    current_result_for_step,
    pending_effects,
    record_executed_effect,
    reset_task_progression,
    waive_effect,
)


class StatePlanExecutionMixin:
    plan: List[PlanStep]
    plan_identity: Optional[str]
    plan_step: int
    current_step_id: Optional[str]
    step_records: Dict[str, StepExecutionRecord]
    tool_history: List[ToolHistoryEntry]

    @staticmethod
    def _new_step_id() -> str:
        return f"step-{uuid.uuid4().hex}"

    @classmethod
    def canonicalize_plan_steps(
        cls,
        plan: Sequence[Mapping[str, Any]],
        *,
        preserve_step_ids: bool = True,
    ) -> List[PlanStep]:
        return cast(
            List[PlanStep],
            canonicalize_plan_steps(plan, cls._new_step_id, preserve_step_ids=preserve_step_ids),
        )

    def set_plan(self, plan: Sequence[Mapping[str, Any]]) -> None:
        """Substitui o plano e preserva registros de IDs sobreviventes."""

        normalized = self.canonicalize_plan_steps(plan)
        if not normalized:
            self.plan_identity = None
        elif self.plan_identity is None or not self.plan or self.plan != normalized:
            self.plan_identity = f"plan-{uuid.uuid4().hex}"
        records: Dict[str, StepExecutionRecord] = {}
        for step in normalized:
            step_id = str(step["_step_id"])
            records[step_id] = self.step_records.get(step_id) or StepExecutionRecord(step_id=step_id)
        self.plan = normalized
        self.step_records = records
        if self.current_step_id not in records:
            self.current_step_id = None

    def reset_execution(self) -> None:
        self.plan = []
        self.plan_identity = None
        self.plan_step = 0
        self.current_step_id = None
        self.step_records = {}

    def reset_task_progression(
        self,
        requested_effects: Sequence[str] = (),
        *,
        preserve_semantics: bool = False,
    ) -> None:
        reset_task_progression(self, requested_effects, preserve_semantics=preserve_semantics)

    def record_executed_effect(
        self,
        effect: str,
        evidence_ref: int | str | None = None,
        *,
        allow_legacy: bool = False,
        effect_authority: Any = None,
    ) -> None:
        record_executed_effect(
            self,
            effect,
            evidence_ref=evidence_ref,
            allow_legacy=allow_legacy,
            effect_authority=effect_authority,
        )

    def waive_effect(
        self,
        effect: str,
        evidence_ref: int | str | None = None,
        *,
        allow_legacy: bool = False,
        effect_authority: Any = None,
    ) -> None:
        waive_effect(
            self,
            effect,
            evidence_ref=evidence_ref,
            allow_legacy=allow_legacy,
            effect_authority=effect_authority,
        )

    def pending_effects(self) -> tuple[str, ...]:
        return pending_effects(self)

    def current_result_for_step(self, step_id: str) -> tuple[int, ToolHistoryEntry] | None:
        current = current_result_for_step(
            self.tool_history,
            step_id,
            plan_id=self.plan_identity,
        )
        return cast(tuple[int, ToolHistoryEntry] | None, current)

    def clear_plan(self) -> None:
        self.reset_execution()

    def insert_plan_step(self, index: int, step: Mapping[str, Any]) -> None:
        if self.plan_identity is None:
            self.plan_identity = f"plan-{uuid.uuid4().hex}"
        prepared = cast(PlanStep, dict(step))
        step_id = str(prepared.get("_step_id") or self._new_step_id())
        prepared["_step_id"] = step_id
        if "tool" in prepared or "args" in prepared:
            args = prepared.get("args")
            prepared["args"] = dict(args) if isinstance(args, dict) else {}
        self.plan.insert(index, prepared)
        self.step_records[step_id] = StepExecutionRecord(step_id=step_id)

    def remove_plan_step(self, index: int) -> None:
        step = self.plan.pop(index)
        self.step_records.pop(str(step.get("_step_id", "")), None)

    def replace_plan_step(self, index: int, new_steps: Sequence[Mapping[str, Any]]) -> None:
        self.remove_plan_step(index)
        for offset, step in enumerate(new_steps):
            self.insert_plan_step(index + offset, step)

    def get_step_id(self, index: int) -> str:
        return str(self.plan[index]["_step_id"])

    def get_step_status(self, index: int) -> StepStatus:
        return self.step_records[self.get_step_id(index)].status

    def next_pending_index(self, start: int = 0) -> Optional[int]:
        for index in range(max(0, start), len(self.plan)):
            if self.get_step_status(index) is StepStatus.PENDING:
                return index
        return None

    def mark_step_running(self, index: int) -> None:
        step_id = self.get_step_id(index)
        record = self.step_records[step_id]
        record.status = StepStatus.RUNNING
        record.attempts += 1
        record.last_error = ""
        self.current_step_id = step_id
        self.plan_step = index + 1

    def mark_step_completed(self, index: int) -> None:
        self._mark_step_terminal(index, StepStatus.COMPLETED)

    def mark_step_failed(self, index: int, error: str = "") -> None:
        self._mark_step_terminal(index, StepStatus.FAILED, error)

    def mark_step_skipped(self, index: int, reason: str = "") -> None:
        self._mark_step_terminal(index, StepStatus.SKIPPED, reason)

    def mark_step_blocked(self, index: int, reason: str = "") -> None:
        self._mark_step_terminal(index, StepStatus.BLOCKED, reason)

    def mark_step_unverified(self, index: int, reason: str = "") -> None:
        self._mark_step_terminal(index, StepStatus.UNVERIFIED, reason)

    def _mark_step_terminal(self, index: int, status: StepStatus, error: str = "") -> None:
        step_id = self.get_step_id(index)
        record = self.step_records[step_id]
        record.status = status
        record.last_error = error
        if self.current_step_id == step_id:
            self.current_step_id = None

    def prepare_for_resume(self, retry_failed: bool = False, retry_skipped: bool = False) -> None:
        for record in self.step_records.values():
            record.prepare_for_resume(retry_failed=retry_failed, retry_skipped=retry_skipped)
        self.current_step_id = None

    def all_steps_terminal(self) -> bool:
        return bool(self.step_records) and all(
            record.status in TERMINAL_STEP_STATUSES for record in self.step_records.values()
        )


__all__ = ["StatePlanExecutionMixin"]
