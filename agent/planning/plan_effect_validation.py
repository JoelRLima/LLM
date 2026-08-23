"""Effect-intent checks shared by the plan schema validator."""

from __future__ import annotations

from typing import Any, Dict, cast

from agent.parsers import validate_tool_args
from agent.planning.effect_intent import effect_intent_error
from agent.planning.planning_context import validate_planning_tool_arguments
from agent.planning.provenance_validation import validate_planner_arguments


class PlanEffectValidationMixin:
    def _validate_context_plan_step(
        self: Any,
        tool_name: str,
        args: Dict[str, Any],
        bound_fields: set[str],
        *,
        allow_conditional_effect: bool = False,
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
        )

    def _validate_descriptor_step(
        self: Any,
        tool_name: str,
        args: Dict[str, Any],
        bound_fields: set[str],
        descriptor: Any,
        *,
        allow_conditional_effect: bool = False,
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
        return problem or self._effect_error(tool_name, args, descriptor, allow_conditional_effect)

    def _validate_skill_step(
        self: Any,
        tool_name: str,
        args: Dict[str, Any],
        bound_fields: set[str],
        *,
        allow_conditional_effect: bool = False,
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
        )

    def _effect_error(
        self: Any,
        tool_name: str,
        args: Dict[str, Any],
        contract: Any,
        allow_conditional_effect: bool,
    ) -> str | None:
        if allow_conditional_effect:
            return None
        return effect_intent_error(self.objective, tool_name, args, contract)


__all__ = ["PlanEffectValidationMixin"]
