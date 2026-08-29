"""Single production composition owner for typed-plan admission."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any

from agent.planning.plan_model import (
    DeferredConditionStep,
    Plan,
    PlanDecodeError,
    ToolPlanStep,
)
from agent.planning.plan_validation_types import ValidationReport
from agent.planning.plan_validator import PlanValidator
from agent.planning.planning_context import PlanningContextSnapshot
from agent.planning.presentation import PlanningPresentationSnapshot


class PlanAdmissionMode(str, Enum):
    """Proven, explicit validator policies used by production callers."""

    INITIAL = "initial"
    BOUND = "bound"
    POST_OPTIMIZATION = "post_optimization"
    REPLAN = "replan"
    VALIDATION_REPAIR = "validation_repair"
    MATERIALIZED_DEFERRED = "materialized_deferred"


@dataclass(frozen=True, slots=True)
class PlanAdmissionPolicy:
    """Typed policy derived from one admission mode and candidate plan."""

    mode: PlanAdmissionMode
    canonical_deferred_references: bool
    scope_to_matching_plan: bool
    allow_conditional_preview: bool


class PlanAdmissionService:
    """Compose and invoke the one production ``PlanValidator`` owner.

    The service only admits a plan or materialized step.  Recovery, repair
    budgets, replanning, and authority/effect ownership stay with their
    existing callers and domain owners.
    """

    _CANONICAL_MODES = {
        PlanAdmissionMode.BOUND,
        PlanAdmissionMode.MATERIALIZED_DEFERRED,
    }
    _AUTO_CANONICAL_MODES = {
        PlanAdmissionMode.POST_OPTIMIZATION,
        PlanAdmissionMode.VALIDATION_REPAIR,
    }
    _PREVIEW_MODES = {
        PlanAdmissionMode.INITIAL,
        PlanAdmissionMode.BOUND,
        PlanAdmissionMode.POST_OPTIMIZATION,
    }

    def __init__(self, orchestrator: Any) -> None:
        self.orchestrator = orchestrator

    def policy_for(
        self,
        mode: PlanAdmissionMode,
        plan: Plan | Sequence[Mapping[str, Any]],
        *,
        allow_conditional_preview: bool = False,
    ) -> PlanAdmissionPolicy:
        if not isinstance(mode, PlanAdmissionMode):
            raise TypeError("PlanAdmissionMode explícito é obrigatório")
        if mode in self._CANONICAL_MODES:
            canonical = True
        elif mode in self._AUTO_CANONICAL_MODES:
            canonical = self.has_canonical_references(plan)
        else:
            canonical = False
        return PlanAdmissionPolicy(
            mode=mode,
            canonical_deferred_references=canonical,
            scope_to_matching_plan=mode is not PlanAdmissionMode.MATERIALIZED_DEFERRED,
            allow_conditional_preview=(
                bool(allow_conditional_preview) and mode in self._PREVIEW_MODES
            ),
        )

    def admit(
        self,
        plan: Plan | Sequence[Mapping[str, Any]],
        objective: str,
        *,
        mode: PlanAdmissionMode,
        planning_context: PlanningContextSnapshot | None = None,
        planning_view: PlanningPresentationSnapshot | None = None,
        allow_conditional_preview: bool = False,
    ) -> ValidationReport:
        """Admit one candidate with the mode's exact validator policy."""

        policy = self.policy_for(
            mode,
            plan,
            allow_conditional_preview=allow_conditional_preview,
        )
        observations, plan_identity = self.observation_scope(plan, policy)
        validator = self._compose_validator(
            policy,
            objective,
            planning_context=planning_context,
            planning_view=planning_view,
            available_observations=observations,
            plan_identity=plan_identity,
        )
        return validator.validate(plan)

    def admit_step(
        self,
        step: ToolPlanStep,
        objective: str,
        *,
        mode: PlanAdmissionMode,
        planning_context: PlanningContextSnapshot | None = None,
        planning_view: PlanningPresentationSnapshot | None = None,
    ) -> str | None:
        """Run the proven single-step materialization gate through this owner."""

        if mode is not PlanAdmissionMode.MATERIALIZED_DEFERRED:
            raise ValueError("admit_step exige o modo MATERIALIZED_DEFERRED")
        plan = Plan((step,))
        policy = self.policy_for(mode, plan)
        observations, plan_identity = self.observation_scope(plan, policy)
        validator = self._compose_validator(
            policy,
            objective,
            planning_context=planning_context,
            planning_view=planning_view,
            available_observations=observations,
            plan_identity=plan_identity,
        )
        return validator._validate_step_schema(step)

    def observation_scope(
        self,
        plan: Plan | Sequence[Mapping[str, Any]],
        policy: PlanAdmissionPolicy,
    ) -> tuple[tuple[Mapping[str, Any], ...], str | None]:
        """Select only the existing causal history allowed by the policy."""

        state = getattr(self.orchestrator, "agent_state", None)
        plan_identity = getattr(state, "plan_identity", None)
        if plan_identity is not None:
            plan_identity = str(plan_identity)
        history = tuple(getattr(state, "tool_history", ()) or ())
        if not policy.scope_to_matching_plan:
            return self._filter_history(history, plan_identity), plan_identity

        current_plan = getattr(state, "plan", Plan())
        current_ids = self._step_ids(current_plan)
        candidate_ids = self._step_ids(plan)
        if plan_identity is None or not current_ids.intersection(candidate_ids):
            return (), None
        return self._filter_history(history, plan_identity), plan_identity

    @staticmethod
    def _filter_history(
        history: tuple[Mapping[str, Any], ...],
        plan_identity: str | None,
    ) -> tuple[Mapping[str, Any], ...]:
        if plan_identity is None:
            return history
        return tuple(
            item
            for item in history
            if item.get("plan_id") in (None, plan_identity)
        )

    @staticmethod
    def has_canonical_references(
        plan: Plan | Sequence[Mapping[str, Any]],
    ) -> bool:
        typed_plan = PlanAdmissionService._typed_plan_or_empty(plan)
        for step in typed_plan.steps:
            if isinstance(step, DeferredConditionStep) and step.observation_ref.is_stable_id:
                return True
            if isinstance(step, ToolPlanStep) and step.bindings is not None:
                if any(binding.from_step.is_stable_id for binding in step.bindings.values()):
                    return True
        return False

    def _compose_validator(
        self,
        policy: PlanAdmissionPolicy,
        objective: str,
        *,
        planning_context: PlanningContextSnapshot | None,
        planning_view: PlanningPresentationSnapshot | None,
        available_observations: Sequence[Mapping[str, Any]],
        plan_identity: str | None,
    ) -> PlanValidator:
        presented_names = (
            planning_view.presented_names
            if planning_view is not None
            else (
                planning_context.eligible_names
                if planning_context is not None
                else None
            )
        )
        return PlanValidator(
            getattr(self.orchestrator, "skills", {}) or {},
            getattr(self.orchestrator, "active_skills", []) or [],
            getattr(self.orchestrator, "allowed_capabilities", None),
            getattr(self.orchestrator, "tool_registry", None),
            planning_context=planning_context,
            presented_names=presented_names,
            planning_view=planning_view,
            objective=objective,
            canonical_deferred_references=policy.canonical_deferred_references,
            available_observations=available_observations,
            plan_identity=plan_identity,
            allow_conditional_preview=policy.allow_conditional_preview,
        )

    @staticmethod
    def _typed_plan_or_empty(
        plan: Plan | Sequence[Mapping[str, Any]],
    ) -> Plan:
        if isinstance(plan, Plan):
            return plan
        try:
            return Plan.from_raw(plan)
        except (PlanDecodeError, TypeError, ValueError):
            return Plan()

    @classmethod
    def _step_ids(
        cls,
        plan: Plan | Sequence[Mapping[str, Any]],
    ) -> set[str]:
        if isinstance(plan, Plan):
            return {step.step_id for step in plan.steps}
        typed = cls._typed_plan_or_empty(plan)
        return {step.step_id for step in typed.steps}


__all__ = [
    "PlanAdmissionMode",
    "PlanAdmissionPolicy",
    "PlanAdmissionService",
]
