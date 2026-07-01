"""
PlanOptimizer: aplica transformações seguras e EQUIVALENTES a um plano já
diagnosticado pelo PlanValidator, reduzindo o custo total estimado de
execução sem alterar o resultado esperado da tarefa.

Otimizações permitidas:
    a. Remoção de passos duplicados exatos (mesma ferramenta + mesmos
       `args`), restrita a ferramentas marcadas como `cacheable=True` em
       `ToolMetadata` — ou seja, ferramentas sem efeitos colaterais e com
       resultado determinístico. Ferramentas com `cacheable=False` (ex.:
       file_writer, shell, python_executor) NUNCA têm passos removidos por
       essa regra, mesmo que os `args` sejam idênticos, pois repetir a
       chamada pode ser intencional (ex.: dois `append` idênticos devem
       gerar duas gravações).
    b. Consolidação de leituras múltiplas do mesmo arquivo com os mesmos
       argumentos — coberta pela mesma regra de deduplicação acima, já
       que duas leituras idênticas do mesmo arquivo (mesmo `file_path`,
       mesmos `start_line`/`end_line`, etc.) são, por definição, passos
       duplicados exatos.
    c. Reordenação de passos de leitura/busca/análise independentes
       (`category` em READ, SEARCH ou ANALYZE e `side_effects=False`)
       para aproximá-los uns dos outros, desde que isso nunca viole uma
       dependência (um passo de leitura nunca "ultrapassa" o passo
       file_writer que produziu o arquivo que ele lê). Ferramentas com
       `side_effects=True` NUNCA são movidas — apenas passos
       independentes de leitura podem se deslocar ao redor delas.

Este módulo NUNCA insere passos novos, NUNCA converte uma ferramenta em
outra, e NUNCA altera os argumentos de um passo existente. Todas as
decisões usam `ToolMetadata` — nenhum nome de ferramenta é hardcoded na
lógica de otimização (os nomes só aparecem nos dados de
`agent/tool_metadata.py`).
"""
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from agent.tool_metadata import ToolMetadata, TOOL_METADATA, estimate_step_cost, get_tool_metadata

_MOVABLE_CATEGORIES = {"READ", "SEARCH", "ANALYZE"}


@dataclass(frozen=True)
class ToolCost:
    """Custo estimado de uma única ferramenta/passo do plano."""
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
    """Otimiza planos aplicando transformações equivalentes e seguras,
    guiadas por `ToolMetadata`."""

    def __init__(self, tool_metadata: Optional[Dict[str, ToolMetadata]] = None):
        self.tool_metadata = tool_metadata if tool_metadata is not None else TOOL_METADATA

    # ------------------------------------------------------------------
    # Ponto de entrada público
    # ------------------------------------------------------------------

    def optimize(self, plan: List[Dict[str, Any]]) -> OptimizationReport:
        """Aplica as otimizações seguras a `plan` e retorna um relatório
        detalhado. NUNCA modifica `plan` in-place; sempre retorna uma nova
        lista em `optimized_steps`, deixando o `plan` original intacto."""
        if not plan or not isinstance(plan, list):
            safe_plan = list(plan) if isinstance(plan, list) else []
            return OptimizationReport(optimized_steps=safe_plan, cost_before=0, cost_after=0, changed=False)

        original = list(plan)
        cost_details_before = self._cost_details(original)
        cost_before = self._total_cost(cost_details_before)

        transformations: List[str] = []

        deduped, removed_duplicates = self._remove_exact_duplicates(original, transformations)
        reordered = self._group_reads(deduped, transformations)

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

    # ------------------------------------------------------------------
    # Auxiliares de custo
    # ------------------------------------------------------------------

    def _meta(self, tool: str) -> ToolMetadata:
        return self.tool_metadata.get(tool) or get_tool_metadata(tool)

    def _cost_details(self, plan: List[Dict[str, Any]]) -> List[ToolCost]:
        details = []
        for step in plan:
            if not isinstance(step, dict):
                continue
            tool = step.get("tool", "")
            args = step.get("args", {}) if isinstance(step.get("args"), dict) else {}
            details.append(ToolCost(tool=tool, cost=estimate_step_cost(tool, args)))
        return details

    @staticmethod
    def _total_cost(details: List[ToolCost]) -> int:
        return sum(d.cost for d in details)

    @staticmethod
    def _step_key(step: Dict[str, Any]) -> tuple:
        args = step.get("args", {})
        try:
            args_repr = json.dumps(args, sort_keys=True, ensure_ascii=False)
        except TypeError:
            args_repr = str(sorted(args.items())) if isinstance(args, dict) else str(args)
        return (step.get("tool", ""), args_repr)

    # ------------------------------------------------------------------
    # (a) + (b) Remoção de duplicatas exatas
    # ------------------------------------------------------------------

    def _remove_exact_duplicates(
        self, plan: List[Dict[str, Any]], transformations: List[str]
    ) -> tuple:
        """Remove passos duplicados exatos, restrito a ferramentas
        `cacheable=True` (sem efeitos colaterais, resultado determinístico).
        Retorna (novo_plano, quantidade_removida)."""
        seen = set()
        result: List[Dict[str, Any]] = []
        removed = 0

        for idx, step in enumerate(plan):
            if not isinstance(step, dict):
                result.append(step)
                continue

            tool = step.get("tool", "")
            meta = self._meta(tool)
            key = self._step_key(step)

            if meta.cacheable and key in seen:
                removed += 1
                transformations.append(
                    f"Passo {idx + 1} ('{tool}') removido: duplicata exata de um passo anterior equivalente."
                )
                continue

            if meta.cacheable:
                seen.add(key)
            result.append(step)

        return result, removed

    # ------------------------------------------------------------------
    # (c) Reordenação segura de leituras/buscas/análises independentes
    # ------------------------------------------------------------------

    @staticmethod
    def _depends_on(step: Dict[str, Any], producer_step: Dict[str, Any]) -> bool:
        """True se `step` (leitura/busca/análise) depende do arquivo
        produzido por `producer_step` (um file_writer)."""
        if not isinstance(producer_step, dict) or producer_step.get("tool") != "file_writer":
            return False
        p_args = producer_step.get("args") if isinstance(producer_step.get("args"), dict) else {}
        p_fp = p_args.get("file_path")
        if not p_fp:
            return False
        s_args = step.get("args") if isinstance(step.get("args"), dict) else {}
        s_fp = s_args.get("file_path") or s_args.get("target")
        return s_fp == p_fp

    def _group_reads(self, plan: List[Dict[str, Any]], transformations: List[str]) -> List[Dict[str, Any]]:
        """Aproxima passos de leitura/busca/análise independentes uns dos
        outros através de passes sucessivos de troca entre posições
        adjacentes (similar a um bubble sort), deslocando um passo de
        leitura para antes de um passo com efeitos colaterais sempre que
        ele não depender do resultado desse passo.

        Ferramentas com `side_effects=True` nunca são movidas: apenas os
        passos de leitura independentes "ultrapassam" elas, uma posição
        por vez, até não haver mais trocas seguras possíveis.
        """
        result = list(plan)
        n = len(result)
        moved_any = False

        for _ in range(n):
            swapped_this_pass = False
            for i in range(1, n):
                prev_step = result[i - 1]
                cur_step = result[i]
                if not isinstance(prev_step, dict) or not isinstance(cur_step, dict):
                    continue

                prev_meta = self._meta(prev_step.get("tool", ""))
                cur_meta = self._meta(cur_step.get("tool", ""))

                movable = (
                    cur_meta.category in _MOVABLE_CATEGORIES
                    and not cur_meta.side_effects
                    and prev_meta.side_effects
                    and not self._depends_on(cur_step, prev_step)
                )
                if movable:
                    result[i - 1], result[i] = result[i], result[i - 1]
                    swapped_this_pass = True
                    moved_any = True

            if not swapped_this_pass:
                break

        if moved_any:
            transformations.append(
                "Passos de leitura/busca/análise independentes foram reagrupados, "
                "sem ultrapassar ferramentas com efeitos colaterais das quais dependem."
            )

        return result
