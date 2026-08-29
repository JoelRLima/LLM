"""Schema and compatibility checks mixed into the canonical PlanValidator."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Dict, List, cast

from agent.planning.deferred_condition import is_deferred_condition
from agent.planning.plan_effect_validation import PlanEffectValidationMixin
from agent.planning.plan_model import (
    DeferredToolBranch,
    Plan,
    PlanStep,
    ToolPlanStep,
)
from agent.planning.plan_policy_checks import (
    check_consecutive_writes,
    check_inverted_dependencies,
)
from agent.planning.plan_validation_types import BlockedStep
from agent.planning.planning_context import PlanningTool, validate_planning_tool_arguments
from agent.planning.result_bindings import ResultBindingError, binding_targets
from agent.planning.validation_repair import repairable_fields
from agent.skills.descriptor import (
    result_data_schema_for_contract,
    target_schema_for_contract,
)


class PlanValidatorSchemaMixin(PlanEffectValidationMixin):
    planning_context: Any
    skills: Dict[str, Any]
    active_skills: List[str]
    allowed_capabilities: frozenset[str] | None
    tool_registry: Any
    presented_names: frozenset[str] | None

    @staticmethod
    def _step_args(
        step: PlanStep | DeferredToolBranch | Mapping[str, Any],
    ) -> Dict[str, Any]:
        def thaw(value: Any) -> Any:
            if isinstance(value, Mapping):
                return {key: thaw(item) for key, item in value.items()}
            if isinstance(value, tuple):
                return [thaw(item) for item in value]
            if isinstance(value, list):
                return [thaw(item) for item in value]
            if isinstance(value, frozenset):
                return [thaw(item) for item in value]
            return value

        if isinstance(step, ToolPlanStep):
            return cast(Dict[str, Any], thaw(step.args))
        if isinstance(step, DeferredToolBranch):
            return cast(Dict[str, Any], thaw(step.args))
        args = step.get("args")
        return dict(args) if isinstance(args, Mapping) else {}

    def _validate_schema_and_tools(
        self, plan: Plan, blocked: List[BlockedStep]
    ) -> None:
        for idx, step in enumerate(plan):
            if is_deferred_condition(step):
                continue
            problem = self._validate_step_schema(step)
            if problem:
                blocked.append(
                    BlockedStep(
                        idx,
                        problem,
                        repairable_fields(step, problem),
                    )
                )

    def _validate_step_schema(
        self,
        step: Any,
        *,
        allow_conditional_effect: bool = False,
        deferred_branch: bool = False,
    ) -> str | None:
        tool, tool_name, args, bound_fields, binding_error = self._step_parts(step)
        if binding_error is not None:
            return binding_error
        if tool is None:
            return "Passo malformado: falta o campo 'tool'."
        if self.planning_context is not None:
            return self._validate_context_plan_step(
                tool_name,
                args,
                bound_fields,
                allow_conditional_effect=allow_conditional_effect,
                deferred_branch=deferred_branch,
            )
        descriptor = self._descriptor(tool_name)
        if tool not in self.skills and descriptor is None:
            return f"Ferramenta '{tool}' não existe."
        if self.active_skills and tool not in self.active_skills and descriptor is None:
            return f"Ferramenta '{tool}' não está permitida para esta tarefa."
        if descriptor is not None:
            return self._validate_descriptor_step(
                tool_name,
                args,
                bound_fields,
                descriptor,
                allow_conditional_effect=allow_conditional_effect,
                deferred_branch=deferred_branch,
            )
        return self._validate_skill_step(
            tool_name,
            args,
            bound_fields,
            allow_conditional_effect=allow_conditional_effect,
            deferred_branch=deferred_branch,
        )

    def _step_parts(
        self, step: Any
    ) -> tuple[Any, str, Dict[str, Any], set[str], str | None]:
        if isinstance(step, ToolPlanStep):
            return step.tool, step.tool, self._step_args(step), set(step.bindings or ()), None
        if isinstance(step, DeferredToolBranch):
            return step.tool, step.tool, self._step_args(step), set(), None
        if isinstance(step, Mapping) and "tool" in step:
            tool = step["tool"]
            try:
                fields = binding_targets(step) if "bindings" in step else set()
            except ResultBindingError:
                return tool, str(tool), self._step_args(step), set(), "Bindings inválidos"
            return tool, str(tool), self._step_args(step), fields, None
        return None, "", {}, set(), None

    def _validate_context_step(
        self,
        tool_name: str,
        args: Dict[str, Any],
        bound_fields: set[str] | None = None,
    ) -> str | None:
        if self.presented_names is not None and tool_name not in self.presented_names:
            return f"Ferramenta '{tool_name}' não foi apresentada neste contexto."
        planning_tool = self._planning_tool(tool_name)
        if planning_tool is None:
            return f"Ferramenta '{tool_name}' não existe no contexto de planning."
        try:
            validate_planning_tool_arguments(planning_tool, args, bound_fields)
        except ValueError as exc:
            return f"Schema inválido para '{tool_name}': {exc}"
        return self._capability_error(tool_name, planning_tool)

    def _descriptor(self, tool_name: str) -> Any:
        if self.tool_registry is None:
            return None
        try:
            return self.tool_registry.descriptor(tool_name)
        except KeyError:
            return None

    def _contract(self, tool_name: str) -> Any:
        if self.planning_context is not None:
            return self._planning_tool(tool_name)
        return self._descriptor(tool_name) or self.skills.get(tool_name)

    def _result_data_schema(
        self, step: PlanStep | Mapping[str, Any]
    ) -> Mapping[str, Any] | None:
        tool = step.tool if isinstance(step, ToolPlanStep) else str(step.get("tool", ""))
        return result_data_schema_for_contract(self._contract(tool))

    def _target_schema(
        self, step: PlanStep | Mapping[str, Any], target: str
    ) -> Mapping[str, Any] | None:
        tool = step.tool if isinstance(step, ToolPlanStep) else str(step.get("tool", ""))
        return target_schema_for_contract(self._contract(tool), target)

    def _planning_tool(self, tool_name: str) -> PlanningTool | None:
        if self.planning_context is None:
            return None
        return next(
            (tool for tool in self.planning_context.tools if tool.name == tool_name),
            None,
        )

    def _capability_error(self, tool_name: str, descriptor: Any) -> str | None:
        if isinstance(descriptor, PlanningTool):
            capabilities = descriptor.required_capabilities
            allowed = (
                self.planning_context.allowed_capabilities
                if self.planning_context is not None
                and self.planning_context.allowed_capabilities is not None
                else self.allowed_capabilities
            )
        elif self.planning_context is None:
            capabilities = frozenset(getattr(descriptor, "capabilities", frozenset()))
            allowed = self.allowed_capabilities
        else:
            capabilities = frozenset()
            allowed = self.allowed_capabilities
        if allowed is None:
            return None
        missing = capabilities - allowed
        if not missing:
            return None
        return f"Ferramenta '{tool_name}' requer capacidades não autorizadas: {', '.join(sorted(missing))}"

    def _validate_consecutive_writes(
        self, plan: List[Dict[str, Any]], warnings: List[str]
    ) -> None:
        check_consecutive_writes(plan, warnings)

    def _validate_inverted_dependencies(
        self, plan: List[Dict[str, Any]], blocked: List[BlockedStep]
    ) -> None:
        check_inverted_dependencies(plan, blocked)
