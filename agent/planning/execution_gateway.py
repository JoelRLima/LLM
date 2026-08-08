"""Single validated entry point for every plan execution path."""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, cast

from agent.planning.plan_optimizer import PlanOptimizer
from agent.planning.plan_validator import BlockedStep, PlanValidator
from agent.planning.planning_context import (
    PlanningContextError,
    PlanningContextSnapshot,
)
from agent.planning.presentation import PlanningPresentationSnapshot, validate_planning_view_binding
from agent.planning.replan import ReplanContext, replan
from agent.runtime.logging import logger


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
        planning_context: PlanningContextSnapshot | None = None,
        planning_view: PlanningPresentationSnapshot | None = None,
    ) -> ExecutionResult:
        validated = self.validate_and_optimize_plan(
            plan,
            objective,
            planning_context=planning_context,
            planning_view=planning_view,
        )
        if validated is None:
            return ExecutionResult(
                aborted=True,
                final_answer="Não foi possível validar um plano seguro; a execução foi interrompida.",
            )
        self.orchestrator.agent_state.set_plan(validated)
        canonical = self.orchestrator.agent_state.plan
        answer = self.orchestrator.plan_executor.execute(objective, tool_usage_count)
        return ExecutionResult(final_answer=answer, validated_plan=canonical)

    def validate_and_optimize_plan(
        self,
        plan: List[Dict[str, Any]],
        objective: str,
        *,
        planning_context: PlanningContextSnapshot | None = None,
        planning_view: PlanningPresentationSnapshot | None = None,
    ) -> Optional[List[Dict[str, Any]]]:
        explicit_context = planning_context is not None
        context = (
            planning_context
            if planning_context is not None
            else getattr(self.orchestrator, "planning_context", None)
        )
        self._active_planning_context = context
        presentation = self._planning_view(context, "linear", planning_view, explicit_context)
        validator = PlanValidator(
            self.orchestrator.skills,
            self.orchestrator.active_skills,
            getattr(self.orchestrator, "allowed_capabilities", None),
            getattr(self.orchestrator, "tool_registry", None),
            planning_context=context,
            presented_names=presentation.presented_names if presentation is not None else None,
            planning_view=presentation,
        )
        report = validator.validate(plan)
        self._log_validation(report)
        if not report.is_valid:
            self._abort("plano inválido", report.errors)
            return None
        recovered = self._recover(
            plan,
            objective,
            report.blocked_steps,
            "replanejamento inicial falhou",
            context,
            presentation,
        )
        if recovered is None:
            return None
        optimized = self._optimize(
            recovered,
            context,
            presentation.presented_names if presentation else None,
            presentation,
        )
        post_report = validator.validate(optimized)
        self._log_validation(post_report, "pós-otimização")
        if not post_report.is_valid:
            self._abort("plano inválido pós-otimização", post_report.errors)
            return None
        return self._recover(
            optimized,
            objective,
            post_report.blocked_steps,
            "replanejamento pós-otimização falhou",
            context,
            presentation,
        )

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
        self.orchestrator.fail_task()

    def _recover(
        self,
        plan: List[Dict[str, Any]],
        objective: str,
        blocked: List[BlockedStep],
        failure_reason: str,
        planning_context: PlanningContextSnapshot | None = None,
        planning_view: PlanningPresentationSnapshot | None = None,
    ) -> Optional[List[Dict[str, Any]]]:
        if not blocked:
            return plan
        recovered = self._replan_blocked_steps(
            plan,
            objective,
            blocked,
            planning_context if planning_context is not None else getattr(self, "_active_planning_context", None),
            planning_view,
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
    ) -> Optional[List[Dict[str, Any]]]:
        updated = list(plan)
        for blocked in sorted(blocked_steps, key=lambda item: item.index, reverse=True):
            if not self._replace_blocked_step(updated, objective, blocked, planning_context, planning_view):
                return None
        return updated or None

    def _replace_blocked_step(
        self,
        plan: List[Dict[str, Any]],
        objective: str,
        blocked: BlockedStep,
        planning_context: PlanningContextSnapshot | None = None,
        planning_view: PlanningPresentationSnapshot | None = None,
    ) -> bool:
        index = blocked.index
        if index >= len(plan):
            return False
        step = plan[index] if isinstance(plan[index], dict) else {"tool": "", "args": {}}
        context = ReplanContext(
            task=objective,
            current_step=step,
            tool_history=self.orchestrator.agent_state.tool_history,
            last_exception=blocked.reason,
        )
        action = replan(
            context, blocked.reason, self.orchestrator,
            planning_context=planning_context,
            planning_view=planning_view,
        )
        self.orchestrator._emit("replan", {
            "original_step": index,
            "error": blocked.reason,
            "strategy": action.source if action else "none",
            "replacement_steps": len(action.steps) if action else 0,
        })
        if action and action.steps:
            plan[index : index + 1] = action.steps
            logger.info("Passo %s substituído por %s passo(s).", index + 1, len(action.steps))
            return True
        else:
            logger.warning("Passo %s permanece bloqueado: nenhuma substituição válida.", index + 1)
            return False

    def _planning_view(
        self,
        context: PlanningContextSnapshot | None,
        planner_kind: str,
        planning_view: PlanningPresentationSnapshot | None = None,
        explicit_context: bool = False,
    ) -> PlanningPresentationSnapshot | None:
        if context is None:
            if planning_view is not None:
                raise PlanningContextError("planning view sem contexto canÃ´nico")
            return None
        if planning_view is not None:
            validate_planning_view_binding(context, planning_view, planner_kind)
            if planning_view.planner_kind != planner_kind:
                raise PlanningContextError("planning view incompatÃ­vel com o planner")
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
                    "contexto explÃ­cito exige view correlacionada quando diverge do orchestrator"
                )
        active = frozenset(getattr(self.orchestrator, "active_skills", ()) or ())
        visible = active & context.eligible_names if active else context.eligible_names
        return context.present(planner_kind, visible)

    @staticmethod
    def _validate_view_binding(
        context: PlanningContextSnapshot,
        planning_view: PlanningPresentationSnapshot,
    ) -> None:
        validate_planning_view_binding(context, planning_view)
