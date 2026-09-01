from __future__ import annotations

from typing import Any, Dict, List, Optional, cast

from agent.contracts import EventData, ToolArgs
from agent.error_handler import ErrorHandler
from agent.orchestration.operations_lifecycle import event_plan_step_ids, handle_step_failure, is_task_solved
from agent.orchestration.operations_reporting import (
    count_metrics_lines,
    get_metrics_for_task,
    record_canonical_run_metric,
)
from agent.orchestration.operations_tools import build_tools_description
from agent.reporting.run_snapshot import CanonicalRunSnapshot
from agent.reporting.task_report import TaskReportBuilder
from agent.runtime.events import RuntimeEvent
from agent.runtime.failures import FailureFact
from agent.runtime.logging import logger
from agent.tools.contracts import ToolResult as CanonicalToolResult


class OrchestratorOperations:
    """Infrastructure adapters shared by planning and execution components."""

    skills: Dict[str, Any]
    active_skills: List[str]
    agent_state: Any
    session: Any
    verbose: bool
    checkpoint_manager: Any
    metrics_recorder: Any
    _metrics_start_line: int
    _run_id: str | None
    event_dispatcher: Any
    _task_failed: bool
    _cancelled: bool
    cancellation_token: Any
    context_manager: Any
    workspace: Any
    reactive_loop: Any
    tool_executor: Any

    def register_skill(self, skill: Any) -> None:
        self.skills[skill.name] = skill

    def unregister_skill(self, name: str) -> None:
        self.skills.pop(name, None)

    def _build_tools_description(
        self,
        compact: bool = False,
        *,
        planner_kind: str | None = None,
    ) -> str:
        return build_tools_description(self, compact, planner_kind=planner_kind)

    def remember(
        self,
        key: str,
        value: Any,
        section: str = "key_findings",
        *,
        cancellation_token: Any | None = None,
        cancellation_event: Any | None = None,
    ) -> None:
        self.agent_state.memory.remember(
            key,
            value,
            section,
            cancellation_token=cancellation_token,
            cancellation_event=cancellation_event,
        )

    def forget(
        self,
        key: str,
        *,
        cancellation_token: Any | None = None,
        cancellation_event: Any | None = None,
    ) -> None:
        self.agent_state.memory.forget(
            key,
            cancellation_token=cancellation_token,
            cancellation_event=cancellation_event,
        )

    def clear_memory(self) -> None:
        self.agent_state.memory.clear()
        self.agent_state.events.clear()

    memory_file: str

    def save_memory_to_file(self, path: str | None = None) -> str:
        return str(self.agent_state.memory.save_to_file(path or self.memory_file))

    def _persist_memory_to_file(self, path: str | None = None) -> None:
        """Persiste memória sem converter falhas em uma falsa resposta de sucesso."""

        self.agent_state.memory.persist_to_file(path or self.memory_file)

    def load_memory_from_file(self, path: str | None = None) -> str:
        return str(self.agent_state.memory.load_from_file(path or self.memory_file))

    def _restore_memory_from_file(self, path: str | None = None) -> None:
        """Restaura memória no bootstrap sem ocultar corrupção ou falha de I/O."""

        self.agent_state.memory.restore_from_file(path or self.memory_file)

    def _save_checkpoint(self) -> bool:
        gateway = getattr(self, "tool_invocation_gateway", None)
        quiescent = getattr(gateway, "are_invocations_quiescent", None)
        if callable(quiescent) and not quiescent(mutating_only=True):
            OrchestratorOperations._emit_checkpoint_event(
                self,
                "checkpoint_deferred",
                {"reason": "task-owned mutating invocation is not quiescent"},
            )
            logger.warning("Checkpoint deferred while a mutating invocation is active.")
            return False
        try:
            saved = self.checkpoint_manager.save(self.agent_state)
        except Exception as exc:
            logger.warning("Checkpoint persistence failed: %s", type(exc).__name__)
            saved = False
        # CheckpointManager returns a strict bool.  Completion without an
        # explicit True is not a durable confirmation.
        if saved is not True:
            OrchestratorOperations._emit_checkpoint_event(
                self,
                "checkpoint_persistence_failed",
                {"reason": "checkpoint write was not confirmed"},
            )
            return False
        return True

    def _emit_checkpoint_event(self, event_type: str, data: EventData) -> None:
        OrchestratorOperations._emit(self, event_type, data)

    def _load_checkpoint(self) -> Optional[Dict[str, Any]]:
        return cast(Optional[Dict[str, Any]], self.checkpoint_manager.load())

    def _delete_checkpoint(self) -> None:
        self.checkpoint_manager.delete()
    def _emit(self, event_type: str, data: Optional[EventData] = None) -> None:
        ensure_correlation = getattr(self, "_ensure_run_correlation", None)
        if not callable(ensure_correlation):
            raise RuntimeError("orchestrator does not expose the canonical runtime correlation owner")
        correlation = ensure_correlation()
        raw_data = data or {}
        plan_id, step_id = event_plan_step_ids(raw_data, self.agent_state)
        event = RuntimeEvent.from_fields(
            event_type,
            correlation,
            raw_data,
            plan_id=plan_id,
            step_id=step_id,
            step=self.agent_state.plan_step,
        )
        dispatcher = getattr(self, "event_dispatcher", None)
        emit_event = getattr(dispatcher, "emit", None)
        if not callable(emit_event):
            raise RuntimeError(
                "orchestrator does not expose the canonical runtime event dispatcher"
            )
        emit_event(event)
        if self.verbose:
            print(f"[{event_type}] {data}")

    def _log_metric(self, entry: Dict[str, Any]) -> None:
        enriched = dict(entry)
        correlation = getattr(self, "_run_correlation", None)
        if correlation is not None:
            enriched.setdefault("run_id", correlation.run_id)
            enriched.setdefault("root_task_id", correlation.root_task_id)
            enriched.setdefault("task_id", correlation.task_id)
        elif getattr(self, "_run_id", None) is not None:
            enriched.setdefault("run_id", self._run_id)
        self.metrics_recorder.log_metric(enriched)

    def _record_canonical_run_metric(self, success: bool) -> None:
        record_canonical_run_metric(self, success)

    def _count_metrics_lines(self) -> int:
        return count_metrics_lines(self)

    def _get_metrics_for_task(self) -> List[Dict[str, Any]]:
        return get_metrics_for_task(self)

    def _generate_task_report(
        self,
        final_answer: str,
        *,
        status: str | None = None,
        error: str | None = None,
        receipt: Dict[str, Any] | None = None,
        snapshot: CanonicalRunSnapshot | None = None,
    ) -> str | None:
        try:
            config = (self.session.config or {}).get("task_report", {}) or {}
            if not config.get("enabled", True):
                return None
            if status is None:
                logger.warning("Task report requires canonical execution status")
                return None
            builder = TaskReportBuilder(self.session.config)
            report = builder.build_report(
                self.agent_state,
                [] if snapshot is not None else self._get_metrics_for_task(),
                final_answer,
                canonical_outcome={"status": status, "error": error},
                receipt=receipt,
                snapshot=snapshot,
            )
            path = builder.save_report(report, format=config.get("format", "json"))
            saved_path = str(path)
            if self.verbose:
                print(f"Relatório da tarefa salvo em: {path}")
            return saved_path
        except Exception as exc:
            logger.warning("Task report generation failed: %s", exc)
            return None

    def _is_task_solved(self) -> bool:
        return is_task_solved(self)

    @staticmethod
    def _sanitize_error(error_message: str) -> str:
        return str(ErrorHandler.sanitize_error(error_message))

    def _handle_step_failure(
        self,
        step_index: int,
        reason: str,
        tool: str = "",
        args: dict[str, Any] | None = None,
        *,
        failure: FailureFact | None = None,
    ) -> str:
        return handle_step_failure(self, step_index, reason, tool, args, failure=failure)

    def _purge_stale_context(self) -> None:
        ErrorHandler.purge_stale_context(self.session, self.verbose)

    def fail_task(self) -> None:
        self._task_failed = True

    def cancel_task(self) -> None:
        self.cancellation_token.cancel()
        from agent.planning.task_completion import mark_terminal_cancelled

        mark_terminal_cancelled(self)
        self._save_checkpoint()

    def _summarize_text(self, text: str, context: str = "") -> str:
        return str(self.tool_executor.summarize_text(text, context))

    def _maybe_summarize_and_store(self, tool_name: str, args: ToolArgs, result: CanonicalToolResult) -> None:
        self.tool_executor.maybe_summarize_and_store(tool_name, args, result)

    def _run_reactive(self, objective: str, usage: Dict[str, int], original_count: int) -> str:
        return str(self.reactive_loop.run_reactive(objective, usage, original_count))

    def _run_tool(self, tool_name: str, args: ToolArgs) -> CanonicalToolResult:
        """Return the canonical result; legacy projection belongs at adapters."""

        return cast(CanonicalToolResult, self.tool_executor.run_canonical_tool(tool_name, args))

    def _run_prepared_invocation(self, prepared: Any) -> CanonicalToolResult:
        """Dispatch the owned concrete preparation boundary."""

        return cast(
            CanonicalToolResult,
            self.tool_executor.run_prepared_invocation_canonical(prepared),
        )
