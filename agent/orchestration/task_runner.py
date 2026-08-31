from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Dict, Optional

from agent.checkpoint_manager import CheckpointLoadError
from agent.llm.router import _is_clearly_trivial
from agent.orchestration.route_coordinator import RouteCoordinatorMixin
from agent.orchestration.task_definition_gate import (
    ensure_task_definition,
    preserve_task_definition_checkpoint,
)
from agent.orchestration.task_execution import execute_task
from agent.orchestration.task_lifecycle import TaskLifecycleMixin
from agent.orchestration.task_runner_support import checkpoint_error_answer, terminal_answer
from agent.planning.complexity import is_hierarchical
from agent.planning.plan_model import Plan
from agent.planning.task_completion import (
    allow_linear_completion,
    complete_direct_answer,
    initialize_task_progression,
    mark_terminal_blocked,
)
from agent.planning.task_policy_support import policy_terminal_answer
from agent.runtime.budget import BudgetExhausted
from agent.runtime.logging import logger
from agent.runtime.operational_outcome import project_operational_outcome
from agent.runtime.task_policy import TaskPolicyError
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
            start_correlation = getattr(self.orchestrator, "_start_run_correlation", None)
            if callable(start_correlation):
                start_correlation(resumed=inputs.resumed)
            if inputs.resumed and self._has_terminal_disposition():
                return self._resume_terminal_checkpoint(inputs.objective)
            self._prepare(inputs)
            if not inputs.resumed and _is_clearly_trivial(inputs.objective):
                return complete_direct_answer(
                    self.orchestrator,
                    inputs.objective,
                    str(self.orchestrator._answer_trivial(inputs.objective)),
                )
            definition_answer = self._ensure_task_definition(inputs)
            if definition_answer is not None:
                return definition_answer
            return self._execute(inputs, stream_callback)
        except KeyboardInterrupt:
            return self._handle_interrupt()
        except CheckpointLoadError as exc:
            return checkpoint_error_answer(self.orchestrator, exc)
        except TaskPolicyError as exc:
            return self._handle_policy_error(exc)
        except BudgetExhausted:
            return self._handle_budget_exhausted()
        finally:
            self._cleanup(original_count)

    def _handle_policy_error(self, exc: TaskPolicyError) -> str:
        self.orchestrator._preserve_checkpoint = True
        answer = policy_terminal_answer(self.orchestrator, exc.result)
        self.orchestrator._save_checkpoint()
        return str(answer or exc)

    def _handle_budget_exhausted(self) -> str:
        self.orchestrator._preserve_checkpoint = True
        compiler = getattr(self.orchestrator, "task_definition_compiler", None)
        partial = getattr(compiler, "last_ref", None)
        if partial is not None:
            self.orchestrator.agent_state.task_definition_ref = partial
        message = mark_terminal_blocked(
            self.orchestrator,
            reason_code=BudgetExhausted.code,
            message="A tarefa atingiu o limite de execucao e nao pode prosseguir agora.",
            status="block",
        )
        self.orchestrator._save_checkpoint()
        return message

    def _resolve_inputs(self, objective: Optional[str], original_count: int) -> TaskInputs | None:
        if objective:
            return TaskInputs(objective, False, original_count)
        checkpoint = self.orchestrator._load_checkpoint()
        if not checkpoint:
            return None
        try:
            self.orchestrator.agent_state.from_checkpoint_dict(
                checkpoint,
                retry_failed=bool(
                    self.orchestrator.session.config.get("resume_retry_failed", False)
                ),
                retry_skipped=bool(
                    self.orchestrator.session.config.get("resume_retry_skipped", False)
                ),
                effect_authority=self.orchestrator,
                admission_authority=getattr(
                    self.orchestrator, "admission_authority", None
                ),
            )
            refresh_policy = getattr(self.orchestrator, "_refresh_task_policy", None)
            if callable(refresh_policy):
                refresh_policy()
        except ValueError:
            self.orchestrator._preserve_checkpoint = True
            self.orchestrator.agent_state.root_task_id = None
            objective = str(checkpoint.get("objective") or "checkpoint invalido")
            mark_terminal_blocked(
                self.orchestrator,
                reason_code="CHECKPOINT_INVALID_TERMINAL_DISPOSITION",
                message="O checkpoint contem um estado terminal incompativel e foi preservado.",
            )
            return TaskInputs(objective, True, original_count)
        lifecycle = getattr(self.orchestrator.agent_state, "hierarchical_lifecycle", {})
        if isinstance(lifecycle, Mapping) and lifecycle.get("status") == "running":
            self.orchestrator._preserve_checkpoint = True
            mark_terminal_blocked(
                self.orchestrator,
                reason_code="HIERARCHICAL_RESUME_UNSUPPORTED",
                message=(
                    "A execucao hierarquica interrompida nao pode ser retomada "
                    "com seguranca; o checkpoint foi preservado para auditoria."
                ),
                status="block",
            )
            return TaskInputs(str(self.orchestrator.agent_state.objective or "checkpoint"), True, original_count)
        restored = self.orchestrator.agent_state.objective
        if not restored:
            self.orchestrator._delete_checkpoint()
            return None
        print(chr(10) + 'Checkpoint encontrado. Retomando tarefa: "' + str(restored) + '"')
        logger.info("Retomando tarefa a partir de checkpoint: %s", restored)
        return TaskInputs(str(restored), True, original_count)

    def _prepare(self, inputs: TaskInputs) -> None:
        if inputs.resumed:
            self.orchestrator._task_failed = False
        else:
            self.orchestrator._reset_task_state(inputs.objective)
            initialize_task_progression(self.orchestrator, inputs.objective)
        self.orchestrator._task_start_time = Watchdog.start_task()
        policy = getattr(self.orchestrator, "task_policy", None)
        if policy is not None:
            policy.start_active_segment()
        self.orchestrator._run_metric_recorded = False
        self.orchestrator._metrics_start_line = self.orchestrator._count_metrics_lines()
        print(chr(10) + 'Analisando: "' + inputs.objective + '"')
        logger.info("Iniciando objetivo do agente: %s", inputs.objective)

    def _ensure_task_definition(self, inputs: TaskInputs) -> str | None:
        return ensure_task_definition(self, inputs)

    def _preserve_task_definition_checkpoint(self) -> None:
        preserve_task_definition_checkpoint(self)

    def _execute(
        self, inputs: TaskInputs, on_chunk: Callable[[str], None] | None
    ) -> str:
        return execute_task(self, inputs, on_chunk)

    def _execute_plan(
        self,
        plan: Plan | Sequence[Mapping[str, Any]],
        objective: str,
        usage: Dict[str, int],
        on_chunk: Callable[[str], None] | None,
        *,
        continue_after_plan: bool = False,
        planning_view: Any = None,
    ) -> str:
        self.orchestrator.agent_state.continue_after_plan = continue_after_plan
        gateway_kwargs: Dict[str, Any] = (
            {"planning_view": planning_view} if planning_view is not None else {}
        )
        if continue_after_plan:
            gateway_kwargs["continue_after_plan"] = True
        result = self.orchestrator.execution_gateway.execute_validated_plan(
            plan, objective, usage, **gateway_kwargs
        )
        if result.aborted:
            answer = result.final_answer or "A execucao foi interrompida."
            mark_terminal_blocked(
                self.orchestrator,
                reason_code="EXECUTION_ABORTED",
                message=str(answer),
                status="block",
            )
            blocker = self._allow_linear_completion(objective)
            return terminal_answer(
                self.orchestrator, objective, on_chunk, str(blocker or answer)
            )
        self.orchestrator.agent_state.set_plan(result.validated_plan)
        self.orchestrator._save_checkpoint()
        if result.final_answer:
            blocker = self._allow_linear_completion(objective)
            if blocker is not None:
                return terminal_answer(self.orchestrator, objective, on_chunk, blocker)
            return self._final_plan_answer(objective, on_chunk)
        blocker = self._allow_linear_completion(objective)
        if blocker is not None:
            return terminal_answer(self.orchestrator, objective, on_chunk, blocker)
        return self._final_plan_answer(objective, on_chunk)

    def _allow_linear_completion(self, objective: str) -> str | None:
        return allow_linear_completion(self.orchestrator, objective)

    def _final_plan_answer(self, objective: str, on_chunk: Callable[[str], None] | None) -> str:
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
