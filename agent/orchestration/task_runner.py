from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, Dict, Optional, cast

from agent.checkpoint_manager import CheckpointLoadError
from agent.llm.router import _is_clearly_trivial
from agent.orchestration.route_coordinator import RouteCoordinatorMixin
from agent.orchestration.task_definition_gate import (
    ensure_task_definition,
    preserve_task_definition_checkpoint,
    revalidate_resume_task_definition,
)
from agent.orchestration.task_directive_runtime import (
    TaskDirectiveRuntimeRestore,
    apply_task_run_directive_runtime,
    restore_task_run_directive_runtime,
)
from agent.orchestration.task_execution import execute_task
from agent.orchestration.task_lifecycle import TaskLifecycleMixin
from agent.orchestration.task_runner_continuity import (
    ExplicitResumeRefused,
    TaskInputs,
    emit_resume_event,
    handle_explicit_resume_interrupt,
    resolve_inputs,
    resume_refusal_message,
)
from agent.orchestration.task_runner_resume_commit import start_explicit_resume_attempt
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
from agent.runtime.event_kinds import RuntimeEventKind
from agent.runtime.logging import logger
from agent.runtime.operational_outcome import project_operational_outcome
from agent.runtime.task_directives import TaskDirective, TaskRunDirective
from agent.runtime.task_policy import TaskPolicyError
from agent.task_definition.errors import TaskDefinitionError
from agent.watchdog import Watchdog


class TaskRunner(RouteCoordinatorMixin, TaskLifecycleMixin):
    """Coordinates one task lifecycle around the public Orchestrator facade."""

    def __init__(self, orchestrator: Any) -> None:
        self.orchestrator = orchestrator
        self._resume_task_definition_admitted = False
        self._resume_commit_failed = False
        self._resume_attempt_committed = False
        self._resume_commit_expectation: Any = None
        self._directive_runtime_restore: TaskDirectiveRuntimeRestore | None = None

    def _route_is_hierarchical(self, objective: str) -> bool:
        state = getattr(self.orchestrator, "agent_state", None)
        directive = getattr(state, "task_run_directive", None)
        if isinstance(directive, TaskRunDirective) and not directive.hierarchical_allowed():
            return False
        return cast(bool, is_hierarchical(objective))

    def _start_observation(self, inputs: TaskInputs) -> None:
        start_observation = getattr(self.orchestrator, "_on_observation_run_started", None)
        if not callable(start_observation):
            return
        try:
            start_observation(self.orchestrator.run_correlation, resumed=inputs.resumed)
        except Exception as exc:
            # Observation is an isolated projection; it cannot prevent
            # the canonical task lifecycle from starting.
            logger.warning("Observation session startup failed: %s", type(exc).__name__)

    def run(
        self,
        objective: Optional[str],
        stream_callback: Callable[[str], None] | None,
        *,
        explicit_resume: bool = False,
        task_run_directive: TaskRunDirective | None = None,
    ) -> str:
        original_count = len(self.orchestrator.session.messages)
        self.orchestrator._cancelled = False
        self.orchestrator._preserve_checkpoint = False
        self.orchestrator.cancellation_token.reset()
        self._resume_task_definition_admitted = False
        self._resume_commit_failed = False
        self._resume_attempt_committed = False
        self._resume_commit_expectation = None
        self._directive_runtime_restore = None
        try:
            inputs = self._resolve_inputs(
                objective,
                original_count,
                explicit_resume=explicit_resume,
                task_run_directive=task_run_directive,
            )
            if inputs is None:
                self._reset_missing_input_state()
                return cast(str, mark_terminal_blocked(
                    self.orchestrator,
                    reason_code="MISSING_REQUIRED_INPUT",
                    message="Nenhum objetivo foi fornecido e nenhum checkpoint valido foi encontrado.",
                ))
            self._admit_and_start_attempt(inputs, explicit_resume=explicit_resume)
            if inputs.resumed and self._has_terminal_disposition():
                return cast(str, self._resume_terminal_checkpoint(inputs.objective))
            self._prepare(inputs)
            if (
                not inputs.resumed
                and inputs.task_run_directive is not None
                and inputs.task_run_directive.trivial_shortcut_allowed()
                and _is_clearly_trivial(inputs.objective)
            ):
                return cast(str, complete_direct_answer(
                    self.orchestrator,
                    inputs.objective,
                    str(self.orchestrator._answer_trivial(inputs.objective)),
                ))
            definition_answer = self._ensure_task_definition(inputs)
            if definition_answer is not None:
                return definition_answer
            return cast(str, self._execute(inputs, stream_callback))
        except KeyboardInterrupt:
            return cast(
                str,
                handle_explicit_resume_interrupt(self, explicit_resume)
                or self._handle_interrupt(),
            )
        except ExplicitResumeRefused as exc:
            self.orchestrator._preserve_checkpoint = True
            self.orchestrator._resume_refusal_reason = exc.reason_code
            self.orchestrator._last_failure_code = exc.reason_code
            return cast(str, resume_refusal_message(exc.reason_code))
        except CheckpointLoadError as exc:
            return cast(str, checkpoint_error_answer(self.orchestrator, exc))
        except TaskPolicyError as exc:
            return self._handle_policy_error(exc)
        except BudgetExhausted:
            return self._handle_budget_exhausted()
        finally:
            # A failed explicit resume has only provisional in-memory state;
            # cleanup must not turn that failed admission into another effect.
            self._cleanup_after_run(original_count)

    def _start_attempt(self, inputs: TaskInputs, *, commit_resume: bool = False) -> None:
        start_correlation = getattr(self.orchestrator, "_start_run_correlation", None)
        if commit_resume:
            start_explicit_resume_attempt(self, start_correlation)
        elif callable(start_correlation):
            start_correlation(resumed=inputs.resumed)
        self._start_observation(inputs)
        if inputs.resumed:
            self._emit_resume_event(inputs)

    def _admit_and_start_attempt(self, inputs: TaskInputs, *, explicit_resume: bool) -> None:
        if explicit_resume and inputs.resumed:
            self._admit_resume_task_definition()
            self._start_attempt(inputs, commit_resume=True)
            return
        self._start_attempt(inputs)

    def _cleanup_after_run(self, original_count: int) -> None:
        try:
            if not self._resume_commit_failed:
                self._cleanup(original_count)
        finally:
            restore = self._directive_runtime_restore
            self._directive_runtime_restore = None
            restore_task_run_directive_runtime(self.orchestrator, restore)

    def _admit_resume_task_definition(self) -> None:
        try:
            reference = revalidate_resume_task_definition(self)
        except TaskDefinitionError as exc:
            raise ExplicitResumeRefused(exc.reason_code) from exc
        self.orchestrator.agent_state.task_definition_ref = reference
        self._resume_task_definition_admitted = True

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
        return cast(str, message)

    def _resolve_inputs(
        self,
        objective: Optional[str],
        original_count: int,
        *,
        explicit_resume: bool = False,
        task_run_directive: TaskRunDirective | None = None,
    ) -> TaskInputs | None:
        return resolve_inputs(
            self,
            objective,
            original_count,
            explicit_resume=explicit_resume,
            task_run_directive=task_run_directive,
        )

    def _emit_resume_event(self, inputs: TaskInputs) -> None:
        emit_resume_event(self, inputs)

    def _prepare(self, inputs: TaskInputs) -> None:
        if inputs.resumed:
            self.orchestrator._task_failed = False
        else:
            self.orchestrator._reset_task_state(inputs.objective)
            input_directive = cast(TaskRunDirective, inputs.task_run_directive)
            self.orchestrator.agent_state.task_run_directive = input_directive
            initialize_task_progression(
                self.orchestrator,
                inputs.objective,
                plan_only=input_directive.directive is TaskDirective.PLAN,
            )
        state = getattr(self.orchestrator, "agent_state", None)
        state_directive: object = getattr(state, "task_run_directive", None)
        if isinstance(state_directive, TaskRunDirective):
            task_run_directive = state_directive
        else:
            fallback_directive: object = inputs.task_run_directive
            if not isinstance(fallback_directive, TaskRunDirective):
                raise ValueError("TaskRunner requires an admitted TaskRunDirective")
            task_run_directive = fallback_directive
            if state is not None:
                state.task_run_directive = task_run_directive
        if getattr(self.orchestrator, "session", None) is not None:
            self._directive_runtime_restore = apply_task_run_directive_runtime(
                self.orchestrator,
                task_run_directive,
            )
        self._emit_task_directive_selected(inputs)
        self.orchestrator._task_start_time = Watchdog.start_task()
        policy = getattr(self.orchestrator, "task_policy", None)
        if policy is not None:
            policy.start_active_segment()
        self.orchestrator._run_metric_recorded = False
        self.orchestrator._metrics_start_line = self.orchestrator._count_metrics_lines()
        print(chr(10) + 'Analisando: "' + inputs.objective + '"')
        logger.info("Iniciando objetivo do agente: %s", inputs.objective)

    def _emit_task_directive_selected(self, inputs: TaskInputs) -> None:
        """Publish a bounded W11 selection fact after runtime application."""

        directive = getattr(getattr(self.orchestrator, "agent_state", None), "task_run_directive", None)
        emit = getattr(self.orchestrator, "_emit", None)
        if not isinstance(directive, TaskRunDirective) or not callable(emit):
            return
        try:
            emit(
                RuntimeEventKind.TASK_DIRECTIVE_SELECTED.value,
                {
                    "directive": directive.directive.value,
                    "deliberation_profile": directive.deliberation_profile.value,
                    "resumed": bool(inputs.resumed),
                },
            )
        except Exception as exc:
            # Event observers are projections; they cannot change task truth.
            logger.warning("W11 directive event emission failed: %s", type(exc).__name__)

    def _ensure_task_definition(self, inputs: TaskInputs) -> str | None:
        return cast(str | None, ensure_task_definition(self, inputs))

    def _preserve_task_definition_checkpoint(self) -> None:
        preserve_task_definition_checkpoint(self)

    def _execute(
        self, inputs: TaskInputs, on_chunk: Callable[[str], None] | None
    ) -> str:
        return cast(str, execute_task(self, inputs, on_chunk))

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
            return cast(str, terminal_answer(
                self.orchestrator, objective, on_chunk, str(blocker or answer)
            ))
        self.orchestrator.agent_state.set_plan(result.validated_plan)
        self.orchestrator._save_checkpoint()
        if result.final_answer:
            blocker = self._allow_linear_completion(objective)
            if blocker is not None:
                return cast(str, terminal_answer(self.orchestrator, objective, on_chunk, blocker))
            return self._final_plan_answer(objective, on_chunk)
        blocker = self._allow_linear_completion(objective)
        if blocker is not None:
            return cast(str, terminal_answer(self.orchestrator, objective, on_chunk, blocker))
        return self._final_plan_answer(objective, on_chunk)

    def _allow_linear_completion(self, objective: str) -> str | None:
        return cast(str | None, allow_linear_completion(self.orchestrator, objective))

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
