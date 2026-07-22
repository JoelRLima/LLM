import os
from typing import Any, Dict, List, Optional, Tuple

from agent.parsers import validate_tool_args

PLANNING_GUIDANCE = """
Escolha a ferramenta de menor custo que resolva cada passo:
- localizar: directory_lister; buscar texto: grep; entender código: code_analyzer;
- ler: file_reader; editar: file_writer; executar Python: python_executor.
Prefira edições cirúrgicas na ordem ast_patch, patch, append e write.
Nunca use patch/ast_patch sem um file_reader anterior para o mesmo arquivo.
Em análises de segurança, comece com code_analyzer mode='security' em um arquivo.
Evite leituras repetidas, caches/logs no escopo e passos que apaguem conteúdo.
"""


class PlanBuilder:
    def __init__(self, orchestrator: Any):
        self.orchestrator = orchestrator

    def build_plan(self, objective: str) -> Tuple[Optional[List[Dict[str, Any]]], Optional[str]]:
        self._clear_analysis_notes()
        decision = self.orchestrator.context_manager.ask_model(
            self._build_prompt(objective),
            step_type="plan",
            base_prompt=getattr(self.orchestrator, "_cached_base_prompt", None),
            log_metric_callback=self.orchestrator._log_metric,
        )
        if self.orchestrator.verbose:
            print(f"[DEBUG] plan_decision bruto: {decision}")
        plan = self._normalize_decision(decision)
        if plan is None:
            return None, None
        filtered = self._filter_plan(plan)
        if not filtered:
            self.orchestrator._emit("hard_block", {"reason": "plano vazio após filtros"})
            self.orchestrator.fail_task()
            return [], "Não foi possível executar esta ação; ela foi bloqueada pelas políticas de segurança."
        self.orchestrator.agent_state.set_plan(filtered)
        canonical = self.orchestrator.agent_state.plan
        self.orchestrator._emit("plan_created", {"steps": len(canonical), "plan": canonical})
        if self.orchestrator.verbose:
            print(f"[DEBUG] Plano canônico com {len(canonical)} passos: {canonical}")
        return canonical, None

    @staticmethod
    def _clear_analysis_notes() -> None:
        if not os.path.exists("analysis_notes.md"):
            return
        try:
            with open("analysis_notes.md", "w", encoding="utf-8") as stream:
                stream.write("")
        except OSError:
            pass

    def _build_prompt(self, objective: str) -> str:
        hints = self.orchestrator.context_manager.get_file_hints(objective)
        hint_block = f"\nTamanhos conhecidos:\n{hints}\n" if hints else ""
        tools = self.orchestrator._build_tools_description(compact=True)
        return f"""Objetivo: {objective}{hint_block}
Ferramentas disponíveis:
{tools}

Crie um plano sequencial mínimo. Responda apenas com JSON:
{{"plan": [{{"tool": "file_reader", "args": {{"file_path": "cli.py"}}}}]}}
Cada passo deve usar exatamente uma ferramenta da lista e conter tool (string) e args (objeto).
Para file_writer, omita content quando ele precisar ser gerado durante a execução.
Use file_reader sem start_line/end_line para leitura automática em chunks.
Não use shell para escrever e não inclua um passo final sem ferramenta.
{PLANNING_GUIDANCE}
"""

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
        valid, error = validate_tool_args(tool, args, self.orchestrator.skills)
        if not valid:
            if self.orchestrator.verbose:
                print(f"[DEBUG] Passo removido por schema inválido: {raw_step} -> {error}")
            return None
        normalized_args = args if isinstance(args, dict) else {}
        empties_notes = tool == "file_writer" and "analysis_notes.md" in str(normalized_args.get("file_path", "")) and not str(normalized_args.get("content", "")).strip()
        return None if empties_notes else {"tool": tool, "args": normalized_args}
