from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar, cast

from agent.auto_coder import AutoCoder
from agent.final_response import FinalResponder
from agent.llm.context_manager import ContextManager
from agent.planning.execution_gateway import ExecutionGateway
from agent.planning.plan_builder import PlanBuilder
from agent.planning.plan_executor import PlanExecutor
from agent.planning.reactive_loop import ReactiveLoop
from agent.tool_executor import ToolExecutor
from agent.watchdog import Watchdog
from agent.workspace import WorkspaceManager

T = TypeVar("T")


class AgentSubsystems:
    """Lazily constructs runtime services to keep startup inexpensive."""

    def __init__(self, orchestrator: Any) -> None:
        self.orchestrator = orchestrator
        self._instances: dict[str, object] = {}

    def _get(self, name: str, factory: Callable[[], T]) -> T:
        if name not in self._instances:
            self._instances[name] = factory()
        return cast(T, self._instances[name])

    @property
    def workspace(self) -> WorkspaceManager:
        return self._get("workspace", lambda: WorkspaceManager(verbose=self.orchestrator.verbose))

    @property
    def context_manager(self) -> ContextManager:
        return self._get("context_manager", lambda: ContextManager(
            self.orchestrator.session, self.orchestrator.agent_state, verbose=self.orchestrator.verbose
        ))

    @property
    def auto_coder(self) -> AutoCoder:
        return self._get("auto_coder", lambda: AutoCoder(self.orchestrator))

    @property
    def reactive_loop(self) -> ReactiveLoop:
        return self._get("reactive_loop", lambda: ReactiveLoop(self.orchestrator))

    @property
    def plan_builder(self) -> PlanBuilder:
        return self._get("plan_builder", lambda: PlanBuilder(self.orchestrator))

    @property
    def plan_executor(self) -> PlanExecutor:
        return self._get("plan_executor", lambda: PlanExecutor(self.orchestrator))

    @property
    def final_responder(self) -> FinalResponder:
        return self._get("final_responder", lambda: FinalResponder(self.orchestrator))

    @property
    def tool_executor(self) -> ToolExecutor:
        return self._get("tool_executor", lambda: ToolExecutor(self.orchestrator))

    @property
    def watchdog(self) -> Watchdog:
        return self._get("watchdog", Watchdog)

    @property
    def execution_gateway(self) -> ExecutionGateway:
        return self._get("execution_gateway", lambda: ExecutionGateway(self.orchestrator))
