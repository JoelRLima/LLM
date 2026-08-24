"""Safe metadata-guided plan optimizer."""
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from agent.planning.deferred_condition import is_deferred_condition
from agent.planning.planning_context import PlanningContextSnapshot
from agent.planning.presentation import PlanningPresentationSnapshot
from agent.planning.result_bindings import has_result_bindings, referenced_step_ids
from agent.planning.tool_metadata import TOOL_METADATA, ToolMetadata, estimate_step_cost, get_tool_metadata


class PlanningOptimizationError(ValueError):
    """Raised when a canonical planning plan references unknown metadata."""


@dataclass(frozen=True)
class ToolCost:
    """Custo estimado de uma Ãºnica ferramenta/passo do plano."""
    tool: str
    cost: int


@dataclass
class OptimizationReport:
    """Resultado de uma chamada a `PlanOptimizer.optimize()`."""
    optimized_steps: List[Dict[str, Any]] = field(default_factory=list)
    removed_duplicates: int = 0
    cost_before: int = 0
    cost_after: int = 0
    cost_details_before: List[ToolCost] = field(default_factory=list)
    cost_details_after: List[ToolCost] = field(default_factory=list)
    transformations: List[str] = field(default_factory=list)
    changed: bool = False


class PlanOptimizer:
    """Otimiza planos aplicando transformaÃ§Ãµes equivalentes e seguras,
    guiadas por `ToolMetadata`."""

    def __init__(
        self,
        tool_metadata: Optional[Dict[str, ToolMetadata]] = None,
        *,
        planning_context: PlanningContextSnapshot | None = None,
        presented_names: frozenset[str] | None = None,
        planning_view: PlanningPresentationSnapshot | None = None,
    ):
        self.tool_metadata = tool_metadata if tool_metadata is not None else TOOL_METADATA
        self.planning_context = planning_context
        if planning_context is not None:
            if planning_view is not None:
                if planning_view.planning_context_id != planning_context.snapshot_id:
                    raise PlanningOptimizationError("planning context e view divergem")
                if planning_view.runtime_identity != planning_context.runtime_identity:
                    raise PlanningOptimizationError("runtime identity do context e view diverge")
                if presented_names is not None and frozenset(presented_names) != planning_view.presented_names:
                    raise PlanningOptimizationError("presented_names diverge da view canonica")
                self.tool_metadata = planning_view.metadata_dict()
            else:
                names = planning_context.eligible_names if presented_names is None else presented_names
                self.tool_metadata = planning_context.present("optimizer", names).metadata_dict()

    def optimize(self, plan: List[Dict[str, Any]]) -> OptimizationReport:
        """Aplica as otimizaÃ§Ãµes seguras a `plan` e retorna um relatÃ³rio
        detalhado. NUNCA modifica `plan` in-place; sempre retorna uma nova
        lista em `optimized_steps`, deixando o `plan` original intacto."""
        if not plan or not isinstance(plan, list):
            safe_plan = list(plan) if isinstance(plan, list) else []
            return OptimizationReport(optimized_steps=safe_plan, cost_before=0, cost_after=0, changed=False)

        original = list(plan)
        cost_details_before = self._cost_details(original)
        cost_before = self._total_cost(cost_details_before)

        if any(is_deferred_condition(step) for step in original):
            return OptimizationReport(
                optimized_steps=original,
                cost_before=cost_before,
                cost_after=cost_before,
                cost_details_before=cost_details_before,
                cost_details_after=list(cost_details_before),
                changed=False,
            )

        transformations: List[str] = []

        reordered, removed_duplicates = self._remove_exact_duplicates(original, transformations)

        cost_details_after = self._cost_details(reordered)
        cost_after = self._total_cost(cost_details_after)

        changed = removed_duplicates > 0 or reordered != original

        return OptimizationReport(
            optimized_steps=reordered,
            removed_duplicates=removed_duplicates,
            cost_before=cost_before,
            cost_after=cost_after,
            cost_details_before=cost_details_before,
            cost_details_after=cost_details_after,
            transformations=transformations,
            changed=changed,
        )

    def _meta(self, tool: str) -> ToolMetadata:
        if self.planning_context is not None:
            metadata = self.tool_metadata.get(tool)
            if metadata is None:
                raise PlanningOptimizationError(
                    f"ferramenta '{tool}' ausente da view canônica de planning"
                )
            return metadata
        return self.tool_metadata.get(tool) or get_tool_metadata(tool)

    def _cost_details(self, plan: List[Dict[str, Any]]) -> List[ToolCost]:
        details = []
        for step in plan:
            if not isinstance(step, dict):
                continue
            if is_deferred_condition(step):
                details.append(ToolCost(tool="deferred_condition", cost=0))
                continue
            tool = step.get("tool", "")
            args = step.get("args", {}) if isinstance(step.get("args"), dict) else {}
            details.append(ToolCost(tool=tool, cost=self._estimate_cost(tool, args)))
        return details

    def _estimate_cost(self, tool: str, args: Dict[str, Any]) -> int:
        if self.planning_context is None:
            return int(estimate_step_cost(tool, args))
        return int(self._meta(tool).cost)

    @staticmethod
    def _total_cost(details: List[ToolCost]) -> int:
        return sum(d.cost for d in details)

    @staticmethod
    def _step_key(step: Dict[str, Any]) -> tuple:
        tool = step.get("tool", "")
        args = step.get("args", {})
        if tool == "code_analyzer":
            args = PlanOptimizer._code_analyzer_semantic_args(args)
        try:
            args_repr = json.dumps(args, sort_keys=True, ensure_ascii=False)
        except TypeError:
            args_repr = str(sorted(args.items())) if isinstance(args, dict) else str(args)
        return (tool, args_repr)

    @staticmethod
    def _code_analyzer_semantic_args(args: Any) -> Any:
        if not isinstance(args, dict):
            return args
        normalized = dict(args)
        normalized.setdefault("mode", "file")
        normalized.setdefault("include_code", False)
        normalized.setdefault("compact", False)
        return normalized
    # Directory output is not a canonical observation of every descendant:
    # discovery exclusions and per-file failures are observable at runtime.
    def _remove_exact_duplicates(
        self, plan: List[Dict[str, Any]], transformations: List[str]
    ) -> tuple:
        """Remove passos duplicados exatos, restrito a ferramentas
        `cacheable=True` (sem efeitos colaterais, resultado determinÃ­stico).
        Retorna (novo_plano, quantidade_removida)."""
        seen: set[tuple[Any, ...]] = set()
        result: List[Dict[str, Any]] = []
        removed = 0
        # Never drop a producer/consumer identity while bindings are present.
        # Their semantic dependency is richer than tool + concrete args.
        referenced = referenced_step_ids(plan)

        for idx, step in enumerate(plan):
            if not isinstance(step, dict):
                result.append(step)
                continue
            if is_deferred_condition(step):
                result.append(step)
                continue

            tool = step.get("tool", "")
            meta = self._meta(tool)
            if meta.modifies_workspace or meta.writes_disk or meta.side_effects:
                seen.clear()
            key = self._step_key(step)

            step_id = str(step.get("_step_id") or "")
            bound_step = has_result_bindings(step)
            if meta.cacheable and key in seen and not bound_step and step_id not in referenced:
                removed += 1
                duplicate_kind = (
                    "duplicata semântica"
                    if tool == "code_analyzer"
                    else "duplicata exata"
                )
                transformations.append(
                    f"Passo {idx + 1} ('{tool}') removido: {duplicate_kind} de um passo anterior equivalente."
                )
                continue

            if meta.cacheable and not bound_step:
                seen.add(key)
            result.append(step)

        return result, removed
