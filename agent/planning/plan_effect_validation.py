"""Effect-intent checks shared by the plan schema validator."""

from __future__ import annotations

from typing import Any, Dict, cast

from agent.parsers import validate_tool_args
from agent.planning.effect_intent import effect_intent_error
from agent.planning.planning_context import validate_planning_tool_arguments
from agent.planning.provenance_validation import validate_planner_arguments
from agent.planning.task_semantics_authority import admit_effect_authority
from agent.planning.task_semantics_inference import predicate_resolutions_from_observations


class PlanEffectValidationMixin:
    objective: str
    available_observations: Any

    def _validate_context_plan_step(
        self: Any,
        tool_name: str,
        args: Dict[str, Any],
        bound_fields: set[str],
        *,
        allow_conditional_effect: bool = False,
        deferred_branch: bool = False,
    ) -> str | None:
        problem = cast(str | None, self._validate_context_step(tool_name, args, bound_fields))
        if problem:
            return problem
        problem = validate_planner_arguments(
            args,
            bound_fields,
            self._planning_tool(tool_name),
            self.objective,
            self.available_observations,
        )
        return problem or self._effect_error(
            tool_name,
            args,
            self._planning_tool(tool_name),
            allow_conditional_effect,
            deferred_branch=deferred_branch,
        )

    def _validate_descriptor_step(
        self: Any,
        tool_name: str,
        args: Dict[str, Any],
        bound_fields: set[str],
        descriptor: Any,
        *,
        allow_conditional_effect: bool = False,
        deferred_branch: bool = False,
    ) -> str | None:
        capability_error = cast(str | None, self._capability_error(tool_name, descriptor))
        if capability_error:
            return capability_error
        try:
            validate_planning_tool_arguments(descriptor, args, bound_fields)
        except ValueError as exc:
            return f"Schema inválido para '{tool_name}': {exc}"
        problem = validate_planner_arguments(
            args,
            bound_fields,
            descriptor,
            self.objective,
            self.available_observations,
        )
        return problem or self._effect_error(
            tool_name,
            args,
            descriptor,
            allow_conditional_effect,
            deferred_branch=deferred_branch,
        )

    def _validate_skill_step(
        self: Any,
        tool_name: str,
        args: Dict[str, Any],
        bound_fields: set[str],
        *,
        allow_conditional_effect: bool = False,
        deferred_branch: bool = False,
    ) -> str | None:
        valid, error = validate_tool_args(tool_name, args, self.skills, bound_fields)
        if not valid:
            return f"Schema inválido para '{tool_name}': {error or ''}"
        problem = validate_planner_arguments(
            args,
            bound_fields,
            self.skills.get(tool_name),
            self.objective,
            self.available_observations,
        )
        return problem or self._effect_error(
            tool_name,
            args,
            self.skills.get(tool_name),
            allow_conditional_effect,
            deferred_branch=deferred_branch,
        )

    def _effect_error(
        self: Any,
        tool_name: str,
        args: Dict[str, Any],
        contract: Any,
        allow_conditional_effect: bool,
        *,
        deferred_branch: bool = False,
    ) -> str | None:
        # ``allow_conditional_effect`` is retained as an API compatibility
        # flag for deferred materialization, but it never bypasses the
        # canonical predicate gate.  Conditional authority is admitted only
        # when trusted observations in the validator context resolve a branch.
        del allow_conditional_effect
        problem = effect_intent_error(
            self.objective,
            tool_name,
            args,
            contract,
            available_observations=self.available_observations,
        )
        if deferred_branch and problem and problem.startswith(
            "UNRESOLVED_CONDITIONAL_EFFECT:"
        ):
            # A deferred branch is a non-executable control payload until the
            # observation resolves it.  Suppressing this one admission-time
            # diagnostic grants no durable authority: materialization runs
            # the same effect gate again with trusted predicate evidence.
            return None
        if (
            getattr(self, "allow_conditional_preview", False)
            and problem
            and problem.startswith("PROHIBITED_EFFECT:")
            and self._conditional_preview_candidate(tool_name)
        ):
            # A continuation may carry a code-task proposal after trusted
            # evidence selected the no-write branch.  The proposal is admitted
            # only as a provisional observation; any persisted mutation is
            # still classified against the same FALSE predicate and the task
            # rollback boundary contains it.
            return None
        return problem

    def _conditional_preview_candidate(self, tool_name: str) -> bool:
        if str(tool_name).strip().casefold() != "code_task":
            return False
        authority = admit_effect_authority(self.objective)
        if not authority.has_conditional_candidate:
            return False
        resolutions = predicate_resolutions_from_observations(
            self.objective,
            self.available_observations,
        )
        return any(evidence.value is False for evidence in resolutions.values())


__all__ = ["PlanEffectValidationMixin"]
