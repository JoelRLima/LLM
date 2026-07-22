"""Planejador hierárquico: decompõe um objetivo complexo em um MacroPlan.

Este módulo é totalmente desacoplado do `Orchestrator` e de qualquer
componente concreto de execução — toda comunicação com o modelo é feita
através da função `ask_model` injetada no construtor de
`HierarchicalPlanner`, seguindo a assinatura `ask_model(prompt, step_type)
-> dict`.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set

from agent.planning.task_graph import task_graph_from_macro_plan
from agent.runtime.logging import logger


class Priority(str, Enum):
    """Prioridade relativa de um sub-objetivo dentro do MacroPlan."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class MacroStep:
    """Um sub-objetivo independente dentro de um `MacroPlan`.

    Attributes:
        id: Identificador único do passo dentro do plano.
        title: Título curto e legível do passo.
        goal: Descrição do objetivo específico deste sub-passo, usada como
            entrada para o planejamento (micro-plano) e execução da etapa.
        priority: Prioridade relativa do passo.
        depends_on: Lista de ids de outros passos dos quais este depende
            (atualmente apenas informativo; não é usado para reordenar a
            execução).
        estimated_tools: Ferramentas que o modelo estima serem necessárias
            para completar este passo, já filtradas contra a lista de
            ferramentas válidas.
    """

    id: str
    title: str
    goal: str
    priority: Priority = Priority.MEDIUM
    depends_on: List[str] = field(default_factory=list)
    estimated_tools: List[str] = field(default_factory=list)


@dataclass
class MacroPlan:
    """Plano hierárquico composto por múltiplos `MacroStep`.

    Attributes:
        objective: Objetivo original e completo fornecido pelo usuário.
        steps: Lista ordenada de sub-objetivos (`MacroStep`) a executar.
        schema_version: Versão do formato do plano, para permitir evolução
            futura sem quebrar consumidores existentes (tracker, executor).
    """

    objective: str
    steps: List[MacroStep] = field(default_factory=list)
    schema_version: str = "1.0"


class HierarchicalPlanner:
    """Gera um `MacroPlan` a partir de um objetivo complexo, via LLM.

    Recebe por injeção uma função `ask_model(prompt, step_type) -> dict`
    responsável por consultar o modelo e retornar a resposta já parseada.
    Este planejador não conhece o `Orchestrator` nem qualquer outro
    componente concreto de execução.
    """

    def __init__(
        self,
        ask_model: Callable[[str, str], Dict[str, Any]],
        valid_tools: List[str],
    ) -> None:
        self.ask_model = ask_model
        self.valid_tools: Set[str] = set(valid_tools or [])

    def build_plan(self, objective: str) -> Optional[MacroPlan]:
        """Solicita ao modelo um `MacroPlan` para `objective` e o valida.

        Retorna `None` se o modelo não responder de forma utilizável, se a
        resposta não puder ser interpretada, ou se nenhum passo válido
        puder ser extraído dela — sinalizando ao chamador que ele deve
        recorrer ao fluxo de planejamento linear como fallback.
        """
        prompt = self._build_prompt(objective)
        try:
            response = self.ask_model(prompt, "macro_plan")
        except Exception as e:
            logger.warning(f"HierarchicalPlanner: falha ao consultar o modelo: {e}")
            return None

        if not isinstance(response, dict):
            logger.warning("HierarchicalPlanner: resposta do modelo não é um dict.")
            return None

        raw_steps = response.get("steps")
        if not raw_steps or not isinstance(raw_steps, list):
            return None

        steps: List[MacroStep] = []
        seen_ids: Set[str] = set()
        for raw_step in raw_steps:
            step = self._validate_step(raw_step, seen_ids)
            if step is not None:
                steps.append(step)
                seen_ids.add(step.id)

        if not steps:
            return None

        macro_plan = MacroPlan(objective=objective, steps=steps)
        try:
            task_graph_from_macro_plan(macro_plan)
        except ValueError as exc:
            logger.warning(f"HierarchicalPlanner: dependências inválidas: {exc}")
            return None
        return macro_plan

    def _validate_step(self, raw: Any, seen_ids: Set[str]) -> Optional[MacroStep]:
        """Valida e normaliza um único passo bruto retornado pelo modelo.

        Verifica campos obrigatórios (id, title, goal), valida o valor de
        `priority` contra o Enum `Priority` (com fallback para MEDIUM em
        caso de valor desconhecido) e filtra `estimated_tools` contra a
        lista de ferramentas válidas fornecida ao planejador.
        """
        if not isinstance(raw, dict):
            return None

        step_id = str(raw.get("id", "")).strip()
        title = str(raw.get("title", "")).strip()
        goal = str(raw.get("goal", "")).strip()
        if not step_id or not title or not goal:
            return None
        if step_id in seen_ids:
            return None

        priority_raw = str(raw.get("priority", Priority.MEDIUM.value)).strip().lower()
        try:
            priority = Priority(priority_raw)
        except ValueError:
            priority = Priority.MEDIUM

        depends_on_raw = raw.get("depends_on", [])
        depends_on = (
            [str(d).strip() for d in depends_on_raw if str(d).strip()]
            if isinstance(depends_on_raw, list)
            else []
        )

        estimated_tools_raw = raw.get("estimated_tools", [])
        estimated_tools = (
            [t for t in estimated_tools_raw if isinstance(t, str) and t in self.valid_tools]
            if isinstance(estimated_tools_raw, list)
            else []
        )

        return MacroStep(
            id=step_id,
            title=title,
            goal=goal,
            priority=priority,
            depends_on=depends_on,
            estimated_tools=estimated_tools,
        )

    def _build_prompt(self, objective: str) -> str:
        """Monta o prompt enviado ao modelo para gerar o MacroPlan."""
        tools_list = ", ".join(sorted(self.valid_tools)) or "(nenhuma ferramenta disponível)"
        return (
            f"Objetivo complexo: {objective}\n\n"
            f"Ferramentas disponíveis: {tools_list}\n\n"
            "Decomponha este objetivo em uma lista de sub-objetivos (macro passos) "
            "independentes e de alto nível, cada um representando uma unidade de "
            "trabalho coerente que poderá ser planejada e executada separadamente.\n"
            "Responda APENAS com um JSON no seguinte formato:\n"
            "{\n"
            '  "steps": [\n'
            "    {\n"
            '      "id": "step_1",\n'
            '      "title": "Título curto",\n'
            '      "goal": "Descrição clara e autocontida do que este sub-objetivo deve alcançar",\n'
            '      "priority": "medium",\n'
            '      "depends_on": [],\n'
            '      "estimated_tools": ["file_reader"]\n'
            "    }\n"
            "  ]\n"
            "}\n"
            "Regras:\n"
            "- 'priority' deve ser um destes valores: low, medium, high, critical.\n"
            "- 'estimated_tools' deve conter apenas nomes da lista de ferramentas disponíveis.\n"
            "- 'depends_on' deve conter apenas ids de outros passos deste mesmo plano.\n"
            "- Gere entre 2 e 8 sub-objetivos, cada um com escopo claro, autocontido e independente.\n"
            "- Não inclua comentários, texto extra ou formatação fora do JSON."
        )
