from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Dict, List, Optional, cast

from agent.auto_coder import AutoCoder
from agent.cancellation import CancellationToken
from agent.checkpoint_manager import CheckpointManager
from agent.final_response import FinalResponder
from agent.llm.context_manager import ContextManager
from agent.llm.router import is_security_objective, route_objective
from agent.llm.session import ChatSession
from agent.memory.memory import AgentMemory
from agent.orchestration.compatibility import install_compatibility_gateway
from agent.orchestration.hierarchical_service import HierarchicalExecutionService
from agent.orchestration.operational_modes import OperationalModeMixin, refresh_capability_projection
from agent.orchestration.operations import OrchestratorOperations
from agent.orchestration.security_service import SecurityAnalysisService
from agent.orchestration.subsystems import AgentSubsystems
from agent.orchestration.task_runner import TaskRunner
from agent.planning.execution_gateway import ExecutionGateway
from agent.planning.plan_builder import PlanBuilder
from agent.planning.plan_executor import PlanExecutor
from agent.planning.planning_context import PlanningContextSnapshot, build_planning_context
from agent.planning.presentation import PlanningPresentationSnapshot
from agent.planning.reactive_loop import ReactiveLoop
from agent.reporting.metrics_recorder import MetricsRecorder
from agent.runtime import paths
from agent.runtime.budget import TaskBudgetLedger
from agent.runtime.paths import WorkspacePaths
from agent.skills.policy import include_eligible_extensions, persona_allowed_capabilities
from agent.skills.registry import SkillRegistry
from agent.state import AgentState
from agent.tool_executor import ToolExecutor
from agent.tools.authority import (
    ApplicationAuthoritySnapshot,
    OperationalMode,
    TaskAuthoritySnapshot,
)
from agent.tools.invocation_gateway import ToolInvocationGateway
from agent.tools.legacy_invoker import LegacyToolInvoker
from agent.tools.tool_registry import ToolRegistry
from agent.watchdog import Watchdog
from agent.workspace import WorkspaceManager


class Orchestrator(OperationalModeMixin, OrchestratorOperations):
    def __init__(
        self,
        session: ChatSession,
        skills: Optional[List[Any]] = None,
        skill_registry: SkillRegistry | None = None,
        verbose: bool = False,
        checkpoint_file: str | None = None,
        *,
        metrics_file: str | None = None,
        workspace_root: str | Path = ".",
        workspace_paths: WorkspacePaths | None = None,
        tool_registry: ToolRegistry | None = None,
        tool_invocation_gateway: ToolInvocationGateway | None = None,
        application_authority: ApplicationAuthoritySnapshot | None = None,
        task_authority: TaskAuthoritySnapshot | None = None,
    ) -> None:
        self.session = session
        self.task_budget = getattr(session, "budget_ledger", None)
        if not isinstance(self.task_budget, TaskBudgetLedger):
            self.task_budget = TaskBudgetLedger.from_config(session.config)
            session.budget_ledger = self.task_budget
        else:
            session.budget_ledger = self.task_budget
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        self.workspace_paths = workspace_paths
        self.analysis_notes_file = (
            workspace_paths.scratch_dir / "analysis_notes.md"
            if workspace_paths is not None
            else Path("analysis_notes.md")
        )
        self.skills: Dict[str, Any] = {}
        self.tool_registry = tool_registry
        self.tool_invocation_gateway = tool_invocation_gateway
        self.application_authority = application_authority
        self.task_authority = task_authority
        self._operational_mode: OperationalMode | None = None
        self._persona_allowed_capabilities: frozenset[str] | None = None
        self._planning_context: PlanningContextSnapshot | None = None
        self.legacy_tool_invoker = None
        self.max_steps = 15
        self.max_total_actions = 20
        self.max_early_final_attempts = 3
        self.max_loop_repetitions = 3
        self.verbose = verbose
        self.active_skills: List[str] = []
        self.allowed_capabilities: frozenset[str] = frozenset()
        self._task_failed = False
        self._cancelled = False
        self._task_start_time = 0.0
        self._metrics_start_line = 0
        self._run_id: str | None = None
        self._run_metric_recorded = False
        self.cancellation_token = CancellationToken()
        self.checkpoint_file = str(
            checkpoint_file
            or (workspace_paths.checkpoint_file if workspace_paths else paths.CHECKPOINT_FILE)
        )
        selected_metrics = str(
            metrics_file
            or (workspace_paths.metrics_file if workspace_paths else paths.METRICS_FILE)
        )
        self.memory_file = str(
            workspace_paths.memory_file if workspace_paths else paths.MEMORY_FILE
        )
        memory = AgentMemory(
            db_path=workspace_paths.memory_db_file if workspace_paths else None,
            default_file=self.memory_file,
            backup_dir=workspace_paths.memory_backup_dir if workspace_paths else None,
        )
        if workspace_paths is not None:
            memory.initialize()
        self.checkpoint_manager = CheckpointManager(self.checkpoint_file)
        self.metrics_recorder = MetricsRecorder(selected_metrics)
        self.session.set_model_call_callback(self._log_metric)
        self.agent_state = AgentState(memory=memory, budget_ledger=self.task_budget)
        self.subsystems = AgentSubsystems(self)
        selected_skills = list(skill_registry.skills()) if skill_registry is not None else (skills or [])
        for skill in selected_skills:
            self.register_skill(skill)
        if self.tool_registry is None and selected_skills:
            install_compatibility_gateway(
                self,
                selected_skills,
                skill_registry=skill_registry,
            )
        if self.tool_invocation_gateway is None and self.tool_registry is None and not selected_skills:
            self.legacy_tool_invoker = LegacyToolInvoker(self)
        if self.tool_invocation_gateway is not None:
            self.tool_invocation_gateway.set_budget_ledger(self.task_budget)
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
        return cast(ReactiveLoop, self.subsystems.reactive_loop)
    @property
    def plan_builder(self) -> PlanBuilder:
        return cast(PlanBuilder, self.subsystems.plan_builder)
    @property
    def plan_executor(self) -> PlanExecutor:
        return cast(PlanExecutor, self.subsystems.plan_executor)
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
        return cast(ExecutionGateway, self.subsystems.execution_gateway)
    def resolve_user_path(self, file_path: str | Path) -> Path:
        """Resolve a user file through the standalone workspace boundary.
        Direct ``Orchestrator`` consumers without ``WorkspacePaths`` retain the
        legacy relative-path behavior.  The standalone composition always
        supplies ``WorkspacePaths`` and therefore confines the result to the
        injected workspace.
        """
        if self.workspace_paths is None:
            return Path(file_path)
        return cast(Path, self.workspace.resolve_path(file_path))
    def _reset_task_state(self, objective: str) -> None:
        assert isinstance(self.task_budget, TaskBudgetLedger)
        self.task_budget.reset()
        self.agent_state.objective = objective
        self.agent_state.reset_execution()
        self.agent_state.reset_task_progression()
        self.agent_state.last_result = None
        self.agent_state.last_tool = None
        self.agent_state.last_args = None
        self.agent_state.tool_history = []
        self.agent_state.events.clear()
        self.context_manager._cached_project_context = None
        self.workspace.restore_points.clear()
        self._planning_context = None
        self._task_failed = False
        self.cancellation_token.reset()
    def _route_persona(self, objective: str) -> None:
        if self.verbose:
            print("Consultando roteador de persona...", end="", flush=True)
        persona_prompt, _, persona = route_objective(objective, self.session)
        self.current_persona_prompt = persona_prompt
        self.current_persona = persona
        self.agent_state.persona = persona
        self.agent_state.persona_prompt = persona_prompt
        self._persona_allowed_capabilities = persona_allowed_capabilities(persona)
        refresh_capability_projection(self)
        self._create_planning_context()
        self._cached_base_prompt = self.context_manager.build_base_system_prompt(
            persona_prompt,
            self._build_tools_description(compact=False),
        )
        if self.verbose:
            print(f" concluído ({len(self.active_skills)} skills permitidas)")

    def _restore_persona_from_state(self) -> None:
        persona = getattr(self.agent_state, "persona", None)
        persona_prompt = getattr(self.agent_state, "persona_prompt", None)
        if persona is None:
            objective = self.agent_state.objective
            if objective:
                self._route_persona(objective)
            return
        self.current_persona = persona
        self.current_persona_prompt = persona_prompt or ""
        self._persona_allowed_capabilities = persona_allowed_capabilities(persona)
        refresh_capability_projection(self)
        self._create_planning_context()
        self._cached_base_prompt = self.context_manager.build_base_system_prompt(
            self.current_persona_prompt,
            self._build_tools_description(compact=False),
        )
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
        view = self.get_planning_view("linear")
        if view is not None:
            return sorted(view.presented_names)
        return list(self.skills)

    @property
    def planning_context(self) -> PlanningContextSnapshot | None:
        """Contexto semântico criado uma vez para a tarefa atual."""

        return self._planning_context

    def _create_planning_context(self) -> None:
        if self.tool_registry is None or self.application_authority is None:
            self._planning_context = None
            return
        self._planning_context = build_planning_context(
            self.tool_registry,
            self.application_authority,
            self.task_authority,
            self.allowed_capabilities,
        )
        include_eligible_extensions(self.active_skills, self.skills, self._planning_context, self.tool_registry)

    def get_planning_view(self, planner_kind: str) -> PlanningPresentationSnapshot | None:
        """Derive a planner view without rebuilding or consulting runtime sources."""

        if self._planning_context is None:
            return None
        visible = (
            frozenset(self.active_skills)
            if self.active_skills
            else self._planning_context.eligible_names
        )
        visible &= self._planning_context.eligible_names
        return self._planning_context.present(planner_kind, visible)

    @staticmethod
    def _is_security_objective(objective: str) -> bool:
        return bool(is_security_objective(objective))

    def _handle_security_analysis(
        self, objective: str, stream_callback: Callable[[str], None] | None = None
    ) -> Optional[str]:
        return cast(Optional[str], SecurityAnalysisService(self).run(objective, stream_callback))

    def _run_hierarchical(
        self, objective: str, on_chunk: Callable[[str], None] | None = None
    ) -> Optional[str]:
        return cast(Optional[str], HierarchicalExecutionService(self).run(objective, on_chunk))
    def run(
        self,
        objective: Optional[str] = None,
        stream_callback: Callable[[str], None] | None = None,
    ) -> str:
        return cast(str, TaskRunner(self).run(objective, stream_callback))
