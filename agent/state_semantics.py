"""AgentState projection methods for the canonical task semantic owner."""

from __future__ import annotations

from typing import Any, List, Optional, Sequence

from agent.planning.task_semantics import ObligationStatus, TaskObligation, TaskSemantics


class TaskSemanticsStateMixin:
    """Expose compatibility properties without owning semantic truth."""

    _terminal_disposition: str | None
    objective: str | None

    @property
    def task_semantics(self) -> TaskSemantics:
        return self._task_semantics

    @property
    def terminal_disposition(self) -> Optional[str]:
        return self._terminal_disposition

    @terminal_disposition.setter
    def terminal_disposition(self, value: Optional[str]) -> None:
        self.set_terminal_disposition(value)

    def set_terminal_disposition(self, value: Optional[str]) -> None:
        if value is not None and not isinstance(value, str):
            raise TypeError("disposicao terminal invalida")
        self._terminal_disposition = value

    def clear_terminal_disposition(self) -> None:
        self.set_terminal_disposition(None)

    def set_task_semantics(self, semantics: TaskSemantics) -> None:
        if not isinstance(semantics, TaskSemantics):
            raise TypeError("semantics de tarefa invalida")
        self._task_semantics = semantics

    def initialize_task_semantics(self, objective: str) -> None:
        self.objective = objective
        self._task_semantics = TaskSemantics.from_objective(objective)

    @property
    def task_intent(self) -> Any:
        return self._task_semantics.intent

    @property
    def task_obligations(self) -> tuple[TaskObligation, ...]:
        return self._task_semantics.obligations

    @property
    def requested_effects(self) -> List[str]:
        return list(self._task_semantics.requested_effects)

    @requested_effects.setter
    def requested_effects(self, effects: Sequence[str]) -> None:
        self._task_semantics.replace_effects(effects, self._task_semantics.prohibited_effects)

    @property
    def executed_effects(self) -> List[str]:
        return list(self._task_semantics.executed_effects())

    @executed_effects.setter
    def executed_effects(self: Any, effects: Sequence[str]) -> None:
        for effect in effects:
            self.record_executed_effect(str(effect), allow_legacy=True)

    @property
    def waived_effects(self) -> List[str]:
        return list(self._task_semantics.waived_effects())

    @waived_effects.setter
    def waived_effects(self: Any, effects: Sequence[str]) -> None:
        for effect in effects:
            self.waive_effect(str(effect), allow_legacy=True)

    @property
    def prohibited_effects(self) -> List[str]:
        return list(self._task_semantics.prohibited_effects)

    @prohibited_effects.setter
    def prohibited_effects(self, effects: Sequence[str]) -> None:
        self._task_semantics.replace_effects(self._task_semantics.requested_effects, effects)

    def review_task_obligations(self, raw: Any, *, source: str) -> tuple[TaskObligation, ...]:
        return self._task_semantics.review_and_add_obligations(raw, source=source)

    def pending_obligations(self) -> tuple[TaskObligation, ...]:
        return self._task_semantics.pending_obligations()

    def blocked_obligations(self) -> tuple[TaskObligation, ...]:
        return self._task_semantics.blocked_obligations()

    def terminal_evidence_complete(self) -> bool:
        return self._task_semantics.terminal_evidence_complete()

    def prohibited_effects_occurred(self) -> tuple[str, ...]:
        return self._task_semantics.prohibited_effects_occurred()

    def obligation_status(self, obligation_id: str) -> ObligationStatus:
        return self._task_semantics.obligation_status(obligation_id)

    def satisfy_obligation(
        self,
        obligation_id: str,
        *,
        evidence_ref: int | str,
        effect_authority: Any = None,
    ) -> None:
        self._task_semantics.satisfy(
            obligation_id,
            evidence_ref=evidence_ref,
            effect_authority=effect_authority,
        )

    def waive_obligation(
        self,
        obligation_id: str,
        *,
        evidence_ref: int | str,
        effect_authority: Any = None,
    ) -> None:
        self._task_semantics.waive(
            obligation_id,
            evidence_ref=evidence_ref,
            effect_authority=effect_authority,
        )

    def block_obligation(
        self,
        obligation_id: str,
        *,
        evidence_ref: int | str,
        effect_authority: Any = None,
    ) -> None:
        self._task_semantics.block(
            obligation_id,
            evidence_ref=evidence_ref,
            effect_authority=effect_authority,
        )
