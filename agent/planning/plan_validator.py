"""Read-only plan schema and policy validator."""
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from agent.parsers import validate_tool_args
from agent.planning.deferred_condition import is_deferred_condition
from agent.planning.deferred_validation import validate_deferred_items
from agent.planning.plan_policy_checks import (
    check_analysis_notes,
    check_consecutive_writes,
    check_inverted_dependencies,
    check_patch_without_read,
)
from agent.planning.planning_context import (
    PlanningContextError,
    PlanningContextSnapshot,
    PlanningTool,
    validate_planning_tool_arguments,
)
from agent.planning.presentation import PlanningPresentationSnapshot
from agent.planning.provenance_validation import validate_argument_provenance
from agent.planning.result_bindings import (
    ResultBindingError,
    binding_targets,
    validate_result_bindings,
)
from agent.planning.validation_repair import repairable_fields
from agent.skills.descriptor import result_data_schema_for_contract, target_schema_for_contract


@dataclass(frozen=True)
class BlockedStep:
    """Um passo do plano que nÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â£o pode ser executado como estÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¡."""
    index: int
    reason: str
    repairable_fields: frozenset[str] = frozenset()

    @property
    def is_validation_repair(self) -> bool:
        """Whether this is a deterministic, field-scoped pre-execution repair."""

        return bool(self.repairable_fields)

@dataclass
class ValidationReport:
    """Resultado de uma chamada a `PlanValidator.validate()`."""
    is_valid: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    blocked_steps: List[BlockedStep] = field(default_factory=list)

class PlanValidator:
    """Valida planos contra o schema das ferramentas, a lista de
    ferramentas permitidas para a tarefa, e um conjunto de heurÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â­sticas de
    seguranÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â§a e consistÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Âªncia.

    NÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â£o possui efeitos colaterais e nunca altera o plano recebido.
    """

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
    ) -> None:
        self.skills = skills
        self.active_skills = active_skills or []
        self.allowed_capabilities = allowed_capabilities
        self.tool_registry = tool_registry
        self.planning_context = planning_context
        self.planning_view = planning_view
        self.objective = objective
        self.canonical_deferred_references = canonical_deferred_references
        self.available_observations = tuple(available_observations or ())
        if planning_context is not None and planning_view is not None:
            if planning_view.planning_context_id != planning_context.snapshot_id:
                raise PlanningContextError("planning context e view divergem")
            if planning_view.runtime_identity != planning_context.runtime_identity:
                raise PlanningContextError("runtime identity do context e view diverge")
            if presented_names is not None and frozenset(presented_names) != planning_view.presented_names:
                raise PlanningContextError("presented_names diverge da view canonica")
            presented_names = planning_view.presented_names
        self.presented_names = (
            frozenset(presented_names)
            if presented_names is not None
        else (planning_context.eligible_names if planning_context is not None else None)
        )
    def validate(self, plan: Optional[List[Dict[str, Any]]]) -> ValidationReport:
        """Executa todas as checagens sobre `plan` e retorna um
        `ValidationReport` consolidado.

        `is_valid` ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â© `False` apenas quando o plano estÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¡ estruturalmente
        inutilizÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¡vel (ausente, nÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â£o ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â© uma lista, vazio, ou todos os passos
        acabaram bloqueados) ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â nesses casos o Orchestrator deve abortar a
        tarefa sem tentar replanejar. Quando `is_valid` ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â© `True` mas
        `blocked_steps` nÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â£o estÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¡ vazio, o plano ainda tem passos
        executÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¡veis e o Orchestrator deve acionar o Replanner apenas para
        os passos bloqueados.
        """
        errors: List[str] = []
        warnings: List[str] = []
        blocked: List[BlockedStep] = []
        if plan is None or not isinstance(plan, list):
            errors.append("Plano ausente ou em formato inv\u00e1lido (esperada uma lista de passos).")
            return ValidationReport(is_valid=False, errors=errors, warnings=warnings, blocked_steps=blocked)

        if len(plan) == 0:
            errors.append("Plano vazio: nenhum passo para executar.")
            return ValidationReport(is_valid=False, errors=errors, warnings=warnings, blocked_steps=blocked)

        errors.extend(validate_deferred_items(plan, self.objective, self.canonical_deferred_references, self._validate_step_schema))
        errors.extend(
            validate_result_bindings(
                plan,
                canonical_references=self.canonical_deferred_references,
                result_data_schema_resolver=self._result_data_schema,
                target_schema_resolver=self._target_schema,
            )
        )
        if errors:
            return ValidationReport(is_valid=False, errors=errors, warnings=warnings, blocked_steps=blocked)
        self._validate_schema_and_tools(plan, blocked)
        check_analysis_notes(plan, blocked)
        check_patch_without_read(plan, warnings)
        check_consecutive_writes(plan, warnings)
        check_inverted_dependencies(plan, blocked)
        is_valid = len(blocked) < len(plan)
        return ValidationReport(is_valid=is_valid, errors=errors, warnings=warnings, blocked_steps=blocked)

    # ------------------------------------------------------------------
    # Checagens individuais
    # ------------------------------------------------------------------

    @staticmethod
    def _step_args(step: Dict[str, Any]) -> Dict[str, Any]:
        args = step.get("args")
        return args if isinstance(args, dict) else {}

    def _validate_schema_and_tools(self, plan: List[Dict[str, Any]], blocked: List[BlockedStep]) -> None:
        """Valida, para cada passo: formato mÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â­nimo, existÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Âªncia da
        ferramenta, permissÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â£o (active_skills) e schema de argumentos."""
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

    def _validate_step_schema(self, step: Any) -> str | None:
        if not isinstance(step, dict) or "tool" not in step:
            return "Passo malformado: falta o campo 'tool'."
        tool = step["tool"]
        tool_name = str(tool)
        args = self._step_args(step)
        try:
            bound_fields = binding_targets(step) if "bindings" in step else set()
        except ResultBindingError:
            return "Bindings inv\u00e1lidos"
        if self.planning_context is not None:
            return self._validate_context_plan_step(tool_name, args, bound_fields)
        descriptor = self._descriptor(tool_name)
        if tool not in self.skills and descriptor is None:
            return f"Ferramenta '{tool}' n\u00e3o existe."
        if self.active_skills and tool not in self.active_skills and descriptor is None:
            return f"Ferramenta '{tool}' n\u00e3o est\u00e1 permitida para esta tarefa."
        if descriptor is not None:
            return self._validate_descriptor_step(tool_name, args, bound_fields, descriptor)
        return self._validate_skill_step(tool_name, args, bound_fields)

    def _validate_context_plan_step(
        self, tool_name: str, args: Dict[str, Any], bound_fields: set[str]
    ) -> str | None:
        problem = self._validate_context_step(tool_name, args, bound_fields)
        if problem:
            return problem
        return validate_argument_provenance(
            args=args,
            bound_fields=bound_fields,
            descriptor=self._planning_tool(tool_name),
            objective=self.objective,
            available_observations=self.available_observations,
        )

    def _validate_descriptor_step(
        self, tool_name: str, args: Dict[str, Any], bound_fields: set[str], descriptor: Any
    ) -> str | None:
        capability_error = self._capability_error(tool_name, descriptor)
        if capability_error:
            return capability_error
        try:
            validate_planning_tool_arguments(descriptor, args, bound_fields)
        except ValueError as exc:
            return f"Schema inv\u00e1lido para '{tool_name}': {exc}"
        return validate_argument_provenance(
            args=args,
            bound_fields=bound_fields,
            descriptor=descriptor,
            objective=self.objective,
            available_observations=self.available_observations,
        )

    def _validate_skill_step(
        self, tool_name: str, args: Dict[str, Any], bound_fields: set[str]
    ) -> str | None:
        valid, error = validate_tool_args(tool_name, args, self.skills, bound_fields)
        if not valid:
            return f"Schema inv\u00e1lido para '{tool_name}': {error or ''}"
        return validate_argument_provenance(
            args=args,
            bound_fields=bound_fields,
            descriptor=self.skills.get(tool_name),
            objective=self.objective,
            available_observations=self.available_observations,
        )

    def _validate_context_step(
        self, tool_name: str, args: Dict[str, Any], bound_fields: set[str] | None = None
    ) -> str | None:
        if self.presented_names is not None and tool_name not in self.presented_names:
            return f"Ferramenta '{tool_name}' n\u00e3o foi apresentada neste contexto."
        planning_tool = self._planning_tool(tool_name)
        if planning_tool is None:
            return f"Ferramenta '{tool_name}' n\u00e3o existe no contexto de planning."
        try:
            validate_planning_tool_arguments(planning_tool, args, bound_fields)
        except ValueError as exc:
            return f"Schema inv\u00e1lido para '{tool_name}': {exc}"
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
    def _result_data_schema(self, step: Mapping[str, Any]) -> Mapping[str, Any] | None:
        return result_data_schema_for_contract(self._contract(str(step.get("tool", ""))))
    def _target_schema(self, step: Mapping[str, Any], target: str) -> Mapping[str, Any] | None:
        return target_schema_for_contract(self._contract(str(step.get("tool", ""))), target)

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
                if self.planning_context is not None and self.planning_context.allowed_capabilities is not None
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
        return f"Ferramenta '{tool_name}' requer capacidades n\u00e3o autorizadas: {', '.join(sorted(missing))}"

    def _validate_consecutive_writes(self, plan: List[Dict[str, Any]], warnings: List[str]) -> None:
        """Aviso: duas escritas seguidas (sem nenhum outro passo entre elas)
        no mesmo arquivo ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â normalmente um sinal de que o plano poderia
        consolidar as duas ediÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â§ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Âµes em uma sÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â³."""
        check_consecutive_writes(plan, warnings)

    def _validate_inverted_dependencies(self, plan: List[Dict[str, Any]], blocked: List[BlockedStep]) -> None:
        """Bloqueia passos que leem/analisam um arquivo ANTES do passo
        file_writer que efetivamente o cria/produz no plano."""
        check_inverted_dependencies(plan, blocked)
