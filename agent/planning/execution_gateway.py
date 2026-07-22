"""Single validated entry point for every plan execution path."""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, cast

from agent.planning.plan_optimizer import PlanOptimizer
from agent.planning.plan_validator import BlockedStep, PlanValidator
from agent.planning.replan import ReplanContext, replan
from agent.planning.tool_metadata import TOOL_METADATA
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
    ) -> ExecutionResult:
        validated = self.validate_and_optimize_plan(plan, objective)
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
        self, plan: List[Dict[str, Any]], objective: str
    ) -> Optional[List[Dict[str, Any]]]:
        validator = PlanValidator(self.orchestrator.skills, self.orchestrator.active_skills)
        report = validator.validate(plan)
        self._log_validation(report)
        if not report.is_valid:
            self._abort("plano inválido", report.errors)
            return None
        recovered = self._recover(plan, objective, report.blocked_steps, "replanejamento inicial falhou")
        if recovered is None:
            return None
        optimized = self._optimize(recovered)
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
    ) -> Optional[List[Dict[str, Any]]]:
        if not blocked:
            return plan
        recovered = self._replan_blocked_steps(plan, objective, blocked)
        if recovered is None:
            self._abort(failure_reason)
        return recovered

    def _optimize(self, plan: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        report = PlanOptimizer(TOOL_METADATA).optimize(plan)
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
        self, plan: List[Dict[str, Any]], objective: str, blocked_steps: List[BlockedStep]
    ) -> Optional[List[Dict[str, Any]]]:
        updated = list(plan)
        for blocked in sorted(blocked_steps, key=lambda item: item.index, reverse=True):
            self._replace_blocked_step(updated, objective, blocked)
        return updated or None

    def _replace_blocked_step(
        self, plan: List[Dict[str, Any]], objective: str, blocked: BlockedStep
    ) -> None:
        index = blocked.index
        if index >= len(plan):
            return
        step = plan[index] if isinstance(plan[index], dict) else {"tool": "", "args": {}}
        context = ReplanContext(
            task=objective,
            current_step=step,
            tool_history=self.orchestrator.agent_state.tool_history,
            last_exception=blocked.reason,
        )
        action = replan(context, blocked.reason, self.orchestrator)
        self.orchestrator._emit("replan", {
            "original_step": index,
            "error": blocked.reason,
            "strategy": action.source if action else "none",
            "replacement_steps": len(action.steps) if action else 0,
        })
        if action and action.steps:
            plan[index : index + 1] = action.steps
            logger.info("Passo %s substituído por %s passo(s).", index + 1, len(action.steps))
        else:
            logger.warning("Passo %s bloqueado foi removido do plano.", index + 1)
            del plan[index]
