"""Plan identity and step-progression operations owned by AgentState."""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from typing import Any, Dict, List, Optional, cast

from agent.contracts import ToolHistoryEntry
from agent.execution_state import TERMINAL_STEP_STATUSES, StepExecutionRecord, StepStatus
from agent.planning.plan_model import (
    DeferredConditionStep,
    Plan,
    PlanStep,
    ToolPlanStep,
)
from agent.state_plan_replacement import canonical_replacement_steps
from agent.state_progression import (
    current_result_for_step,
    pending_effects,
    record_executed_effect,
    reset_task_progression,
    waive_effect,
)


class StatePlanExecutionMixin:
    plan: Plan
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
        plan: Plan | Sequence[Mapping[str, Any]],
        *,
        preserve_step_ids: bool = True,
    ) -> Plan:
        if isinstance(plan, Plan):
            return plan
        return Plan.from_raw(
            plan,
            new_step_id=cls._new_step_id,
            preserve_step_ids=preserve_step_ids,
        )

    def set_plan(self, plan: Plan | Sequence[Mapping[str, Any]]) -> None:
        """Substitui o plano e preserva registros de IDs sobreviventes."""

        normalized = self.canonicalize_plan_steps(plan)
        if not normalized:
            self.plan_identity = None
        elif self.plan_identity is None or not self.plan or self.plan != normalized:
            self.plan_identity = f"plan-{uuid.uuid4().hex}"
        records: Dict[str, StepExecutionRecord] = {}
        for step in normalized:
            step_id = step.step_id
            records[step_id] = self.step_records.get(step_id) or StepExecutionRecord(step_id=step_id)
        self.plan = normalized
        self.step_records = records
        if self.current_step_id not in records:
            self.current_step_id = None

    def set_plan_step(self, value: int) -> None:
        """Record the current plan cursor through the state owner."""

        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("plan step must be a non-negative integer")
        self.plan_step = value

    def reset_execution(self) -> None:
        self.plan = Plan()
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

    def insert_plan_step(
        self, index: int, step: PlanStep | Mapping[str, Any]
    ) -> None:
        if self.plan_identity is None:
            self.plan_identity = f"plan-{uuid.uuid4().hex}"
        if isinstance(step, (ToolPlanStep, DeferredConditionStep)):
            prepared = step
        elif isinstance(step, Plan):
            raise TypeError("insert_plan_step exige um único passo")
        else:
            prepared = Plan.from_raw(
                [step], new_step_id=self._new_step_id
            ).steps[0]
        self.plan = Plan(
            (*self.plan.steps[:index], prepared, *self.plan.steps[index:])
        )
        step_id = prepared.step_id
        self.step_records[step_id] = StepExecutionRecord(step_id=step_id)

    def remove_plan_step(self, index: int) -> None:
        step = self.plan.steps[index]
        self.plan = Plan((*self.plan.steps[:index], *self.plan.steps[index + 1 :]))
        self.step_records.pop(step.step_id, None)

    def replace_plan_step(
        self, index: int, new_steps: Sequence[PlanStep | Mapping[str, Any]]
    ) -> None:
        replacement = canonical_replacement_steps(self, index, new_steps)
        self.remove_plan_step(index)
        for offset, step in enumerate(replacement):
            self.insert_plan_step(index + offset, step)

    def get_step_id(self, index: int) -> str:
        return self.plan[index].step_id

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
        self.set_plan_step(index + 1)

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
