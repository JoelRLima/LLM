"""Read-only plan schema and policy validator."""

from collections.abc import Mapping, Sequence
from typing import Any, Dict, List, Optional

from agent.planning.deferred_validation import validate_deferred_items
from agent.planning.plan_identity_validation import validate_plan_identities
from agent.planning.plan_model import Plan, PlanDecodeError
from agent.planning.plan_policy_checks import (
    check_analysis_notes,
    check_consecutive_writes,
    check_inverted_dependencies,
    check_patch_without_read,
)
from agent.planning.plan_validation_types import BlockedStep, ValidationReport
from agent.planning.plan_validator_schema import PlanValidatorSchemaMixin
from agent.planning.planning_context import (
    PlanningContextError,
    PlanningContextSnapshot,
)
from agent.planning.presentation import PlanningPresentationSnapshot
from agent.planning.result_bindings import validate_result_bindings


class PlanValidator(PlanValidatorSchemaMixin):
    """Validate plans without mutating the received representation."""

    def __init__(
        self,
        skills: Dict[str, Any],
        active_skills: Optional[List[str]] = None,
        allowed_capabilities: Optional[frozenset[str]] = None,
        tool_registry: Any = None,
        *,
        planning_context: PlanningContextSnapshot | None = None,
        presented_names: frozenset[str] | None = None,
        planning_view: PlanningPresentationSnapshot | None = None,
        objective: str = "",
        canonical_deferred_references: bool = False,
        available_observations: Sequence[Mapping[str, Any]] | None = None,
        plan_identity: str | None = None,
        allow_conditional_preview: bool = False,
    ) -> None:
        self.skills = skills
        self.active_skills = active_skills or []
        self.allowed_capabilities = allowed_capabilities
        self.tool_registry = tool_registry
        self.planning_context = planning_context
        self.planning_view = planning_view
        self.objective = objective
        self.canonical_deferred_references = canonical_deferred_references
        self.plan_identity = plan_identity
        self.allow_conditional_preview = allow_conditional_preview
        observations = tuple(available_observations or ())
        if plan_identity is not None:
            observations = tuple(
                item
                for item in observations
                if not isinstance(item, Mapping)
                or item.get("plan_id") in (None, plan_identity)
            )
        self.available_observations = observations
        if planning_context is not None and planning_view is not None:
            if planning_view.planning_context_id != planning_context.snapshot_id:
                raise PlanningContextError("planning context e view divergem")
            if planning_view.runtime_identity != planning_context.runtime_identity:
                raise PlanningContextError("runtime identity do context e view diverge")
            if (
                presented_names is not None
                and frozenset(presented_names) != planning_view.presented_names
            ):
                raise PlanningContextError("presented_names diverge da view canonica")
            presented_names = planning_view.presented_names
        self.presented_names = (
            frozenset(presented_names)
            if presented_names is not None
            else (
                planning_context.eligible_names
                if planning_context is not None
                else None
            )
        )

    def validate(
        self, plan: Plan | Sequence[Mapping[str, Any]] | None
    ) -> ValidationReport:
        """Validate a plan and return one consolidated report."""
        errors: List[str] = []
        warnings: List[str] = []
        blocked: List[BlockedStep] = []
        if plan is None:
            errors.append(
                "Plano ausente ou em formato inválido (esperada uma lista de passos)."
            )
            return ValidationReport(False, errors, warnings, blocked)
        if isinstance(plan, Plan):
            typed_plan = plan
        elif isinstance(plan, Sequence) and not isinstance(plan, (str, bytes)):
            try:
                typed_plan = Plan.from_raw(plan)
            except PlanDecodeError as exc:
                errors.append(f"Plano ausente ou em formato inválido: {exc}.")
                return ValidationReport(False, errors, warnings, blocked)
        else:
            errors.append(
                "Plano ausente ou em formato inválido (esperada uma lista de passos)."
            )
            return ValidationReport(False, errors, warnings, blocked)
        if len(typed_plan) == 0:
            errors.append("Plano vazio: nenhum passo para executar.")
            return ValidationReport(False, errors, warnings, blocked)

        errors.extend(validate_plan_identities(typed_plan))
        errors.extend(
            validate_deferred_items(
                typed_plan,
                self.objective,
                self.canonical_deferred_references,
                self._validate_step_schema,
                deferred_step_validator=lambda step: self._validate_step_schema(
                    step, deferred_branch=True
                ),
            )
        )
        errors.extend(
            validate_result_bindings(
                typed_plan,
                canonical_references=self.canonical_deferred_references,
                result_data_schema_resolver=self._result_data_schema,
                target_schema_resolver=self._target_schema,
            )
        )
        if errors:
            return ValidationReport(False, errors, warnings, blocked)
        self._validate_schema_and_tools(typed_plan, blocked)
        check_analysis_notes(typed_plan, blocked)
        check_patch_without_read(typed_plan, warnings)
        check_consecutive_writes(typed_plan, warnings)
        check_inverted_dependencies(typed_plan, blocked)
        return ValidationReport(
            len(blocked) < len(typed_plan), errors, warnings, blocked
        )

    def _validate_consecutive_writes(
        self, plan: List[Dict[str, Any]], warnings: List[str]
    ) -> None:
        check_consecutive_writes(plan, warnings)

    def _validate_inverted_dependencies(
        self, plan: List[Dict[str, Any]], blocked: List[BlockedStep]
    ) -> None:
        check_inverted_dependencies(plan, blocked)
