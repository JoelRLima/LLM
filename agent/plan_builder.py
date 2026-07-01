import os
from typing import Any, Dict, List, Optional, Tuple
import logging

from agent.parsers import validate_tool_args

# Orientação estática injetada no prompt de planejamento: matriz de
# decisão objetivo -> ferramenta, política de custo, prioridade de edição,
# a regra de nunca gerar patch às cegas, e um checklist de autoavaliação
# para o modelo revisar o próprio plano antes de respondê-lo.
PLANNING_GUIDANCE = (
    "\n\n**Matriz de decisão (objetivo → ferramenta preferida):**\n"
    "| Objetivo | Ferramenta preferida |\n"
    "|---|---|\n"
    "| Encontrar arquivo | directory_lister |\n"
    "| Encontrar texto | grep |\n"
    "| Entender estrutura | code_analyzer |\n"
    "| Ler conteúdo | file_reader |\n"
    "| Editar trecho | file_writer (action='patch') |\n"
    "| Editar função | file_writer (action='ast_patch') |\n"
    "| Criar arquivo | file_writer (action='write') |\n"
    "\n"
    "**Política de custo — minimize, nesta ordem de prioridade:**\n"
    "1. Chamadas ao modelo (LLM)\n"
    "2. Chamadas de ferramentas\n"
    "3. Escrita em disco\n"
    "4. Leitura de arquivos\n"
    "5. Tamanho do contexto\n"
    "\n"
    "**Prioridade de edição — da mais para a menos cirúrgica:**\n"
    "1. ast_patch (substitui uma função/classe inteira pelo nome)\n"
    "2. patch (substitui um trecho de texto exato)\n"
    "3. append (adiciona conteúdo ao final do arquivo)\n"
    "4. write (último recurso — sobrescreve o arquivo inteiro)\n"
    "\n"
    "**Regra crítica:** NUNCA gere um passo de file_writer com action='patch' ou "
    "action='ast_patch' para um arquivo sem que exista, em algum passo anterior do "
    "mesmo plano, um file_reader que leia esse arquivo. Editar às cegas produz "
    "'old_content' que não corresponde ao conteúdo real do arquivo.\n"
    "\n"
    "**Checklist de autoavaliação (revise antes de responder):**\n"
    "- [ ] Para cada objetivo do plano, escolhi a ferramenta de menor custo que o resolve "
    "(ver matriz de decisão acima)?\n"
    "- [ ] Evitei ler ou analisar o mesmo arquivo mais de uma vez sem necessidade?\n"
    "- [ ] Toda edição (patch/ast_patch) tem um file_reader anterior lendo o mesmo arquivo?\n"
    "- [ ] Usei a opção de edição mais cirúrgica possível (ast_patch > patch > append > write)?\n"
    "- [ ] O plano tem o menor número de passos possível para atingir o objetivo com segurança?\n"
    "- [ ] Se estou usando grep com expressão regular, o padrão é SIMPLES, bem escapado e balanceado (sem parênteses não fechados)?\n"
    "- [ ] O plano tem o menor número de passos possível para atingir o objetivo com segurança?\n"
)


class PlanBuilder:
    def __init__(self, orchestrator: Any):
        self.orchestrator = orchestrator

    def build_plan(self, objective: str) -> Tuple[Optional[List[Dict[str, Any]]], Optional[str]]:
        if os.path.exists("analysis_notes.md"):
            try:
                with open("analysis_notes.md", "w", encoding="utf-8") as f:
                    f.write("")
            except Exception:
                pass

        file_hints = self.orchestrator.context_manager.get_file_hints(objective)
        hint_block = ""
        if file_hints:
            hint_block = (
                "\n\n**Tamanhos de arquivos conhecidos (use para planejar chunks):**\n"
                + file_hints
                + "\n"
            )

        plan_prompt = (
            f"Objetivo: {objective}{hint_block}\n\n"
            f"Ferramentas disponíveis:\n{self.orchestrator._build_tools_description(compact=True)}\n\n"
            "Crie um plano sequencial para atingir o objetivo. "
            "Cada passo deve conter exatamente UMA ferramenta.\n"
            "Responda APENAS com um JSON no seguinte formato:\n"
            "{\n"
            '  "plan": [\n'
            '    {"tool": "code_analyzer", "args": {"target": "cli.py", "mode": "file", "compact": true}},\n'
            '    {"tool": "file_reader", "args": {"file_path": "cli.py"}}\n'
            "  ]\n"
            "}\n"
            f"{PLANNING_GUIDANCE}\n"
            "Regras:\n"
            "- Use APENAS ferramentas da lista acima.\n"
            "- Cada objeto do plano deve ter os campos 'tool' (string) e 'args' (objeto).\n"
            "- Não inclua comentários, texto extra ou formatação fora do JSON.\n"
            "- Quando o objetivo for analisar um arquivo, inclua SEMPRE um passo para ler o conteúdo com file_reader.\n"
            "- Informe apenas o file_path no file_reader; o sistema divide automaticamente se necessário.\n"
            "- NÃO especifique start_line ou end_line ao usar file_reader, a menos que queira um trecho específico.\n"
            "- NÃO inclua passos para deletar, apagar ou esvaziar arquivos."
            "- NÃO inclua um passo 'final' de resposta. A resposta final de cada sub-objetivo será gerada automaticamente após a execução das ferramentas.\n"
            "- Para passos de file_writer, NÃO inclua o conteúdo no campo 'content'. Use 'content' como string vazia (\"\"). O sistema gerará o conteúdo automaticamente."
            "- Para alterar uma parte específica de um arquivo (ex.: uma linha, uma função), prefira usar file_writer com action='patch' (substituição exata de trecho) ou action='ast_patch' (substituição de função/classe por nome).\n"
            "- Só use action='write' quando precisar criar um arquivo novo ou substituir TODO o conteúdo."
            "- NÃO use shell para criar, modificar ou apagar arquivos. Use file_writer para qualquer operação de escrita."
            "- Ao usar python_executor, o código DEVE incluir print() para exibir resultados. Nunca passe expressões soltas como '2+2'."
        )
        plan_decision = self.orchestrator.context_manager.ask_model(
            plan_prompt,
            step_type="plan",
            base_prompt=getattr(self.orchestrator, "_cached_base_prompt", None),
            log_metric_callback=self.orchestrator._log_metric,
        )

        if self.orchestrator.verbose:
            print(f"[DEBUG] plan_decision bruto: {plan_decision}")

        plan = plan_decision.get("plan")
        if not plan or not isinstance(plan, list):
            # Fallback: a resposta não tem o campo "plan". Tenta extrair um
            # passo único a partir de campos soltos comuns (file_path, target).
            tool = plan_decision.get("tool")
            args = plan_decision.get("args", {})
            if not isinstance(args, dict):
                args = {}
            if not tool:
                if "file_path" in plan_decision:
                    tool = "file_reader"
                    args = plan_decision
                elif "target" in plan_decision:
                    tool = "code_analyzer"
                    args = plan_decision
            if tool:
                plan = [{"tool": tool, "args": args}]
                if self.orchestrator.verbose:
                    print(f"[DEBUG] Plano extraído de resposta sem 'plan': {plan}")
            else:
                return None, None

        if plan and isinstance(plan, list) and all(isinstance(s, str) for s in plan):
            tool = plan_decision.get("tool")
            args = plan_decision.get("args")
            if isinstance(tool, str) and isinstance(args, dict):
                plan = [{"tool": tool, "args": args}]
                if self.orchestrator.verbose:
                    print(f"[DEBUG] Plano corrigido (extraído de campos tool/args): {plan}")
            else:
                plan = []

        filtered_plan = []
        for step in plan:
            if not isinstance(step, dict):
                continue
            tool = step.get("tool", "")
            args = step.get("args", {})
            valid, error_msg = validate_tool_args(tool, args, self.orchestrator.skills)
            if not valid:
                if self.orchestrator.verbose:
                    print(f"[DEBUG] Passo removido por schema inválido: {step} -> {error_msg}")
                continue
            if not isinstance(args, dict):
                args = {}
            if tool == "file_writer" and "analysis_notes.md" in str(args.get("file_path", "")):
                content = str(args.get("content", ""))
                if content.strip() == "":
                    if self.orchestrator.verbose:
                        print(f"[DEBUG] Removido passo que esvazia analysis_notes.md: {step}")
                    continue
            filtered_plan.append({"tool": tool, "args": args})

        if not filtered_plan:
            self.orchestrator._emit("hard_block", {"reason": "plano vazio após filtros"})
            self.orchestrator.fail_task()
            return [], "Não foi possível executar esta ação. Ela foi bloqueada pelas políticas de segurança do agente."

        self.orchestrator.agent_state.plan = filtered_plan
        self.orchestrator.agent_state.plan_step = 0
        self.orchestrator._emit("plan_created", {"steps": len(filtered_plan), "plan": filtered_plan})
        if self.orchestrator.verbose:
            print(f"[DEBUG] Plano canônico com {len(filtered_plan)} passos: {filtered_plan}")
        return filtered_plan, None