from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from uuid import uuid4

from agent.checkpoint_manager import CheckpointLoadError
from agent.final_response import compose_operational_answer
from agent.llm.router import _is_clearly_trivial
from agent.orchestration.route_coordinator import (
    LINEAR_ROUTE as _LINEAR_ROUTE,
)
from agent.orchestration.route_coordinator import (
    REACTIVE_ROUTE as _REACTIVE_ROUTE,
)
from agent.orchestration.route_coordinator import (
    SECURITY_ROUTE as _SECURITY_ROUTE,
)
from agent.orchestration.route_coordinator import (
    RouteCoordinatorMixin,
)
from agent.orchestration.route_result import RouteResult
from agent.orchestration.task_lifecycle import TaskLifecycleMixin
from agent.orchestration.task_runner_support import checkpoint_error_answer, terminal_answer
from agent.planning.complexity import is_hierarchical
from agent.planning.plan_builder import PlanningDecisionKind
from agent.planning.task_completion import (
    allow_linear_completion,
    complete_direct_answer,
    initialize_task_progression,
    mark_terminal_blocked,
)
from agent.reporting.operational_outcome import project_operational_outcome
from agent.runtime.budget import BudgetExhausted
from agent.runtime.logging import logger
from agent.watchdog import Watchdog


@dataclass
class TaskInputs:
    objective: str
    resumed: bool
    original_message_count: int


class TaskRunner(RouteCoordinatorMixin, TaskLifecycleMixin):
    """Coordinates one task lifecycle around the public Orchestrator facade."""
    def __init__(self, orchestrator: Any) -> None:
        self.orchestrator = orchestrator

    @staticmethod
    def _route_is_hierarchical(objective: str) -> bool:
        return is_hierarchical(objective)

    def run(
        self, objective: Optional[str], stream_callback: Callable[[str], None] | None
    ) -> str:
        original_count = len(self.orchestrator.session.messages)
        self.orchestrator._cancelled = False
        self.orchestrator._preserve_checkpoint = False
        self.orchestrator.cancellation_token.reset()
        try:
            inputs = self._resolve_inputs(objective, original_count)
            if inputs is None:
                self._reset_missing_input_state()
                return mark_terminal_blocked(
                    self.orchestrator,
                    reason_code="MISSING_REQUIRED_INPUT",
                    message="Nenhum objetivo foi fornecido e nenhum checkpoint valido foi encontrado.",
                )
            if inputs.resumed and self._has_terminal_disposition():
                return self._resume_terminal_checkpoint(inputs.objective)
            self._prepare(inputs)
            if not inputs.resumed and _is_clearly_trivial(inputs.objective):
                return complete_direct_answer(self.orchestrator, inputs.objective, str(self.orchestrator._answer_trivial(inputs.objective)))
            answer = self._execute(inputs, stream_callback)
            return answer
        except KeyboardInterrupt:
            return self._handle_interrupt()
        except CheckpointLoadError as exc:
            return checkpoint_error_answer(self.orchestrator, exc)
        except BudgetExhausted:
            self.orchestrator._preserve_checkpoint = True
            message = mark_terminal_blocked(
                self.orchestrator,
                reason_code=BudgetExhausted.code,
                message="A tarefa atingiu o limite de execucao e nao pode prosseguir agora.",
                status="block",
            )
            self.orchestrator._save_checkpoint()
            return message
        finally:
            self._cleanup(original_count)

    def _resolve_inputs(self, objective: Optional[str], original_count: int) -> TaskInputs | None:
        if objective:
            return TaskInputs(objective, False, original_count)
        checkpoint = self.orchestrator._load_checkpoint()
        if not checkpoint:
            return None
        try:
            self.orchestrator.agent_state.from_checkpoint_dict(
                checkpoint,
                retry_failed=bool(self.orchestrator.session.config.get("resume_retry_failed", False)),
                retry_skipped=bool(self.orchestrator.session.config.get("resume_retry_skipped", False)),
                effect_authority=self.orchestrator,
            )
        except ValueError:
            self.orchestrator._preserve_checkpoint = True
            objective = str(checkpoint.get("objective") or "checkpoint invalido")
            mark_terminal_blocked(
                self.orchestrator,
                reason_code="CHECKPOINT_INVALID_TERMINAL_DISPOSITION",
                message="O checkpoint contem um estado terminal incompatível e foi preservado.",
            )
            return TaskInputs(objective, True, original_count)
        restored = self.orchestrator.agent_state.objective
        if not restored:
            self.orchestrator._delete_checkpoint()
            return None
        print(f"\nCheckpoint encontrado. Retomando tarefa: \"{restored}\"")
        logger.info("Retomando tarefa a partir de checkpoint: %s", restored)
        return TaskInputs(str(restored), True, original_count)

    def _prepare(self, inputs: TaskInputs) -> None:
        if inputs.resumed:
            self.orchestrator._task_failed = False
        else:
            self.orchestrator._reset_task_state(inputs.objective)
            initialize_task_progression(self.orchestrator, inputs.objective)
        self.orchestrator._task_start_time = Watchdog.start_task()
        self.orchestrator._run_id = uuid4().hex
        self.orchestrator._run_metric_recorded = False
        self.orchestrator._metrics_start_line = self.orchestrator._count_metrics_lines()
        print(f"\nAnalisando: \"{inputs.objective}\"")
        logger.info("Iniciando objetivo do agente: %s", inputs.objective)

    def _execute(
        self, inputs: TaskInputs, on_chunk: Callable[[str], None] | None
    ) -> str:
        usage: Dict[str, int] = {}
        if inputs.resumed and self.orchestrator.agent_state.plan:
            plan = self._resume_plan()
            return self._execute_plan(
                plan,
                inputs.objective,
                usage,
                on_chunk,
                continue_after_plan=bool(
                    getattr(self.orchestrator.agent_state, "continue_after_plan", False)
                ),
            )
        self.orchestrator._route_persona(inputs.objective)
        self.orchestrator._save_checkpoint()
        hierarchical = self._try_hierarchical(inputs.objective, on_chunk)
        route_answer = self._consume_route_result(
            hierarchical,
            inputs.objective,
            next_route=_SECURITY_ROUTE,
        )
        if route_answer is not None:
            return route_answer
        security = self._try_security(inputs.objective, on_chunk)
        route_answer = self._consume_route_result(
            security,
            inputs.objective,
            next_route=_LINEAR_ROUTE,
        )
        if route_answer is not None:
            return route_answer
        decision = self.orchestrator.plan_builder.build_plan(inputs.objective)
        if decision.kind is PlanningDecisionKind.BLOCK:
            blocked_answer = str(decision.blocked_answer or "O planejamento bloqueou a tarefa antes da execucao.")
            self.orchestrator.agent_state.project_last_result(
                "planner",
                {},
                {
                    "ok": False,
                    "done": True,
                    "status": "blocked",
                    "executed": False,
                    "error": blocked_answer,
                    "message": blocked_answer,
                },
            )
            blocked = allow_linear_completion(self.orchestrator, inputs.objective) or blocked_answer
            return terminal_answer(self.orchestrator, inputs.objective, None, str(blocked))
        if decision.kind is PlanningDecisionKind.COMPLETE and decision.direct_answer:
            answer = complete_direct_answer(
                self.orchestrator, inputs.objective, str(decision.direct_answer))
            self.orchestrator.agent_state.conversation_history.append(
                {"user": inputs.objective, "agent": answer}
            )
            return answer
        if decision.kind is PlanningDecisionKind.REPLAN or not decision.plan:
            self._emit_route_transition(
                RouteResult.fallback(
                    _LINEAR_ROUTE,
                    reason_code=(
                        "PLANNER_REPLAN"
                        if decision.kind is PlanningDecisionKind.REPLAN
                        else "PLANNER_NO_PLAN"
                    ),
                ),
                reason_code=(
                    "PLANNER_REPLAN"
                    if decision.kind is PlanningDecisionKind.REPLAN
                    else "PLANNER_NO_PLAN"
                ),
                next_route=_REACTIVE_ROUTE,
                action="continue",
            )
            reactive_answer = str(
                self.orchestrator._run_reactive(
                    inputs.objective, usage, inputs.original_message_count
                )
            )
            outcome = project_operational_outcome(
                self.orchestrator.agent_state,
                task_failed=bool(getattr(self.orchestrator, "_task_failed", False)),
                cancelled=bool(getattr(self.orchestrator, "_cancelled", False)),
            )
            return str(
                compose_operational_answer(
                    outcome,
                    reactive_answer,
                    self.orchestrator.agent_state.tool_history,
                    getattr(self.orchestrator, "tool_registry", None),
                )
            )
        return self._execute_plan(
            decision.plan,
            inputs.objective,
            usage,
            on_chunk,
            continue_after_plan=decision.continue_after_plan,
        )
    def _resume_plan(self) -> List[Dict[str, Any]]:
        self.orchestrator._restore_persona_from_state()
        return [dict(step) for step in self.orchestrator.agent_state.plan]

    def _execute_plan(
        self, plan: List[Dict[str, Any]], objective: str, usage: Dict[str, int],
        on_chunk: Callable[[str], None] | None,
        *,
        continue_after_plan: bool = False,
    ) -> str:
        self.orchestrator.agent_state.continue_after_plan = continue_after_plan
        if continue_after_plan:
            result = self.orchestrator.execution_gateway.execute_validated_plan(
                plan, objective, usage, continue_after_plan=True
            )
        else:
            result = self.orchestrator.execution_gateway.execute_validated_plan(
                plan, objective, usage
            )
        if result.aborted:
            answer = result.final_answer or "A execução foi interrompida."
            mark_terminal_blocked(
                self.orchestrator,
                reason_code="EXECUTION_ABORTED",
                message=str(answer),
                status="block",
            )
            blocker = allow_linear_completion(self.orchestrator, objective)
            return terminal_answer(self.orchestrator, objective, on_chunk, str(blocker or answer))
        self.orchestrator.agent_state.set_plan(result.validated_plan)
        self.orchestrator._save_checkpoint()
        if result.final_answer:
            blocker = allow_linear_completion(self.orchestrator, objective)
            if blocker is not None:
                return terminal_answer(self.orchestrator, objective, on_chunk, blocker)
            outcome = project_operational_outcome(
                self.orchestrator.agent_state,
                task_failed=bool(getattr(self.orchestrator, "_task_failed", False)),
                cancelled=bool(getattr(self.orchestrator, "_cancelled", False)),
            )
            return str(
                self.orchestrator.final_responder.build_final_answer(
                    objective,
                    on_chunk=on_chunk,
                    operational_outcome=outcome,
                )
            )
        blocker = allow_linear_completion(self.orchestrator, objective)
        if blocker is not None:
            return terminal_answer(self.orchestrator, objective, on_chunk, blocker)
        outcome = project_operational_outcome(
            self.orchestrator.agent_state,
            task_failed=bool(getattr(self.orchestrator, "_task_failed", False)),
            cancelled=bool(getattr(self.orchestrator, "_cancelled", False)),
        )
        return str(
            self.orchestrator.final_responder.build_final_answer(
                objective,
                on_chunk=on_chunk,
                operational_outcome=outcome,
            )
        )
