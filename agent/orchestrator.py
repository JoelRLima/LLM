import json
import os
import sys
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.auto_coder import AutoCoder
from agent.context_manager import ContextManager
from agent.error_handler import ErrorHandler
from agent.final_response import FinalResponder
from agent.plan_builder import PlanBuilder
from agent.plan_executor import PlanExecutor
from agent.reactive_loop import ReactiveLoop
from agent.router import _is_clearly_trivial, route_objective
from agent.state import AgentState
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
        self.checkpoint_file: str = checkpoint_file

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

    def run(self, objective: Optional[str] = None, stream_callback=None) -> str:
        original_msg_count = len(self.session.messages)
        tool_usage_count: Dict[str, int] = {}
        resumed = False

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
            print(f"\n🤖 Analisando: \"{objective}\"")
            logger.info(f"Iniciando objetivo do agente: {objective}")

            if not resumed and _is_clearly_trivial(objective):
                return self._answer_trivial(objective)

            self._route_persona(objective)
            self._save_checkpoint()

            if resumed and self.agent_state.plan:
                plan = self.agent_state.plan
            else:
                plan, blocked_answer = self.plan_builder.build_plan(objective)
                if blocked_answer:
                    self.agent_state.conversation_history.append({"user": objective, "agent": blocked_answer})
                    return blocked_answer
                if not plan:
                    if self.verbose:
                        print("[DEBUG] Plano não gerado ou inválido, usando modo reativo.")
                    return self._run_reactive(objective, tool_usage_count, original_msg_count)
                self.agent_state.plan = plan
                self._save_checkpoint()

            execution_answer = self.plan_executor.execute(objective, tool_usage_count)
            if execution_answer:
                return execution_answer

            return self.final_responder.build_final_answer(objective, on_chunk=stream_callback)

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
            # ser necessário e é removido.
            self._delete_checkpoint()