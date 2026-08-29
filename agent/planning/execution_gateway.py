"""Single validated entry point for every plan execution path."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from agent.planning.deferred_condition import is_deferred_condition
from agent.planning.execution_gateway_support import ExecutionGatewaySupportMixin
from agent.planning.execution_validation import validate_and_optimize_plan as _validate_and_optimize_plan
from agent.planning.plan_model import Plan, bind_plan_references, serialize_plan
from agent.planning.planning_context import (
    PlanningContextSnapshot,
)
from agent.planning.planning_view_support import extend_planning_view
from agent.planning.presentation import PlanningPresentationSnapshot
from agent.planning.result_bindings import has_result_bindings
from agent.state import AgentState


@dataclass
class ExecutionResult:
    aborted: bool = False
    final_answer: Optional[str] = None
    validated_plan: Plan = field(default_factory=Plan)


class ExecutionGateway(ExecutionGatewaySupportMixin):
    """Validates, optimizes and executes plans with one shared policy."""

    def __init__(self, orchestrator: Any):
        self.orchestrator = orchestrator

    def execute_validated_plan(
        self,
        plan: Plan | Sequence[Mapping[str, Any]],
        objective: str,
        tool_usage_count: Dict[str, int],
        *,
        continue_after_plan: bool = False,
        planning_context: PlanningContextSnapshot | None = None,
        planning_view: PlanningPresentationSnapshot | None = None,
        allow_conditional_preview: bool = False,
    ) -> ExecutionResult:
        self._active_planning_view = planning_view
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
                "plan": serialize_plan(canonical),
                "continue_after_plan": continue_after_plan,
            },
        )
        if continue_after_plan:
            answer = self.orchestrator.plan_executor.execute(
                objective, tool_usage_count, continue_after_plan=True
            )
        else:
            answer = self.orchestrator.plan_executor.execute(objective, tool_usage_count)
        return ExecutionResult(
            final_answer=answer,
            validated_plan=self.orchestrator.agent_state.plan,
        )

    def validate_and_optimize_plan(
        self,
        plan: Plan | Sequence[Mapping[str, Any]],
        objective: str,
        *,
        planning_context: PlanningContextSnapshot | None = None,
        planning_view: PlanningPresentationSnapshot | None = None,
        allow_conditional_preview: bool = False,
    ) -> Optional[Plan]:
        if planning_view is not None:
            self._active_planning_view = planning_view
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
        plan: Plan | Sequence[Mapping[str, Any]],
    ) -> Plan:
        canonical = AgentState.canonicalize_plan_steps(
            plan,
            preserve_step_ids=True,
        )
        if not any(is_deferred_condition(step) for step in canonical) and not any(
            has_result_bindings(step) for step in canonical
        ):
            return canonical
        return bind_plan_references(canonical)

    def extend_validated_plan(
        self,
        plan: Plan | Sequence[Mapping[str, Any]],
        objective: str,
        *,
        allow_conditional_preview: bool = False,
        planning_view: PlanningPresentationSnapshot | None = None,
    ) -> Optional[Plan]:
        """Validate a continuation before appending it to the canonical plan.

        A boundary extension may bind an argument to an observation in the
        already-executed prefix.  Validate the persisted prefix and proposed
        suffix as one canonical plan so ordinal bindings can be resolved
        against that real history before anything is inserted.
        """

        state = self.orchestrator.agent_state
        prefix = state.plan
        suffix = AgentState.canonicalize_plan_steps(
            plan,
            preserve_step_ids=True,
        )
        combined = Plan((*prefix.steps, *suffix.steps))
        context = getattr(self.orchestrator, "planning_context", None)
        effective_view = extend_planning_view(context, planning_view, prefix)
        self._active_planning_view = effective_view
        validated = self.validate_and_optimize_plan(
            combined,
            objective,
            allow_conditional_preview=allow_conditional_preview,
            planning_view=effective_view,
        )
        if validated is None or len(validated) <= len(prefix):
            return None
        prefix_ids = [step.step_id for step in prefix.steps]
        validated_prefix_ids = [step.step_id for step in validated.steps[: len(prefix)]]
        if prefix_ids != validated_prefix_ids:
            self._abort("prefixo do plano alterado durante a extensao")
            return None
        extension = Plan(validated.steps[len(prefix) :])
        for step in extension:
            state.insert_plan_step(len(state.plan), step)
        self.orchestrator._emit(
            "plan_extended",
            {"steps": len(extension), "plan": serialize_plan(extension)},
        )
        return extension
