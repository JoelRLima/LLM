from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, Dict, cast

from agent.planning.capability_manifest import render_active_harness_capabilities
from agent.planning.hierarchical_executor import HierarchicalExecutor
from agent.planning.hierarchical_planner import HierarchicalPlanner
from agent.reporting.incremental_summarizer import IncrementalSummarizer
from agent.reporting.task_tracker import TaskTracker
from agent.runtime import paths
from agent.runtime.logging import logger


class HierarchicalExecutionService:
    def __init__(self, orchestrator: Any) -> None:
        self.orchestrator = orchestrator

    def run(self, objective: str, on_chunk: Callable[[str], None] | None = None) -> str | None:
        planning_view = getattr(self.orchestrator, "get_planning_view", lambda _kind: None)("hierarchical")
        planner = HierarchicalPlanner(
            ask_model=self._ask_model,
            valid_tools=list(planning_view.presented_names) if planning_view is not None else list(self.orchestrator.skills),
            planning_view=planning_view,
            capability_manifest=render_active_harness_capabilities(
                self.orchestrator, planner_kind="hierarchical"
            ),
        )
        try:
            macro_plan = planner.build_plan(objective)
        except Exception as exc:
            logger.warning("Falha ao gerar MacroPlan, usando fallback linear: %s", exc)
            self.orchestrator._emit("hierarchical_fallback", {"reason": str(exc)})
            return None
        if not macro_plan or not macro_plan.steps:
            self.orchestrator._emit("hierarchical_fallback", {"reason": "macro_plan vazio ou não gerado"})
            return None
        workspace_paths = self.orchestrator.workspace_paths
        tracker = TaskTracker(
            json_path=str(
                workspace_paths.task_tracker_json
                if workspace_paths is not None
                else paths.TASK_TRACKER_JSON
            ),
            markdown_path=str(
                workspace_paths.task_tracker_markdown
                if workspace_paths is not None
                else paths.TASK_TRACKER_MD
            ),
        )
        tracker.start(objective, macro_plan.steps, self._metadata(objective))
        executor = HierarchicalExecutor(
            plan_builder=self.orchestrator.plan_builder,
            plan_executor=self.orchestrator.plan_executor,
            final_responder=self.orchestrator.final_responder,
            context_manager=self.orchestrator.context_manager,
            session=self.orchestrator.session,
            tracker=tracker,
            summarizer=IncrementalSummarizer(summarize_fn=self.orchestrator._summarize_text),
            execution_gateway=self.orchestrator.execution_gateway,
        )
        self.orchestrator._emit("hierarchical_started", {"steps": len(macro_plan.steps)})
        answer = executor.execute(macro_plan, self.orchestrator.agent_state, {}, on_chunk=on_chunk)
        self.orchestrator._emit("hierarchical_completed", {"steps": len(macro_plan.steps)})
        return str(answer) if answer is not None else None

    def _ask_model(self, prompt: str, step_type: str) -> Dict[str, Any]:
        return cast(Dict[str, Any], self.orchestrator.context_manager.ask_model(
            prompt,
            step_type=step_type,
            base_prompt=getattr(self.orchestrator, "_cached_base_prompt", None) or "",
            log_metric_callback=self.orchestrator._log_metric,
        ))

    def _metadata(self, objective: str) -> Dict[str, Any]:
        manager = self.orchestrator.context_manager
        return {
            "model": getattr(manager, "model_name", None) or getattr(manager, "model", None) or "desconhecido",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "prompt": objective,
        }
