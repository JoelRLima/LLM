"""Single validated entry point for every plan execution path."""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, cast

from agent.planning.deferred_condition import bind_deferred_observation_references, is_deferred_condition
from agent.planning.execution_validation import validate_and_optimize_plan as _validate_and_optimize_plan
from agent.planning.plan_optimizer import PlanOptimizer
from agent.planning.plan_validator import BlockedStep
from agent.planning.planning_context import (
    PlanningContextError,
    PlanningContextSnapshot,
)
from agent.planning.presentation import PlanningPresentationSnapshot, validate_planning_view_binding
from agent.planning.result_bindings import bind_result_references, has_result_bindings
from agent.planning.validation_repair import replace_blocked_step, replan_blocked_steps
from agent.runtime.logging import logger
from agent.state import AgentState


@dataclass
class ExecutionResult:
    aborted: bool = False
    final_answer: Optional[str] = None
    validated_plan: List[Dict[str, Any]] = field(default_factory=list)


class ExecutionGateway:
    """Validates, optimizes and executes plans with one shared policy."""

    def __init__(self, orchestrator: Any):
        self.orchestrator = orchestrator

    def execute_validated_plan(
        self,
        plan: List[Dict[str, Any]],
        objective: str,
        tool_usage_count: Dict[str, int],
        *,
        continue_after_plan: bool = False,
        planning_context: PlanningContextSnapshot | None = None,
        planning_view: PlanningPresentationSnapshot | None = None,
        allow_conditional_preview: bool = False,
    ) -> ExecutionResult:
        validated = self.validate_and_optimize_plan(
            plan,
            objective,
            planning_context=planning_context,
            planning_view=planning_view,
            allow_conditional_preview=allow_conditional_preview,
        )
        if validated is None:
            return ExecutionResult(
                aborted=True,
                final_answer="Execução bloqueada: não foi possível validar um plano seguro; a execução foi interrompida.",
            )
        self.orchestrator.agent_state.set_plan(validated)
        canonical = self.orchestrator.agent_state.plan
        self.orchestrator._emit(
            "plan_created",
            {
                "steps": len(canonical),
                "plan": canonical,
                "continue_after_plan": continue_after_plan,
            },
        )
        if continue_after_plan:
            answer = self.orchestrator.plan_executor.execute(
                objective, tool_usage_count, continue_after_plan=True
            )
        else:
            answer = self.orchestrator.plan_executor.execute(objective, tool_usage_count)
        return ExecutionResult(final_answer=answer, validated_plan=canonical)

    def validate_and_optimize_plan(
        self,
        plan: List[Dict[str, Any]],
        objective: str,
        *,
        planning_context: PlanningContextSnapshot | None = None,
        planning_view: PlanningPresentationSnapshot | None = None,
        allow_conditional_preview: bool = False,
    ) -> Optional[List[Dict[str, Any]]]:
        return _validate_and_optimize_plan(
            self,
            plan,
            objective,
            planning_context=planning_context,
            planning_view=planning_view,
            allow_conditional_preview=allow_conditional_preview,
        )

    @staticmethod
    def _bind_deferred_references(
        plan: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        if not any(is_deferred_condition(step) for step in plan) and not any(
            has_result_bindings(step) for step in plan
        ):
            return plan
        canonical = AgentState.canonicalize_plan_steps(
            plan,
            preserve_step_ids=True,
        )
        bound: List[Dict[str, Any]] = bind_result_references(canonical, AgentState._new_step_id)
        return bind_deferred_observation_references(bound)

    def extend_validated_plan(
        self,
        plan: List[Dict[str, Any]],
        objective: str,
        *,
        allow_conditional_preview: bool = False,
    ) -> Optional[List[Dict[str, Any]]]:
        """Validate a continuation before appending it to the canonical plan.

        A boundary extension may bind an argument to an observation in the
        already-executed prefix.  Validate the persisted prefix and proposed
        suffix as one canonical plan so ordinal bindings can be resolved
        against that real history before anything is inserted.
        """

        state = self.orchestrator.agent_state
        prefix = [dict(step) for step in state.plan]
        combined = [*prefix, *[dict(step) for step in plan]]
        validated = self.validate_and_optimize_plan(
            combined,
            objective,
            allow_conditional_preview=allow_conditional_preview,
        )
        if validated is None or len(validated) <= len(prefix):
            return None
        prefix_ids = [str(step.get("_step_id", "")) for step in prefix]
        validated_prefix_ids = [str(step.get("_step_id", "")) for step in validated[: len(prefix)]]
        if prefix_ids != validated_prefix_ids:
            self._abort("prefixo do plano alterado durante a extensao")
            return None
        extension = validated[len(prefix):]
        for step in extension:
            state.insert_plan_step(len(state.plan), step)
        self.orchestrator._emit(
            "plan_extended", {"steps": len(extension), "plan": extension}
        )
        return extension

    @staticmethod
    def _log_validation(report: Any, phase: str = "validação") -> None:
        for warning in report.warnings:
            logger.info("[GATEWAY][%s] %s", phase, warning)
        for error in report.errors:
            logger.warning("[GATEWAY][%s] %s", phase, error)
        for blocked in report.blocked_steps:
            logger.warning("[GATEWAY][%s] Passo %s bloqueado: %s", phase, blocked.index + 1, blocked.reason)

    def _abort(self, reason: str, errors: Any = None) -> None:
        event: Dict[str, Any] = {"reason": reason}
        if errors:
            event["errors"] = errors
        self.orchestrator._emit("hard_block", event)
        project_result = getattr(
            self.orchestrator.agent_state, "project_last_result", None
        )
        if callable(project_result):
            project_result(
                "planner",
                {},
                {
                    "ok": False,
                    "done": True,
                    "status": "blocked",
                    "executed": False,
                    "error": "plan_blocked",
                    "message": "Plano bloqueado pela validação canônica.",
                },
            )
        self.orchestrator.fail_task()

    def _recover(
        self,
        plan: List[Dict[str, Any]],
        objective: str,
        blocked: List[BlockedStep],
        failure_reason: str,
        planning_context: PlanningContextSnapshot | None = None,
        planning_view: PlanningPresentationSnapshot | None = None,
        repair_budget: Dict[str, int] | None = None,
    ) -> Optional[List[Dict[str, Any]]]:
        if not blocked:
            return plan
        if repair_budget is None:
            repair_budget = {"remaining": 1}
        recovered = self._replan_blocked_steps(
            plan,
            objective,
            blocked,
            planning_context if planning_context is not None else getattr(self, "_active_planning_context", None),
            planning_view,
            repair_budget,
        )
        if recovered is None:
            self._abort(failure_reason)
        return recovered

    def _optimize(
        self,
        plan: List[Dict[str, Any]],
        planning_context: PlanningContextSnapshot | None = None,
        presented_names: frozenset[str] | None = None,
        planning_view: PlanningPresentationSnapshot | None = None,
    ) -> List[Dict[str, Any]]:
        from agent.planning.tool_metadata import build_metadata_dict
        context = planning_context or getattr(self, "_active_planning_context", None)
        if context is None:
            registry = getattr(self.orchestrator, "tool_registry", None)
            report = PlanOptimizer(build_metadata_dict(registry)).optimize(plan)
        else:
            report = PlanOptimizer(
                planning_context=context,
                presented_names=presented_names,
                planning_view=planning_view,
            ).optimize(plan)
        if report.changed:
            logger.info(
                "[GATEWAY][OPTIMIZER] custo %s -> %s; %s transformações; %s duplicatas removidas",
                report.cost_before,
                report.cost_after,
                len(report.transformations),
                report.removed_duplicates,
            )
            if getattr(self.orchestrator, "verbose", False):
                for transformation in report.transformations:
                    print(f"[DEBUG][GATEWAY][OPTIMIZER] {transformation}")
        return cast(List[Dict[str, Any]], report.optimized_steps)

    def _replan_blocked_steps(
        self,
        plan: List[Dict[str, Any]],
        objective: str,
        blocked_steps: List[BlockedStep],
        planning_context: PlanningContextSnapshot | None = None,
        planning_view: PlanningPresentationSnapshot | None = None,
        repair_budget: Dict[str, int] | None = None,
    ) -> Optional[List[Dict[str, Any]]]:
        return replan_blocked_steps(
            self, plan, objective, blocked_steps, planning_context, planning_view, repair_budget
        )

    def _replace_blocked_step(
        self,
        plan: List[Dict[str, Any]],
        objective: str,
        blocked: BlockedStep,
        planning_context: PlanningContextSnapshot | None = None,
        planning_view: PlanningPresentationSnapshot | None = None,
        repair_budget: Dict[str, int] | None = None,
    ) -> bool:
        return replace_blocked_step(
            self, plan, objective, blocked, planning_context, planning_view, repair_budget
        )

    def _planning_view(
        self,
        context: PlanningContextSnapshot | None,
        planner_kind: str,
        planning_view: PlanningPresentationSnapshot | None = None,
        explicit_context: bool = False,
    ) -> PlanningPresentationSnapshot | None:
        if context is None:
            if planning_view is not None:
                raise PlanningContextError("planning view sem contexto canônico")
            return None
        if planning_view is not None:
            validate_planning_view_binding(context, planning_view, planner_kind)
            if planning_view.planner_kind != planner_kind:
                raise PlanningContextError("planning view incompatível com o planner")
            return planning_view
        if explicit_context:
            raise PlanningContextError("contexto explícito exige view correlacionada")
        stored_context = getattr(self.orchestrator, "planning_context", None)
        if explicit_context and stored_context is not None and stored_context != context:
            raise PlanningContextError("contexto explícito exige view correlacionada")
        if explicit_context and stored_context is not None and stored_context is not context:
            if (
                stored_context.snapshot_id != context.snapshot_id
                or stored_context.runtime_identity != context.runtime_identity
            ):
                raise PlanningContextError(
                    "contexto explícito exige view correlacionada quando diverge do orchestrator"
                )
        return context.resolve_view(planner_kind, getattr(self.orchestrator, "active_skills", ()))

    @staticmethod
    def _validate_view_binding(
        context: PlanningContextSnapshot,
        planning_view: PlanningPresentationSnapshot,
    ) -> None:
        validate_planning_view_binding(context, planning_view)
