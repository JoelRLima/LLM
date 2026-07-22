from __future__ import annotations

from collections.abc import Callable
from typing import Any, Dict, List, Optional

from agent.auto_coder import AutoCoder
from agent.cancellation import CancellationToken
from agent.checkpoint_manager import CheckpointManager
from agent.final_response import FinalResponder
from agent.llm.context_manager import ContextManager
from agent.llm.router import is_security_objective, route_objective
from agent.llm.session import ChatSession
from agent.orchestration.hierarchical_service import HierarchicalExecutionService
from agent.orchestration.operations import OrchestratorOperations
from agent.orchestration.security_service import SecurityAnalysisService
from agent.orchestration.subsystems import AgentSubsystems
from agent.orchestration.task_runner import TaskRunner
from agent.planning.execution_gateway import ExecutionGateway
from agent.planning.plan_builder import PlanBuilder
from agent.planning.plan_executor import PlanExecutor
from agent.planning.reactive_loop import ReactiveLoop
from agent.reporting.metrics_recorder import MetricsRecorder
from agent.runtime import paths
from agent.state import AgentState
from agent.tool_executor import ToolExecutor
from agent.watchdog import Watchdog
from agent.workspace import WorkspaceManager

AGENT_METRICS_FILE = paths.METRICS_FILE


class Orchestrator(OrchestratorOperations):
    """Public composition facade for the agent runtime."""

    def __init__(
        self,
        session: ChatSession,
        skills: Optional[List[Any]] = None,
        verbose: bool = False,
        checkpoint_file: str = paths.CHECKPOINT_FILE,
    ) -> None:
        self.session = session
        self.skills: Dict[str, Any] = {}
        self.max_steps = 15
        self.max_total_actions = 20
        self.max_early_final_attempts = 3
        self.max_loop_repetitions = 3
        self.verbose = verbose
        self.active_skills: List[str] = []
        self._task_failed = False
        self._cancelled = False
        self._task_start_time = 0.0
        self._metrics_start_line = 0
        self.cancellation_token = CancellationToken()
        self.checkpoint_file = checkpoint_file
        self.checkpoint_manager = CheckpointManager(checkpoint_file)
        self.metrics_recorder = MetricsRecorder(AGENT_METRICS_FILE)
        self.agent_state = AgentState()
        self.subsystems = AgentSubsystems(self)
        for skill in skills or []:
            self.register_skill(skill)

    @property
    def workspace(self) -> WorkspaceManager:
        return self.subsystems.workspace

    @property
    def context_manager(self) -> ContextManager:
        return self.subsystems.context_manager

    @property
    def auto_coder(self) -> AutoCoder:
        return self.subsystems.auto_coder

    @property
    def reactive_loop(self) -> ReactiveLoop:
        return self.subsystems.reactive_loop

    @property
    def plan_builder(self) -> PlanBuilder:
        return self.subsystems.plan_builder

    @property
    def plan_executor(self) -> PlanExecutor:
        return self.subsystems.plan_executor

    @property
    def final_responder(self) -> FinalResponder:
        return self.subsystems.final_responder

    @property
    def tool_executor(self) -> ToolExecutor:
        return self.subsystems.tool_executor

    @property
    def watchdog(self) -> Watchdog:
        return self.subsystems.watchdog

    @property
    def execution_gateway(self) -> ExecutionGateway:
        return self.subsystems.execution_gateway

    def _reset_task_state(self, objective: str) -> None:
        self.agent_state.objective = objective
        self.agent_state.reset_execution()
        self.agent_state.last_result = None
        self.agent_state.last_tool = None
        self.agent_state.last_args = None
        self.agent_state.tool_history = []
        self.agent_state.events.clear()
        self.context_manager._cached_project_context = None
        self.workspace.restore_points.clear()
        self._task_failed = False
        self.cancellation_token.reset()

    def _route_persona(self, objective: str) -> None:
        if self.verbose:
            print("Consultando roteador de persona...", end="", flush=True)
        persona_prompt, allowed_skills = route_objective(objective, self.session)
        self.current_persona_prompt = persona_prompt
        self.active_skills = allowed_skills
        self._cached_base_prompt = self.context_manager.build_base_system_prompt(
            persona_prompt,
            self._build_tools_description(compact=False),
        )
        if self.verbose:
            print(f" concluído ({len(allowed_skills)} skills permitidas)")

    def _answer_trivial(self, objective: str) -> str:
        normalized = objective.strip().lower().rstrip("!?.")
        greetings = {"oi", "olá", "ola", "oie", "oii", "hey", "hello"}
        wellbeing = ("como vai", "tudo bem", "tudo bom", "td bem", "td bom")
        identity = ("quem é você", "o que você faz", "o que vc faz", "qual o seu nome", "qual seu nome")
        if normalized in greetings:
            answer = "Olá! Como posso ajudar você hoje?"
        elif any(term in normalized for term in wellbeing):
            answer = "Estou bem, obrigado! Como posso ajudar você hoje?"
        elif any(term in normalized for term in identity):
            answer = "Eu sou um agente de desenvolvimento assistido por IA. Posso analisar arquivos, escrever código e responder dúvidas técnicas."
        else:
            answer = "Olá! Como posso ajudar você hoje?"
        self._emit("final", {"answer": answer[:100]})
        self.agent_state.conversation_history.append({"user": objective, "agent": answer})
        return answer

    def _get_valid_tool_names(self) -> List[str]:
        return list(self.skills)

    @staticmethod
    def _is_security_objective(objective: str) -> bool:
        return bool(is_security_objective(objective))

    def _handle_security_analysis(
        self, objective: str, stream_callback: Callable[[str], None] | None = None
    ) -> Optional[str]:
        return SecurityAnalysisService(self).run(objective, stream_callback)

    def _run_hierarchical(
        self, objective: str, on_chunk: Callable[[str], None] | None = None
    ) -> Optional[str]:
        return HierarchicalExecutionService(self).run(objective, on_chunk)

    def run(
        self,
        objective: Optional[str] = None,
        stream_callback: Callable[[str], None] | None = None,
    ) -> str:
        return TaskRunner(self).run(objective, stream_callback)
