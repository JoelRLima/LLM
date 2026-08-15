import inspect
import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, cast

PLANNING_GUIDANCE = """
Responder diretamente e uma acao valida. Use uma ferramenta somente quando ela
fornecer observacao, computacao deterministica relevante ou efeito que melhore
materialmente a execucao. Nao use ferramentas apenas porque estao disponiveis.
Escolha a ferramenta de menor custo que resolva cada passo:
- localizar: directory_lister; buscar texto: grep; entender código: code_analyzer;
- ler: file_reader; modificar/validar: code_task; executar Python: python_executor.
Para modificações, use code_task action='modify' ou action='repair'.
Em análises de segurança, comece com code_analyzer mode='security' em um arquivo.
Evite leituras repetidas, caches/logs no escopo e passos que apaguem conteúdo.
"""


class PlanningDecisionKind(str, Enum):
    EXECUTE = "execute"
    REPLAN = "replan"
    COMPLETE = "complete"
    BLOCK = "block"
    FAIL = "fail"


@dataclass(frozen=True)
class PlanBuildResult:
    plan: Optional[List[Dict[str, Any]]] = None
    blocked_answer: Optional[str] = None
    direct_answer: Optional[str] = None
    waiver_observation_index: Optional[int] = None
    kind: PlanningDecisionKind | None = None

    def __post_init__(self) -> None:
        if self.kind is not None:
            return
        if self.plan:
            selected = PlanningDecisionKind.EXECUTE
        elif self.blocked_answer:
            selected = PlanningDecisionKind.BLOCK
        elif self.direct_answer or self.waiver_observation_index is not None:
            selected = PlanningDecisionKind.COMPLETE
        else:
            selected = PlanningDecisionKind.REPLAN
        object.__setattr__(self, "kind", selected)


def build_planner_tools_description(orchestrator: Any, *, planner_kind: str, compact: bool) -> str:
    """Call the planner catalog contract without catching runtime TypeError."""

    builder = orchestrator._build_tools_description
    try:
        signature = inspect.signature(builder)
    except (TypeError, ValueError) as exc:
        if getattr(orchestrator, "planning_context", None) is not None:
            raise TypeError("canonical planner catalog signature is unavailable") from exc
        return cast(str, builder(compact=compact))
    supports_kind = "planner_kind" in signature.parameters or any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    )
    if supports_kind:
        return cast(str, builder(compact=compact, planner_kind=planner_kind))
    if getattr(orchestrator, "planning_context", None) is not None:
        raise TypeError("canonical planner catalog requires planner_kind")
    return cast(str, builder(compact=compact))


class PlanBuilder:
    def __init__(
        self,
        orchestrator: Any,
        *,
        analysis_notes_file: str | Path = "analysis_notes.md",
    ):
        self.orchestrator = orchestrator
        self.analysis_notes_file = Path(analysis_notes_file)

    def build_plan(self, objective: str) -> PlanBuildResult:
        self._clear_analysis_notes()
        decision = self.orchestrator.context_manager.ask_model(
            self._build_prompt(objective),
            step_type="plan",
            base_prompt=getattr(self.orchestrator, "_cached_base_prompt", None),
            log_metric_callback=self.orchestrator._log_metric,
        )
        if self.orchestrator.verbose:
            print(f"[DEBUG] plan_decision bruto: {decision}")
        direct_answer = self._direct_answer(decision)
        if direct_answer is not None:
            self.orchestrator._emit("direct_response", {})
            return PlanBuildResult(direct_answer=direct_answer)
        plan = self._normalize_decision(decision)
        if plan is None:
            return PlanBuildResult()
        if self.orchestrator.verbose:
            print(f"[DEBUG] Plano proposto com {len(plan)} passos: {plan}")
        return PlanBuildResult(plan=cast(List[Dict[str, Any]], plan))

    def continue_after_observation(
        self,
        objective: str,
        effect_evidence: str,
        observation_references: str,
    ) -> PlanBuildResult:
        summary = ""
        responder = getattr(self.orchestrator, "final_responder", None)
        summarize = getattr(responder, "_tool_results_summary", None)
        if callable(summarize):
            summary = str(summarize())
        decision = self.orchestrator.context_manager.ask_model(
            self._build_continuation_prompt(
                objective,
                summary,
                effect_evidence,
                observation_references,
                self._plan_progress(),
            ),
            step_type="continuation_plan",
            base_prompt=getattr(self.orchestrator, "_cached_base_prompt", None),
            log_metric_callback=self.orchestrator._log_metric,
        )
        action = decision.get("action")
        if action == "complete_without_effect":
            index = decision.get("observation_index")
            if set(decision) != {"action", "observation_index"}:
                return PlanBuildResult(kind=PlanningDecisionKind.FAIL)
            if type(index) is not int or index < 1:
                return PlanBuildResult(kind=PlanningDecisionKind.FAIL)
            return PlanBuildResult(
                waiver_observation_index=index,
                kind=PlanningDecisionKind.COMPLETE,
            )
        if action == "blocked":
            reason = decision.get("reason")
            if set(decision) != {"action", "reason"}:
                return PlanBuildResult(kind=PlanningDecisionKind.FAIL)
            return PlanBuildResult(
                blocked_answer=reason.strip()
                if isinstance(reason, str) and reason.strip()
                else "Efeito solicitado permanece pendente.",
                kind=PlanningDecisionKind.BLOCK,
            )
        if action != "execute" or set(decision) != {"action", "plan"}:
            return PlanBuildResult(kind=PlanningDecisionKind.FAIL)
        plan = self._normalize_decision(decision)
        if plan is None:
            return PlanBuildResult(kind=PlanningDecisionKind.FAIL)
        return PlanBuildResult(
            plan=cast(List[Dict[str, Any]], plan),
            kind=PlanningDecisionKind.EXECUTE,
        )

    def _clear_analysis_notes(self) -> None:
        if not self.analysis_notes_file.exists():
            return
        try:
            with self.analysis_notes_file.open("w", encoding="utf-8") as stream:
                stream.write("")
        except OSError:
            pass

    def _build_prompt(self, objective: str) -> str:
        hints = self.orchestrator.context_manager.get_file_hints(objective)
        hint_block = f"\nTamanhos conhecidos:\n{hints}\n" if hints else ""
        tools = build_planner_tools_description(
            self.orchestrator, planner_kind="linear", compact=True
        )
        return f"""Objetivo: {objective}{hint_block}
Ferramentas disponíveis:
{tools}

Escolha exatamente uma das duas respostas JSON:
{{"action": "direct_response", "answer": "resposta final ao usuario"}}
{{"action": "use_tools", "plan": [{{"tool": "ferramenta", "args": {{}}}}]}}
Nesta chamada, este contrato substitui exemplos legados de action=tool,
action=final ou plan como lista de strings presentes no prompt de sistema.
Use direct_response quando puder responder adequadamente com o conhecimento e o
contexto atuais e nenhuma ferramenta acrescentar observacao, computacao ou efeito
material. Use use_tools quando precisar observar um recurso, obter informacao
externa/atual, executar computacao deterministica relevante ou causar um efeito.
Quando usar use_tools, produza nesta unica decisao o plano executavel completo e
limitado para o objetivo. Inclua todos os passos cujos argumentos ja podem ser
determinados com seguranca; o runtime persiste o plano e executa seus passos sem
pedir uma nova decisao ao modelo entre eles. Nao reduza uma tarefa incondicional
a um unico passo apenas para decidir novamente depois.
Se um efeito depender de uma comparacao textual mecanica ainda desconhecida, use
um item deferred_condition logo apos a observacao segura. Ele aceita somente:
observation_ref como posicao local 1-based de um ToolStep anterior; predicate com
op='equals' e value literal presente no objetivo original; on_true como um unico
ToolStep concreto; e on_false exatamente {{"waive_effect":"write"}}. O runtime
vincula a referencia a identidade canonica do passo e resolve o ramo sem nova
decisao do modelo. Nunca esconda a condicao apenas no objective de uma ferramenta
de efeito nem torne esse efeito incondicional.
Nao use deferred_condition para julgamento semantico (por exemplo, "parece
incorreto" ou "evidencia suficiente"). Nesse caso, planeje somente a observacao
segura anterior ao frontier; a decisao focal existente permanece model-owned.
Cada ToolStep deve usar exatamente uma ferramenta da lista e conter tool (string)
e args (objeto).
Exemplo multi-passo quando os dois alvos ja sao conhecidos:
{{"action":"use_tools","plan":[
  {{"tool":"file_reader","args":{{"file_path":"a.txt"}}}},
  {{"tool":"file_reader","args":{{"file_path":"b.txt"}}}}
]}}
Para uma edicao incondicional de app.py, code_task ja seleciona o contexto:
{{"action":"use_tools","plan":[{{"tool":"code_task","args":{{"action":"modify","objective":"Aplicar a alteracao solicitada em app.py","targets":["app.py"]}}}}]}}
Para "se controle.txt for exatamente original, altere; caso contrario nao altere":
{{"action":"use_tools","plan":[
  {{"tool":"file_reader","args":{{"file_path":"controle.txt"}}}},
  {{"kind":"deferred_condition","observation_ref":1,
   "predicate":{{"op":"equals","value":"original"}},
   "on_true":{{"tool":"code_task","args":{{"action":"modify","objective":"Alterar controle.txt conforme solicitado","targets":["controle.txt"]}}}},
   "on_false":{{"waive_effect":"write"}}}}
]}}
Para file_writer, omita content quando ele precisar ser gerado durante a execução.
Use file_reader sem start_line/end_line para leitura automática em chunks.
Não use shell para escrever e não inclua um passo final sem ferramenta.
{PLANNING_GUIDANCE}
"""

    def _build_continuation_prompt(
        self,
        objective: str,
        observations: str,
        effect_evidence: str,
        observation_references: str,
        plan_progress: str,
    ) -> str:
        tools = build_planner_tools_description(
            self.orchestrator, planner_kind="linear", compact=True
        )
        return f"""A tarefa ainda esta em execucao.
Objetivo original: {objective}
Plano persistido e progresso real:
{plan_progress or '<nenhum passo persistido>'}
Observacoes reais dos passos ja executados:
{observations or '<nenhuma observacao>'}
Referencias canonicas para observacoes elegiveis:
{observation_references or '<nenhuma observacao elegivel>'}
Efeitos executados comprovados pelo runtime:
{effect_evidence}
Ferramentas disponiveis:
{tools}

O efeito pendente e uma obrigacao ainda nao resolvida, nao uma ordem para executar
independentemente da condicao observada. Primeiro confronte o objetivo condicional
original com o plano persistido e as observacoes. Decida somente a proxima transicao;
nao escreva resposta ao usuario. Nao repita uma observacao ja concluida com sucesso,
a menos que evidencia posterior demonstre que ela ficou obsoleta. Se a condicao
observada requer o efeito e ele ainda falta, use:
{{"action":"execute","plan":[{{"tool":"...","args":{{}}}}]}}
Quando uma observacao elegivel provar que o ramo condicional nao requer efeito,
referencie exatamente seu indice canonico:
{{"action":"complete_without_effect","observation_index":1}}
Se nao puder prosseguir, use:
{{"action":"blocked","reason":"..."}}
Uma afirmacao textual nao prova execucao nem dispensa efeito. Nao inclua answer,
effect_required ou effect_disposition. Este contrato substitui exemplos legados
de action=tool, action=final ou plan como lista de strings. Responda somente com
um JSON do contrato."""

    def _plan_progress(self) -> str:
        state = self.orchestrator.agent_state
        items: list[str] = []
        for index, step in enumerate(state.plan):
            status = state.get_step_status(index).value
            tool = json.dumps(str(step.get("tool", "")), ensure_ascii=True)
            items.append(f"{index + 1}: status={status}, tool={tool}")
        return "\n".join(items)

    @staticmethod
    def _direct_answer(decision: Dict[str, Any]) -> Optional[str]:
        if decision.get("action") != "direct_response":
            return None
        answer = decision.get("answer")
        return answer.strip() if isinstance(answer, str) and answer.strip() else None

    def _normalize_decision(self, decision: Dict[str, Any]) -> Optional[List[Any]]:
        plan = decision.get("plan")
        if isinstance(plan, list):
            return plan or None
        single = self._single_step(decision)
        return [single] if single else None

    def _single_step(self, decision: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        tool = decision.get("tool")
        args = decision.get("args", {})
        if not isinstance(args, dict):
            args = {}
        if not tool and "file_path" in decision:
            tool, args = "file_reader", decision
        elif not tool and "target" in decision:
            tool, args = "code_analyzer", decision
        if self.orchestrator.verbose and tool:
            print(f"[DEBUG] Plano extraído de campos soltos: {tool}")
        return {"tool": tool, "args": args} if tool else None
