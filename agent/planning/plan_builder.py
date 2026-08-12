import inspect
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, cast

from agent.parsers import validate_tool_args
from agent.planning.planning_context import validate_planning_tool_arguments

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


@dataclass(frozen=True)
class PlanBuildResult:
    plan: Optional[List[Dict[str, Any]]] = None
    blocked_answer: Optional[str] = None
    direct_answer: Optional[str] = None


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
        filtered = self._filter_plan(plan)
        if not filtered:
            self.orchestrator._emit("hard_block", {"reason": "plano vazio após filtros"})
            self.orchestrator.agent_state.project_last_result(
                "planner",
                {},
                {
                    "ok": False,
                    "done": True,
                    "status": "blocked",
                    "error": "plan_blocked",
                    "message": "Plano bloqueado pelas politicas de seguranca.",
                },
            )
            self.orchestrator.fail_task()
            return PlanBuildResult(
                plan=[],
                blocked_answer="Não foi possível executar esta ação; ela foi bloqueada pelas políticas de segurança.",
            )
        self.orchestrator.agent_state.set_plan(filtered)
        canonical = self.orchestrator.agent_state.plan
        self.orchestrator._emit("plan_created", {"steps": len(canonical), "plan": canonical})
        if self.orchestrator.verbose:
            print(f"[DEBUG] Plano canônico com {len(canonical)} passos: {canonical}")
        return PlanBuildResult(plan=canonical)

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
Use direct_response quando puder responder adequadamente com o conhecimento e o
contexto atuais e nenhuma ferramenta acrescentar observacao, computacao ou efeito
material. Use use_tools quando precisar observar um recurso, obter informacao
externa/atual, executar computacao deterministica relevante ou causar um efeito.
Cada passo deve usar exatamente uma ferramenta da lista e conter tool (string) e args (objeto).
Para file_writer, omita content quando ele precisar ser gerado durante a execução.
Use file_reader sem start_line/end_line para leitura automática em chunks.
Não use shell para escrever e não inclua um passo final sem ferramenta.
{PLANNING_GUIDANCE}
"""

    @staticmethod
    def _direct_answer(decision: Dict[str, Any]) -> Optional[str]:
        if decision.get("action") != "direct_response":
            return None
        answer = decision.get("answer")
        return answer.strip() if isinstance(answer, str) and answer.strip() else None

    def _normalize_decision(self, decision: Dict[str, Any]) -> Optional[List[Any]]:
        plan = decision.get("plan")
        if not isinstance(plan, list) or not plan:
            single = self._single_step(decision)
            return [single] if single else None
        if all(isinstance(step, str) for step in plan):
            single = self._single_step(decision)
            return [single] if single else []
        return plan

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

    def _filter_plan(self, plan: List[Any]) -> List[Dict[str, Any]]:
        filtered: List[Dict[str, Any]] = []
        for raw_step in plan:
            step = self._validated_step(raw_step)
            if step is not None:
                filtered.append(step)
        return filtered

    def _validated_step(self, raw_step: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(raw_step, dict):
            return None
        tool, args = raw_step.get("tool", ""), raw_step.get("args", {})
        planning_view = getattr(self.orchestrator, "get_planning_view", lambda _kind: None)("linear")
        if planning_view is not None:
            planning_tool = next(
                (item for item in planning_view.tools if item.name == tool),
                None,
            )
            if planning_tool is None:
                return None
            try:
                validate_planning_tool_arguments(planning_tool, args)
            except ValueError:
                return None
        else:
            valid, error = validate_tool_args(tool, args, self.orchestrator.skills)
            if not valid:
                if self.orchestrator.verbose:
                    print(f"[DEBUG] Passo removido por schema inválido: {raw_step} -> {error}")
                return None
        normalized_args = args if isinstance(args, dict) else {}
        empties_notes = tool == "file_writer" and "analysis_notes.md" in str(normalized_args.get("file_path", "")) and not str(normalized_args.get("content", "")).strip()
        return None if empties_notes else {"tool": tool, "args": normalized_args}
