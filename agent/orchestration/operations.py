from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, cast

from agent.contracts import AgentEvent, EventData, ToolArgs, ToolResult
from agent.error_handler import ErrorHandler
from agent.planning.capability_manifest import render_active_harness_capabilities
from agent.planning.presentation import PlanningPresentationSnapshot
from agent.reporting.operational_outcome import project_operational_outcome
from agent.reporting.task_report import TaskReportBuilder
from agent.runtime.logging import logger


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
    _task_failed: bool
    _cancelled: bool
    cancellation_token: Any
    context_manager: Any
    workspace: Any
    auto_coder: Any
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
        planning_view = cast(
            PlanningPresentationSnapshot | None,
            getattr(self, "get_planning_view", lambda _kind: None)(
                planner_kind or ("linear" if not compact else "reactive")
            ),
        )
        if planning_view is not None:
            context_limit = int(
                getattr(getattr(self.session, "hardware_profile", None), "context_limit", 8_192)
            )
            rendered = cast(str, planning_view.render(compact=compact, context_limit=context_limit))
            if not compact:
                return rendered
            return rendered + "\n" + render_active_harness_capabilities(
                self, planner_kind=planner_kind or "reactive"
            )
        descriptions = []
        for skill in self.skills.values():
            if self.active_skills and skill.name not in self.active_skills:
                continue
            if compact:
                descriptions.append(f"- {skill.name}: {skill.description}")
            else:
                schema = json.dumps(skill.get_schema(), indent=2, ensure_ascii=False)
                descriptions.append(f"- {skill.name}: {skill.description}\nArgs: {schema}")
        return "\n".join(descriptions)

    def remember(self, key: str, value: Any, section: str = "key_findings") -> None:
        self.agent_state.memory.remember(key, value, section)

    def forget(self, key: str) -> None:
        self.agent_state.memory.forget(key)

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

    def _save_checkpoint(self) -> None:
        self.checkpoint_manager.save(self.agent_state)

    def _load_checkpoint(self) -> Optional[Dict[str, Any]]:
        return cast(Optional[Dict[str, Any]], self.checkpoint_manager.load())

    def _delete_checkpoint(self) -> None:
        self.checkpoint_manager.delete()

    def _emit(self, event_type: str, data: Optional[EventData] = None) -> None:
        event: AgentEvent = {"type": event_type, "step": self.agent_state.plan_step, "data": data or {}}
        self.agent_state.events.append(event)
        if self.verbose:
            print(f"[{event_type}] {data}")
        if event_type in {"step_completed", "step_failed", "step_skipped"}:
            self._save_checkpoint()

    def _log_metric(self, entry: Dict[str, Any]) -> None:
        enriched = dict(entry)
        if getattr(self, "_run_id", None) is not None:
            enriched.setdefault("run_id", self._run_id)
        self.metrics_recorder.log_metric(enriched)

    def _record_canonical_run_metric(self, success: bool) -> None:
        """Project the already-derived application outcome into JSONL once."""
        if getattr(self, "_run_metric_recorded", False):
            return
        run_id = getattr(self, "_run_id", None)
        started = getattr(self, "_task_start_time", 0.0)
        if not run_id or not started:
            return
        import time
        from datetime import datetime, timezone

        self._log_metric({
            "type": "run",
            "metric_type": "run",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "duration_ms": max(0, int((time.monotonic() - started) * 1000)),
            "success": bool(success),
        })
        self._run_metric_recorded = True

    def _count_metrics_lines(self) -> int:
        return int(self.metrics_recorder.count_lines())

    def _get_metrics_for_task(self) -> List[Dict[str, Any]]:
        return cast(List[Dict[str, Any]], self.metrics_recorder.get_entries_since(self._metrics_start_line))

    def _generate_task_report(
        self,
        final_answer: str,
        *,
        status: str | None = None,
        error: str | None = None,
        receipt: Dict[str, Any] | None = None,
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
                self._get_metrics_for_task(),
                final_answer,
                canonical_outcome={"status": status, "error": error},
                receipt=receipt,
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
        from agent.planning.task_completion import review_task_completion

        review = review_task_completion(self)
        outcome = project_operational_outcome(
            self.agent_state,
            task_failed=bool(getattr(self, "_task_failed", False)),
            cancelled=bool(getattr(self, "_cancelled", False)),
        )
        return (
            review.accepted
            and getattr(self.agent_state, "terminal_disposition", None)
            in {"complete", "succeeded"}
            and outcome.terminal_status == "succeeded"
            and not outcome.pending_effects
        )

    @staticmethod
    def _sanitize_error(error_message: str) -> str:
        return str(ErrorHandler.sanitize_error(error_message))

    def _handle_step_failure(
        self, step_index: int, reason: str, tool: str = "", args: dict[str, Any] | None = None
    ) -> str:
        return str(ErrorHandler.handle_step_failure(
            step_index, reason, tool, args, emit_callback=self._emit, verbose=self.verbose
        ))

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

    def _maybe_summarize_and_store(self, tool_name: str, args: ToolArgs, result: ToolResult) -> None:
        self.tool_executor.maybe_summarize_and_store(tool_name, args, result)

    def _test_and_correct(self, file_path: str, objective: str) -> bool:
        return bool(self.auto_coder.test_and_correct(file_path, objective))

    def _generate_content(self, tool: str, args: dict[str, Any], objective: str) -> Optional[str]:
        generated = self.auto_coder.generate_content(tool, args, objective)
        return str(generated) if generated is not None else None

    def _run_reactive(self, objective: str, usage: Dict[str, int], original_count: int) -> str:
        return str(self.reactive_loop.run_reactive(objective, usage, original_count))

    def _run_tool(self, tool_name: str, args: ToolArgs) -> ToolResult:
        return cast(ToolResult, self.tool_executor.run_tool(tool_name, args))

    def _run_prepared_invocation(self, prepared: Any) -> ToolResult:
        """Dispatch the owned concrete preparation boundary."""

        return cast(ToolResult, self.tool_executor.run_prepared_invocation(prepared))
