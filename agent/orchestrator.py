import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.auto_coder import AutoCoder
from agent.cancellation import CancellationToken
from agent.complexity import is_hierarchical
from agent.context_manager import ContextManager
from agent.error_handler import ErrorHandler
from agent.final_response import FinalResponder
from agent.hierarchical_executor import HierarchicalExecutor
from agent.hierarchical_planner import HierarchicalPlanner
from agent.incremental_summarizer import IncrementalSummarizer
from agent.plan_builder import PlanBuilder
from agent.plan_executor import PlanExecutor
from agent.plan_optimizer import PlanOptimizer
from agent.plan_validator import PlanValidator
from agent.reactive_loop import ReactiveLoop
from agent.replan import ReplanContext, replan
from agent.tool_metadata import TOOL_METADATA
from agent.router import _is_clearly_trivial, route_objective
from agent.state import AgentState
from agent.task_report import TaskReportBuilder
from agent.task_tracker import TaskTracker
from agent.tool_executor import ToolExecutor
from agent.workspace import WorkspaceManager
from agent.watchdog import Watchdog
from logger import logger
from session import ChatSession

CONTEXT_LIMIT = 8192

CONTEXT_COMPRESSION_THRESHOLD = 0.8
AGENT_METRICS_FILE = "agent_metrics.jsonl"
MAX_MEMORY_BACKUPS = 5
MEMORY_BACKUP_DIR = "memory_backups"
DEFAULT_AGENT_MAX_TOKENS = 2048
FALLBACK_AGENT_MAX_TOKENS = 4096

STEP_BUDGETS = {
    "plan": 4096,
    "final": 4096,
    "tool_decision": 2048,
}

TOOL_DECISION_BUDGETS = {
    "file_writer": 1024,
    "python_executor": 512,
    "shell": 256,
    "grep": 150,
    "code_analyzer": 150,
    "file_reader": 150,
    "directory_lister": 150,
    "session_memory": 150,
    "summarize": 300,
    "web_search": 200,
    "git": 200,
    "echo": 100,
    "calculator": 100,
}


class Orchestrator:
    def __init__(
        self,
        session: ChatSession,
        skills: Optional[List[Any]] = None,
        verbose: bool = False,
        checkpoint_file: str = "agent_checkpoint.json",
    ) -> None:
        self.session = session
        self.skills: Dict[str, Any] = {}
        self.max_steps: int = 15
        self.max_total_actions: int = 20
        self.max_early_final_attempts: int = 3
        self.max_loop_repetitions: int = 3
        self.verbose: bool = verbose
        self.active_skills: List[str] = []
        self._task_failed = False
        self._cancelled = False
        self.checkpoint_file: str = checkpoint_file
        # Linha (no arquivo agent_metrics.jsonl) a partir da qual as
        # métricas pertencem à tarefa atual. É recalculada no início de
        # cada `run()`, servindo como "marca d'água" (reset) para que
        # `_get_metrics_for_task` só retorne entradas desta execução.
        self._metrics_start_line: int = 0

        self.agent_state = AgentState()
        self.workspace = WorkspaceManager(verbose=self.verbose)
        self.context_manager = ContextManager(self.session, self.agent_state, verbose=self.verbose)
        self.auto_coder = AutoCoder(self)
        self.reactive_loop = ReactiveLoop(self)
        self.plan_builder = PlanBuilder(self)
        self.plan_executor = PlanExecutor(self)
        self.final_responder = FinalResponder(self)
        self.tool_executor = ToolExecutor(self)
        self.watchdog = Watchdog()

        if skills:
            for s in skills:
                self.register_skill(s)

    def register_skill(self, skill: Any) -> None:
        self.skills[skill.name] = skill

    def unregister_skill(self, name: str) -> None:
        self.skills.pop(name, None)

    def _build_tools_description(self, compact: bool = False) -> str:
        out = []
        for s in self.skills.values():
            if not self.active_skills or s.name in self.active_skills:
                if compact:
                    out.append(f"- {s.name}: {s.description}")
                else:
                    schema = json.dumps(s.get_schema(), indent=2, ensure_ascii=False)
                    out.append(f"- {s.name}: {s.description}\nArgs: {schema}")
        return "\n".join(out)

    def remember(self, key: str, value: Any, section: str = "key_findings") -> None:
        self.agent_state.memory.remember(key, value, section)

    def forget(self, key: str) -> None:
        self.agent_state.memory.forget(key)

    def clear_memory(self) -> None:
        self.agent_state.memory.clear()
        self.agent_state.events.clear()

    def save_memory_to_file(self, path: str = "agent_memory.json") -> str:
        return self.agent_state.memory.save_to_file(path)

    def load_memory_from_file(self, path: str = "agent_memory.json") -> str:
        return self.agent_state.memory.load_from_file(path)

    def _save_checkpoint(self) -> None:
        """Salva o estado atual da tarefa em disco para possibilitar retomada
        após uma interrupção (Ctrl+C, queda de energia, etc.).

        A escrita é feita em um arquivo temporário e depois renomeada
        atomicamente (`os.replace`), evitando checkpoints corrompidos em caso
        de interrupção durante a própria gravação. Falhas de gravação não
        devem interromper a execução do agente.
        """
        try:
            checkpoint_data = self.agent_state.to_checkpoint_dict()
            tmp_path = f"{self.checkpoint_file}.tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(checkpoint_data, f, indent=2, ensure_ascii=False, default=str)
            os.replace(tmp_path, self.checkpoint_file)
        except Exception as e:
            logger.warning(f"Falha ao salvar checkpoint: {e}")

    def _load_checkpoint(self) -> Optional[Dict[str, Any]]:
        """Carrega o checkpoint salvo em disco, se existir e for válido.

        Retorna `None` silenciosamente se o arquivo não existir ou estiver
        corrompido/ilegível, garantindo que uma nova tarefa possa iniciar
        normalmente sem que o checkpoint quebre a execução.
        """
        if not os.path.exists(self.checkpoint_file):
            return None
        try:
            with open(self.checkpoint_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return None
            return data
        except (json.JSONDecodeError, OSError, UnicodeDecodeError, ValueError) as e:
            logger.warning(f"Checkpoint corrompido ou ilegível, ignorando: {e}")
            return None

    def _delete_checkpoint(self) -> None:
        """Remove o arquivo de checkpoint ao final da tarefa (sucesso ou falha)."""
        try:
            if os.path.exists(self.checkpoint_file):
                os.remove(self.checkpoint_file)
        except OSError as e:
            logger.warning(f"Falha ao remover checkpoint: {e}")

    def _emit(self, event_type: str, data: Dict[str, Any] = None) -> None:
        event = {
            "type": event_type,
            "step": self.agent_state.objective is not None,
            "data": data or {},
        }
        self.agent_state.events.append(event)
        if self.verbose:
            emoji = {
                "plan_created": "📋",
                "tool_start": "⚙️",
                "tool_end": "✅",
                "final": "💬",
                "error": "❌",
                "hard_block": "🚫",
                "loop_detected": "🔄",
            }.get(event_type, "•")
            print(f"{emoji} [{event_type}] {data}")

        # Checkpoint incremental: persiste o progresso assim que um passo
        # da tarefa é concluído com sucesso, permitindo retomada posterior.
        if event_type == "tool_end":
            self._save_checkpoint()

    def _log_metric(self, entry: Dict[str, Any]) -> None:
        try:
            with open(AGENT_METRICS_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.warning(f"Falha ao registrar métrica: {e}")

    def _count_metrics_lines(self) -> int:
        """Conta quantas linhas já existem em `agent_metrics.jsonl`.

        Usado como marca d'água no início de uma tarefa para que
        `_get_metrics_for_task` só considere entradas gravadas durante a
        execução atual. Retorna 0 se o arquivo não existir ou não puder ser
        lido.
        """
        if not os.path.exists(AGENT_METRICS_FILE):
            return 0
        try:
            with open(AGENT_METRICS_FILE, "r", encoding="utf-8") as f:
                return sum(1 for _ in f)
        except OSError as e:
            logger.warning(f"Falha ao contar linhas de métricas: {e}")
            return 0

    def _get_metrics_for_task(self) -> List[Dict[str, Any]]:
        """Lê as entradas de `agent_metrics.jsonl` relativas à tarefa atual.

        Retorna todas as entradas gravadas após o último reset de tarefa
        (`self._metrics_start_line`), isto é, aquelas produzidas durante a
        execução corrente de `run()`. Linhas malformadas (JSON inválido) são
        ignoradas silenciosamente, garantindo leitura robusta mesmo diante
        de gravações concorrentes ou truncadas.

        Retorna lista vazia se o arquivo não existir.
        """
        if not os.path.exists(AGENT_METRICS_FILE):
            return []

        entries: List[Dict[str, Any]] = []
        try:
            with open(AGENT_METRICS_FILE, "r", encoding="utf-8") as f:
                for index, line in enumerate(f):
                    if index < self._metrics_start_line:
                        continue
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        parsed = json.loads(line)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    if isinstance(parsed, dict):
                        entries.append(parsed)
        except OSError as e:
            logger.warning(f"Falha ao ler métricas da tarefa: {e}")
            return []

        return entries

    def _generate_task_report(self, final_answer: str) -> None:
        """Gera e persiste o Relatório da Tarefa ao final da execução.

        Uma falha na geração/gravação do relatório nunca deve impedir a
        conclusão da tarefa: todo o processo é protegido por try/except e
        eventuais erros apenas são registrados em log.
        """
        try:
            task_report_cfg = (self.session.config or {}).get("task_report", {}) or {}
            if not task_report_cfg.get("enabled", True):
                return

            report_builder = TaskReportBuilder(self.session.config)
            report = report_builder.build_report(
                self.agent_state,
                self._get_metrics_for_task(),
                final_answer,
            )
            report_path = report_builder.save_report(
                report,
                format=task_report_cfg.get("format", "json"),
            )
            if self.verbose:
                print(f"🗒️  Relatório da tarefa salvo em: {report_path}")
        except Exception as e:
            logger.warning(f"Falha ao gerar relatório da tarefa: {e}")

    def _is_task_solved(self) -> bool:
        if not self.agent_state.tool_history:
            return True
        r = self.agent_state.last_result
        if not isinstance(r, dict):
            return False
        return r.get("ok") is True and r.get("done") is True

    def _sanitize_error(self, error_message: str) -> str:
        return ErrorHandler.sanitize_error(error_message)

    def _handle_step_failure(self, step_index: int, reason: str,
                             tool: str = "", args: dict = None) -> str:
        return ErrorHandler.handle_step_failure(
            step_index,
            reason,
            tool,
            args,
            emit_callback=self._emit,
            verbose=self.verbose,
        )

    def _purge_stale_context(self) -> None:
        ErrorHandler.purge_stale_context(self.session, self.verbose)

    def fail_task(self) -> None:
        """Marca a tarefa atual como falhada, disparando rollback ao final do run().

        Método público para que subcomponentes (PlanExecutor, ReactiveLoop, etc.)
        não precisem acessar diretamente o atributo privado _task_failed.
        """
        self._task_failed = True

    def _summarize_text(self, text: str, context: str = "") -> str:
        return self.tool_executor.summarize_text(text, context)

    def _maybe_summarize_and_store(self, tool_name: str, args: Dict[str, Any], result: Dict[str, Any]) -> None:
        self.tool_executor.maybe_summarize_and_store(tool_name, args, result)

    def _test_and_correct(self, file_path: str, objective: str) -> bool:
        return self.auto_coder.test_and_correct(file_path, objective)

    def _generate_content(self, tool: str, args: dict, objective: str) -> Optional[str]:
        return self.auto_coder.generate_content(tool, args, objective)

    def _run_reactive(self, objective: str, tool_usage_count: Dict[str, int], original_msg_count: int) -> str:
        return self.reactive_loop.run_reactive(objective, tool_usage_count, original_msg_count)

    def _run_tool(self, tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        return self.tool_executor.run_tool(tool_name, args)

    def _reset_task_state(self, objective: str) -> None:
        self.agent_state.objective = objective
        self.agent_state.plan = []
        self.agent_state.plan_step = 0
        self.agent_state.last_result = None
        self.agent_state.last_tool = None
        self.agent_state.last_args = None
        self.agent_state.tool_history = []
        self.agent_state.events.clear()
        self.context_manager._cached_project_context = None
        self.workspace.restore_points.clear()
        self._task_failed = False

    def _route_persona(self, objective: str) -> None:
        if self.verbose:
            print("🧭 Consultando roteador de persona...", end="", flush=True)
        persona_prompt, allowed_skills = route_objective(objective, self.session)
        if self.verbose:
            print(f" ✓ ({len(allowed_skills)} skills permitidas)")

        self.current_persona_prompt = persona_prompt
        self.active_skills = allowed_skills
        self._cached_base_prompt = self.context_manager.build_base_system_prompt(
            getattr(self, "current_persona_prompt", ""),
            self._build_tools_description(compact=False),
        )

    def _answer_trivial(self, objective: str) -> str:
        decision = self.context_manager.ask_model(
            objective,
            step_type="final",
            base_prompt=getattr(self, "_cached_base_prompt", None),
            log_metric_callback=self._log_metric,
        )
        answer = decision.get("answer", "Olá! Como posso ajudar?")
        self._emit("final", {"answer": answer[:100]})
        self.agent_state.conversation_history.append({"user": objective, "agent": answer})
        return answer

    def _get_valid_tool_names(self) -> List[str]:
        """Retorna os nomes de todas as ferramentas (skills) registradas.

        Usado para validar `estimated_tools` ao gerar um `MacroPlan`
        hierárquico, evitando que o planejador aceite ferramentas
        inexistentes sugeridas pelo modelo.
        """
        return list(self.skills.keys())

    def _replan_blocked_steps(
        self, plan: List[Dict[str, Any]], objective: str, blocked_steps: List[Any]
    ) -> Optional[List[Dict[str, Any]]]:
        """Aciona o Replanner (agent.replan) para cada passo bloqueado pelo
        PlanValidator, substituindo-o pelos passos que o Replanner sugerir.

        O Replanner já reaplica PlanValidator + PlanOptimizer aos passos que
        ele mesmo propõe (ver `agent/replan.py`), então os passos retornados
        aqui já chegam diagnosticados e otimizados.

        Se o Replanner não conseguir resolver um passo bloqueado, esse passo
        é removido do plano (não é seguro executá-lo como está) e um aviso é
        registrado em log. Retorna `None` se, ao final, o plano ficar vazio.
        """
        plan = list(plan)
        # Substitui de trás para frente para não invalidar os índices dos
        # demais passos bloqueados que ainda serão processados.
        for blocked in sorted(blocked_steps, key=lambda b: b.index, reverse=True):
            idx = blocked.index
            if idx >= len(plan):
                continue
            step = plan[idx]
            step = step if isinstance(step, dict) else {"tool": "", "args": {}}

            ctx = ReplanContext(
                task=objective,
                current_step=step,
                tool_history=self.agent_state.tool_history,
                last_exception=blocked.reason,
            )
            action = replan(ctx, blocked.reason, self)

            if action and action.steps:
                plan[idx:idx + 1] = action.steps
                logger.info(
                    f"[VALIDATOR] Passo {idx + 1} bloqueado ('{blocked.reason}') "
                    f"substituído por {len(action.steps)} passo(s) do replanner."
                )
            else:
                logger.warning(
                    f"[VALIDATOR] Passo {idx + 1} bloqueado ('{blocked.reason}') não pôde ser "
                    f"resolvido pelo replanner e foi removido do plano."
                )
                del plan[idx]

        if not plan:
            return None
        return plan

    def _validate_and_optimize_plan(
        self, plan: List[Dict[str, Any]], objective: str
    ) -> Optional[List[Dict[str, Any]]]:
        """Executa o pipeline de diagnóstico e otimização sobre um plano
        recém-gerado pelo PlanBuilder:

            PlanValidator (diagnóstico) -> PlanOptimizer (transformações
            seguras) -> PlanValidator (checagem pós-otimização)

        Passos bloqueados em qualquer uma das duas validações são
        encaminhados ao Replanner via `_replan_blocked_steps`. Retorna o
        plano final pronto para execução, ou `None` se a tarefa deve ser
        abortada (plano estruturalmente inválido, ou impossível de
        recuperar via replanejamento).
        """
        validator = PlanValidator(self.skills, self.active_skills)

        report = validator.validate(plan)
        for w in report.warnings:
            logger.info(f"[VALIDATOR] {w}")
        for e in report.errors:
            logger.warning(f"[VALIDATOR] {e}")

        if not report.is_valid:
            logger.warning(f"[VALIDATOR] Plano inválido, abortando tarefa: {report.errors}")
            self._emit("hard_block", {"reason": "plano inválido", "errors": report.errors})
            self.fail_task()
            return None

        if report.blocked_steps:
            for b in report.blocked_steps:
                logger.warning(f"[VALIDATOR] Passo {b.index + 1} bloqueado: {b.reason}")
            plan = self._replan_blocked_steps(plan, objective, report.blocked_steps)
            if plan is None:
                self._emit("hard_block", {"reason": "replanejamento de passos bloqueados falhou"})
                self.fail_task()
                return None

        optimizer = PlanOptimizer(TOOL_METADATA)
        opt_report = optimizer.optimize(plan)
        if opt_report.changed:
            logger.info(
                f"[OPTIMIZER] custo {opt_report.cost_before} → {opt_report.cost_after}, "
                f"{len(opt_report.transformations)} otimização(ões), "
                f"{opt_report.removed_duplicates} duplicata(s) removida(s)."
            )
            if self.verbose:
                for t in opt_report.transformations:
                    print(f"[DEBUG][OPTIMIZER] {t}")
        optimized_plan = opt_report.optimized_steps

        post_report = validator.validate(optimized_plan)
        for w in post_report.warnings:
            logger.info(f"[VALIDATOR] (pós-otimização) {w}")
        for e in post_report.errors:
            logger.warning(f"[VALIDATOR] (pós-otimização) {e}")

        if not post_report.is_valid:
            logger.warning(f"[VALIDATOR] Plano inválido após otimização, abortando tarefa: {post_report.errors}")
            self._emit("hard_block", {"reason": "plano inválido pós-otimização", "errors": post_report.errors})
            self.fail_task()
            return None

        if post_report.blocked_steps:
            for b in post_report.blocked_steps:
                logger.warning(f"[VALIDATOR] (pós-otimização) Passo {b.index + 1} bloqueado: {b.reason}")
            optimized_plan = self._replan_blocked_steps(optimized_plan, objective, post_report.blocked_steps)
            if optimized_plan is None:
                self._emit("hard_block", {"reason": "replanejamento pós-otimização falhou"})
                self.fail_task()
                return None

        return optimized_plan

    def _run_hierarchical(self, objective: str, on_chunk=None) -> Optional[str]:
        """Tenta resolver `objective` via planejamento hierárquico.

        Gera um `MacroPlan` (decomposição em sub-objetivos), executa cada
        sub-objetivo isoladamente através do `HierarchicalExecutor` e
        consolida os resultados em uma única resposta final.

        Retorna a resposta final consolidada em caso de sucesso, ou `None`
        se o `MacroPlan` não puder ser gerado — nesse caso o chamador
        (`run`) deve prosseguir com o fluxo linear normal como fallback.
        """
        valid_tools = self._get_valid_tool_names()

        def _ask_model(prompt: str, step_type: str) -> Dict[str, Any]:
            return self.context_manager.ask_model(
                prompt,
                step_type=step_type,
                base_prompt=getattr(self, "_cached_base_prompt", None),
                log_metric_callback=self._log_metric,
            )

        planner = HierarchicalPlanner(ask_model=_ask_model, valid_tools=valid_tools)

        try:
            macro_plan = planner.build_plan(objective)
        except Exception as e:
            logger.warning(f"Falha ao gerar MacroPlan, usando fallback linear: {e}")
            self._emit("hierarchical_fallback", {"reason": str(e)})
            return None

        if not macro_plan or not macro_plan.steps:
            if self.verbose:
                print("[DEBUG] MacroPlan não gerado ou vazio, usando fallback linear.")
            self._emit("hierarchical_fallback", {"reason": "macro_plan vazio ou não gerado"})
            return None

        planning_metadata = {
            "model": (
                getattr(self.context_manager, "model_name", None)
                or getattr(self.context_manager, "model", None)
                or "desconhecido"
            ),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "prompt": objective,
        }

        tracker = TaskTracker()
        tracker.start(objective, macro_plan.steps, planning_metadata)

        summarizer = IncrementalSummarizer(summarize_fn=self._summarize_text)

        executor = HierarchicalExecutor(
            plan_builder=self.plan_builder,
            plan_executor=self.plan_executor,
            final_responder=self.final_responder,
            context_manager=self.context_manager,
            session=self.session,
            tracker=tracker,
            summarizer=summarizer,
        )

        tool_usage_count: Dict[str, int] = {}
        self._emit("hierarchical_started", {"steps": len(macro_plan.steps)})
        final_answer = executor.execute(macro_plan, self.agent_state, tool_usage_count, on_chunk=on_chunk)
        self._emit("hierarchical_completed", {"steps": len(macro_plan.steps)})
        return final_answer

    def run(self, objective: Optional[str] = None, stream_callback=None) -> str:
        original_msg_count = len(self.session.messages)
        tool_usage_count: Dict[str, int] = {}
        resumed = False
        self._cancelled = False

        try:
            try:
                if not objective:
                    # Nenhum objetivo novo foi informado: tenta retomar uma
                    # tarefa interrompida a partir do checkpoint em disco.
                    checkpoint_data = self._load_checkpoint()
                    if checkpoint_data:
                        self.agent_state.from_checkpoint_dict(checkpoint_data)
                        objective = self.agent_state.objective
                        if objective:
                            resumed = True
                            print(f"\n♻️  Checkpoint encontrado. Retomando tarefa interrompida: \"{objective}\"")
                            logger.info(f"Retomando tarefa a partir de checkpoint: {objective}")
                        else:
                            # Checkpoint sem objetivo utilizável: descarta.
                            self._delete_checkpoint()

                    if not objective:
                        return "Nenhum objetivo foi fornecido e nenhum checkpoint válido foi encontrado."

                if resumed:
                    self._task_failed = False
                else:
                    # Um novo objetivo foi fornecido: qualquer checkpoint antigo
                    # é ignorado e será substituído pelo progresso desta tarefa.
                    self._reset_task_state(objective)

                self._task_start_time = Watchdog.start_task()
                # Marca d'água das métricas: apenas entradas gravadas a
                # partir daqui pertencem ao Relatório da Tarefa atual.
                self._metrics_start_line = self._count_metrics_lines()
                print(f"\n🤖 Analisando: \"{objective}\"")
                logger.info(f"Iniciando objetivo do agente: {objective}")

                if not resumed and _is_clearly_trivial(objective):
                    return self._answer_trivial(objective)

                # ---- Plano e roteamento ----
                hierarchical_answer: Optional[str] = None
                if resumed and self.agent_state.plan:
                    # Retomada com plano já existente: não roteia nem gera plano.
                    plan = self.agent_state.plan
                    # Habilita todas as skills para não restringir o plano salvo.
                    self.active_skills = list(self.skills.keys())
                    # Reconstrói o prompt base para manter a formatação.
                    self._cached_base_prompt = self.context_manager.build_base_system_prompt(
                        getattr(self, "current_persona_prompt", ""),
                        self._build_tools_description(compact=False),
                    )
                    if self.verbose:
                        print(f"[DEBUG] Retomando com plano existente ({len(plan)} passos): {plan}")
                else:
                    self._route_persona(objective)
                    self._save_checkpoint()

                    # Objetivos complexos (muitos componentes, análise ampla)
                    # são delegados ao planejamento hierárquico. Se este não
                    # conseguir gerar um MacroPlan utilizável, o fluxo segue
                    # normalmente pelo planejamento linear (fallback).
                    if is_hierarchical(objective):
                        hierarchical_answer = self._run_hierarchical(objective, on_chunk=stream_callback)
                        if hierarchical_answer is None and self.verbose:
                            print("[DEBUG] Planejamento hierárquico indisponível, seguindo fluxo linear.")

                    if hierarchical_answer is None:
                        plan, blocked_answer = self.plan_builder.build_plan(objective)
                        if blocked_answer:
                            self.agent_state.conversation_history.append(
                                {"user": objective, "agent": blocked_answer}
                            )
                            return blocked_answer
                        if not plan:
                            if self.verbose:
                                print("[DEBUG] Plano não gerado ou inválido, usando modo reativo.")
                            return self._run_reactive(objective, tool_usage_count, original_msg_count)

                        # ---- Pipeline de validação e otimização do plano ----
                        # PlanValidator (diagnóstico) -> PlanOptimizer (seguro)
                        # -> PlanValidator (pós-otimização). Passos bloqueados
                        # acionam o Replanner; um plano estruturalmente
                        # inválido aborta a tarefa.
                        plan = self._validate_and_optimize_plan(plan, objective)
                        if plan is None:
                            abort_msg = (
                                "Não foi possível validar um plano seguro para esta tarefa. "
                                "A execução foi interrompida."
                            )
                            self.agent_state.conversation_history.append(
                                {"user": objective, "agent": abort_msg}
                            )
                            return abort_msg

                        self.agent_state.plan = plan
                        self._save_checkpoint()

                # ---- Execução do plano ----
                if hierarchical_answer is not None:
                    final_answer = hierarchical_answer
                else:
                    execution_answer = self.plan_executor.execute(objective, tool_usage_count)
                    if execution_answer:
                        final_answer = execution_answer
                    else:
                        final_answer = self.final_responder.build_final_answer(objective, on_chunk=stream_callback)

                # Após obter a resposta final (e antes do `finally`), gera o
                # Relatório da Tarefa. Falhas aqui nunca devem impedir a
                # conclusão da tarefa (ver try/except em _generate_task_report).
                self._generate_task_report(final_answer)

                return final_answer

            except KeyboardInterrupt:
                # Cancelamento cooperativo: interrompe a execução de forma
                # limpa, preservando o progresso via checkpoint em disco
                # para que a tarefa possa ser retomada posteriormente.
                self._cancelled = True
                self._save_checkpoint()
                return "Tarefa cancelada pelo usuário. O progresso foi salvo e pode ser retomado posteriormente."

        finally:
            if self._task_failed:
                self.workspace.rollback()

            while len(self.session.messages) > original_msg_count:
                self.session.messages.pop()
            if len(self.agent_state.conversation_history) > self.agent_state.max_history_turns:
                self.agent_state.conversation_history = self.agent_state.conversation_history[-self.agent_state.max_history_turns:]
            self.context_manager.maybe_compress_context()
            self.save_memory_to_file("agent_memory.json")
            # Tarefa concluída (com sucesso ou falha): o checkpoint deixa de
            # ser necessário e é removido. Se a tarefa foi cancelada pelo
            # usuário (Ctrl+C), o checkpoint é preservado para permitir a
            # retomada posterior.
            if not getattr(self, "_cancelled", False):
                self._delete_checkpoint()