from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from uuid import uuid4

from agent.llm.router import _is_clearly_trivial
from agent.planning.complexity import is_hierarchical
from agent.planning.plan_builder import PlanningDecisionKind
from agent.planning.task_completion import (
    allow_linear_completion,
    complete_direct_answer,
    initialize_task_progression,
    mark_terminal_failure,
)
from agent.reporting.operational_outcome import project_operational_outcome
from agent.runtime.logging import logger
from agent.watchdog import Watchdog


@dataclass
class TaskInputs:
    objective: str
    resumed: bool
    original_message_count: int


class TaskRunner:
    """Coordinates one task lifecycle around the public Orchestrator facade."""

    def __init__(self, orchestrator: Any) -> None:
        self.orchestrator = orchestrator

    def run(
        self, objective: Optional[str], stream_callback: Callable[[str], None] | None
    ) -> str:
        original_count = len(self.orchestrator.session.messages)
        self.orchestrator._cancelled = False
        self.orchestrator.cancellation_token.reset()
        try:
            inputs = self._resolve_inputs(objective, original_count)
            if inputs is None:
                return "Nenhum objetivo foi fornecido e nenhum checkpoint válido foi encontrado."
            self._prepare(inputs)
            if not inputs.resumed and _is_clearly_trivial(inputs.objective):
                return str(self.orchestrator._answer_trivial(inputs.objective))
            answer = self._execute(inputs, stream_callback)
            return answer
        except KeyboardInterrupt:
            return self._handle_interrupt()
        finally:
            self._cleanup(original_count)

    def _resolve_inputs(self, objective: Optional[str], original_count: int) -> TaskInputs | None:
        if objective:
            return TaskInputs(objective, False, original_count)
        checkpoint = self.orchestrator._load_checkpoint()
        if not checkpoint:
            return None
        self.orchestrator.agent_state.from_checkpoint_dict(
            checkpoint,
            retry_failed=bool(self.orchestrator.session.config.get("resume_retry_failed", False)),
            retry_skipped=bool(self.orchestrator.session.config.get("resume_retry_skipped", False)),
        )
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
            return self._execute_plan(plan, inputs.objective, usage, on_chunk)
        self.orchestrator._route_persona(inputs.objective)
        self.orchestrator._save_checkpoint()
        hierarchical = self._try_hierarchical(inputs.objective, on_chunk)
        if hierarchical is not None:
            return hierarchical
        security = self._try_security(inputs.objective, on_chunk)
        if security is not None:
            return security
        decision = self.orchestrator.plan_builder.build_plan(inputs.objective)
        if decision.kind is PlanningDecisionKind.BLOCK and decision.blocked_answer:
            self.orchestrator.agent_state.terminal_disposition = "block"
            self.orchestrator.agent_state.conversation_history.append(
                {"user": inputs.objective, "agent": decision.blocked_answer}
            )
            return str(decision.blocked_answer)
        if decision.kind is PlanningDecisionKind.COMPLETE and decision.direct_answer:
            answer = complete_direct_answer(
                self.orchestrator, inputs.objective, str(decision.direct_answer)
            )
            self.orchestrator.agent_state.conversation_history.append(
                {"user": inputs.objective, "agent": answer}
            )
            return answer
        if decision.kind is PlanningDecisionKind.REPLAN or not decision.plan:
            return str(self.orchestrator._run_reactive(inputs.objective, usage, inputs.original_message_count))
        return self._execute_plan(decision.plan, inputs.objective, usage, on_chunk)

    def _resume_plan(self) -> List[Dict[str, Any]]:
        self.orchestrator._restore_persona_from_state()
        return [dict(step) for step in self.orchestrator.agent_state.plan]

    def _try_hierarchical(
        self, objective: str, on_chunk: Callable[[str], None] | None
    ) -> str | None:
        return self.orchestrator._run_hierarchical(objective, on_chunk) if is_hierarchical(objective) else None

    def _try_security(
        self, objective: str, on_chunk: Callable[[str], None] | None
    ) -> str | None:
        if not self.orchestrator._is_security_objective(objective):
            return None
        answer = self.orchestrator._handle_security_analysis(objective, on_chunk)
        return str(answer) if answer is not None else None

    def _execute_plan(
        self, plan: List[Dict[str, Any]], objective: str, usage: Dict[str, int],
        on_chunk: Callable[[str], None] | None,
    ) -> str:
        result = self.orchestrator.execution_gateway.execute_validated_plan(plan, objective, usage)
        if result.aborted:
            mark_terminal_failure(self.orchestrator)
            answer = result.final_answer or "A execução foi interrompida."
            self.orchestrator.agent_state.conversation_history.append({"user": objective, "agent": answer})
            return answer
        self.orchestrator.agent_state.set_plan(result.validated_plan)
        self.orchestrator._save_checkpoint()
        if result.final_answer:
            blocker = allow_linear_completion(self.orchestrator, objective)
            if blocker is not None:
                return blocker
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
            return blocker
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

    def _handle_interrupt(self) -> str:
        self.orchestrator._cancelled = True
        self.orchestrator.cancellation_token.cancel()
        self.orchestrator._save_checkpoint()
        return "Tarefa cancelada pelo usuário. O progresso foi salvo e pode ser retomado posteriormente."

    def _cleanup(self, original_count: int) -> None:
        orchestrator = self.orchestrator
        try:
            if orchestrator._task_failed:
                orchestrator.workspace.rollback()
            while len(orchestrator.session.messages) > original_count:
                orchestrator.session.messages.pop()
            maximum = orchestrator.agent_state.max_history_turns
            orchestrator.agent_state.conversation_history = orchestrator.agent_state.conversation_history[-maximum:]
            orchestrator.context_manager.maybe_compress_context()
            try:
                orchestrator._persist_memory_to_file()
            except Exception:
                orchestrator._task_failed = True
                logger.exception("Falha ao persistir memória ao finalizar a tarefa.")
                raise
            if not orchestrator._cancelled:
                orchestrator._delete_checkpoint()
        except Exception:
            raise
