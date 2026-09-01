"""Non-owning typed support methods for the execution gateway."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Dict, List, Optional

from agent.planning.plan_model import Plan
from agent.planning.plan_optimizer import PlanOptimizer
from agent.planning.plan_validator import BlockedStep
from agent.planning.planning_context import (
    PlanningContextError,
    PlanningContextSnapshot,
)
from agent.planning.presentation import (
    PlanningPresentationSnapshot,
    validate_planning_view_binding,
)
from agent.planning.validation_repair import replan_blocked_steps
from agent.planning.validation_repair_plan import _replace_typed_step
from agent.runtime.logging import logger


class ExecutionGatewaySupportMixin:
    orchestrator: Any

    @staticmethod
    def _log_validation(report: Any, phase: str = "validação") -> None:
        for warning in report.warnings:
            logger.info("[GATEWAY][%s] %s", phase, warning)
        for error in report.errors:
            logger.warning("[GATEWAY][%s] %s", phase, error)
        for blocked in report.blocked_steps:
            logger.warning(
                "[GATEWAY][%s] Passo %s bloqueado: %s",
                phase,
                blocked.index + 1,
                blocked.reason,
            )

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
        plan: Plan,
        objective: str,
        blocked: List[BlockedStep],
        failure_reason: str,
        planning_context: PlanningContextSnapshot | None = None,
        planning_view: PlanningPresentationSnapshot | None = None,
        repair_budget: Mapping[str, int] | None = None,
    ) -> Optional[Plan]:
        # Keep recovery orchestration non-owning; the gateway stores only the
        # typed plan returned by the canonical repair owner.
        del repair_budget
        if not blocked:
            return plan
        recovered = self._replan_blocked_steps(
            plan,
            objective,
            blocked,
            planning_context
            if planning_context is not None
            else getattr(self, "_active_planning_context", None),
            planning_view,
        )
        if recovered is None:
            self._abort(failure_reason)
        return recovered

    def _optimize(
        self,
        plan: Plan,
        planning_context: PlanningContextSnapshot | None = None,
        presented_names: frozenset[str] | None = None,
        planning_view: PlanningPresentationSnapshot | None = None,
    ) -> Plan:
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
        return report.optimized_plan

    def _replan_blocked_steps(
        self,
        plan: Plan,
        objective: str,
        blocked_steps: List[BlockedStep],
        planning_context: PlanningContextSnapshot | None = None,
        planning_view: PlanningPresentationSnapshot | None = None,
        repair_budget: Mapping[str, int] | None = None,
    ) -> Optional[Plan]:
        del repair_budget
        recovered = replan_blocked_steps(
            self, plan, objective, blocked_steps, planning_context, planning_view
        )
        return recovered if isinstance(recovered, Plan) else None

    def _replace_blocked_step(
        self,
        plan: Plan,
        objective: str,
        blocked: BlockedStep,
        planning_context: PlanningContextSnapshot | None = None,
        planning_view: PlanningPresentationSnapshot | None = None,
        repair_budget: Mapping[str, int] | None = None,
    ) -> bool:
        del repair_budget
        recovered = _replace_typed_step(
            self,
            plan,
            objective,
            blocked,
            planning_context,
            planning_view,
            {blocked.index},
        )
        if recovered is None:
            return False
        self.orchestrator.agent_state.set_plan(recovered)
        return True

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
        return context.resolve_view(
            planner_kind, getattr(self.orchestrator, "active_skills", ())
        )
