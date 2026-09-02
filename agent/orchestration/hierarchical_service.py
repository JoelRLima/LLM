from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, Dict

from agent.llm.admitted_decisions import MacroPlanDecision, ask_typed_model_decision
from agent.llm.decision_contract import ModelRequestContract
from agent.orchestration.route_result import RouteResult
from agent.planning.capability_manifest import render_active_harness_capabilities
from agent.planning.hierarchical_executor import HierarchicalExecutor
from agent.planning.hierarchical_planner import HierarchicalPlanner
from agent.reporting.incremental_summarizer import IncrementalSummarizer
from agent.reporting.task_tracker import TaskTracker
from agent.runtime.budget import BudgetExhausted
from agent.runtime.logging import logger

_ROUTE = "hierarchical"
_PLANNER_ERROR = "HIERARCHICAL_PLANNER_ERROR"
_MACROPLAN_EMPTY = "HIERARCHICAL_MACROPLAN_EMPTY"
_PRECONDITION_ERROR = "HIERARCHICAL_PRECONDITION_UNAVAILABLE"
_AUTHORITY_DENIED = "HIERARCHICAL_AUTHORITY_DENIED"


class HierarchicalExecutionService:
    def __init__(self, orchestrator: Any) -> None:
        self.orchestrator = orchestrator

    def run(self, objective: str, on_chunk: Callable[[str], None] | None = None) -> RouteResult:
        self._require_workspace_paths()
        planning = self._planning_context()
        if isinstance(planning, RouteResult):
            return planning
        planning_view, valid_tools, capability_manifest = planning
        macro_plan = self._build_macro_plan(
            objective, planning_view, valid_tools, capability_manifest
        )
        if isinstance(macro_plan, RouteResult):
            return macro_plan
        if not macro_plan or not getattr(macro_plan, "steps", None):
            return self._fallback(
                _MACROPLAN_EMPTY,
                "Hierarchical planner returned no usable macroplan.",
            )
        answer = self._execute_macro_plan(macro_plan, objective, on_chunk)
        if isinstance(answer, RouteResult):
            return answer
        self.orchestrator._emit("hierarchical_completed", {"steps": len(macro_plan.steps)})
        return RouteResult.handled(
            route=_ROUTE,
            answer=answer if isinstance(answer, str) else None,
        )

    def _planning_context(self) -> tuple[Any, list[str], str] | RouteResult:
        try:
            planning_view = getattr(self.orchestrator, "get_planning_view", lambda _kind: None)(_ROUTE)
            valid_tools = (
                list(planning_view.presented_names)
                if planning_view is not None
                else list(self.orchestrator.skills)
            )
            capability_manifest = render_active_harness_capabilities(
                self.orchestrator, planner_kind=_ROUTE
            )
        except BudgetExhausted:
            raise
        except PermissionError as exc:
            return self._fallback(_AUTHORITY_DENIED, self._safe_exception_detail(exc))
        except Exception as exc:
            detail = self._safe_exception_detail(exc)
            logger.warning("Precondicao hierarquica indisponivel: %s", detail)
            return self._fallback(_PRECONDITION_ERROR, detail)

        return planning_view, valid_tools, capability_manifest

    def _build_macro_plan(
        self,
        objective: str,
        planning_view: Any,
        valid_tools: list[str],
        capability_manifest: str,
    ) -> Any | RouteResult:
        try:
            planner = HierarchicalPlanner(
                ask_model=self._ask_model,
                valid_tools=valid_tools,
                planning_view=planning_view,
                capability_manifest=capability_manifest,
            )
            macro_plan = planner.build_plan(objective)
        except BudgetExhausted:
            raise
        except PermissionError as exc:
            return self._fallback(_AUTHORITY_DENIED, self._safe_exception_detail(exc))
        except Exception as exc:
            detail = self._safe_exception_detail(exc)
            logger.warning("Falha ao gerar MacroPlan, usando fallback linear: %s", detail)
            return self._fallback(_PLANNER_ERROR, detail)
        return macro_plan

    def _execute_macro_plan(
        self, macro_plan: Any, objective: str, on_chunk: Callable[[str], None] | None
    ) -> str | RouteResult | None:
        workspace_paths = self._require_workspace_paths()
        tracker = TaskTracker(
            json_path=str(workspace_paths.task_tracker_json),
            markdown_path=str(workspace_paths.task_tracker_markdown),
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
        try:
            answer = executor.execute(macro_plan, self.orchestrator.agent_state, {}, on_chunk=on_chunk)
        except BudgetExhausted:
            finish_failure = getattr(tracker, "finish_failure", None)
            if callable(finish_failure):
                finish_failure("TASK_BUDGET_EXHAUSTED")
            raise
        except Exception as exc:
            detail = self._safe_exception_detail(exc)
            logger.exception("Falha na execucao hierarquica: %s", detail)
            finish_failure = getattr(tracker, "finish_failure", None)
            if callable(finish_failure):
                finish_failure("HIERARCHICAL_EXECUTION_FAILED")
            fail_task = getattr(self.orchestrator, "fail_task", None)
            if callable(fail_task):
                fail_task()
            else:
                self.orchestrator._task_failed = True
            return RouteResult.handled(
                route=_ROUTE,
                answer="A execucao hierarquica falhou.",
                reason_code="HIERARCHICAL_EXECUTION_FAILED",
                detail=detail,
            )
        return answer if isinstance(answer, str) else None

    def _require_workspace_paths(self) -> Any:
        workspace_paths = getattr(self.orchestrator, "workspace_paths", None)
        if workspace_paths is None:
            raise RuntimeError(
                "HierarchicalExecutionService requires explicit workspace path authority"
            )
        for attribute in ("task_tracker_json", "task_tracker_markdown"):
            if getattr(workspace_paths, attribute, None) is None:
                raise RuntimeError(
                    "HierarchicalExecutionService requires complete WorkspacePaths"
                )
        return workspace_paths

    def _fallback(self, reason_code: str, detail: str) -> RouteResult:
        result = RouteResult.fallback(
            route=_ROUTE,
            reason_code=reason_code,
            detail=detail,
        )
        self.orchestrator._emit(
            "hierarchical_fallback",
            {
                "reason_code": result.reason_code,
                "reason": detail,
                "detail": detail,
            },
        )
        return result

    @staticmethod
    def _safe_exception_detail(exc: Exception) -> str:
        exception_name = type(exc).__name__ or "Exception"
        try:
            message = " ".join(str(exc).split())
        except Exception:
            message = ""
        if not message:
            return exception_name
        return f"{exception_name}: {message[:240]}"

    def _ask_model(
        self, prompt: str, request_contract: ModelRequestContract
    ) -> MacroPlanDecision | None:
        if request_contract is not ModelRequestContract.MACRO_PLAN:
            return None
        decision = ask_typed_model_decision(
            self.orchestrator.context_manager,
            prompt,
            request_contract=request_contract,
            base_prompt=getattr(self.orchestrator, "_cached_base_prompt", None) or "",
            log_metric_callback=self.orchestrator._log_metric,
        )
        return decision if isinstance(decision, MacroPlanDecision) else None

    def _metadata(self, objective: str) -> Dict[str, Any]:
        manager = self.orchestrator.context_manager
        return {
            "model": getattr(manager, "model_name", None) or getattr(manager, "model", None) or "desconhecido",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "prompt": objective,
        }
