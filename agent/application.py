"""UI-independent composition root for the standalone assistant."""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, Iterable, Mapping

from agent.application_cleanup import abort_startup
from agent.application_lifecycle import close_application
from agent.application_result import AgentRunResult, finalize_application_result
from agent.approval import ApprovalPort, RequireExplicitApproval
from agent.llm.contracts import ModelGateway
from agent.llm.session import ChatSession
from agent.observability.application_adapter import (
    build_inspection_service,
    finish_observation,
    start_observation_session,
)
from agent.observability.live import ObservationSession
from agent.observability.modes import ObservabilityMode, resolve_observability_mode
from agent.orchestration.operational_modes import ApplicationOperationalModeMixin, OperationalMode
from agent.orchestrator import Orchestrator
from agent.runtime.config_repository import ConfigRepository
from agent.runtime.instance_lock import InstanceLock
from agent.runtime.logging import setup_logger
from agent.runtime.paths import AppPaths, WorkspacePaths
from agent.runtime.task_directives import TaskRunDirective
from agent.runtime.workspace_context import WorkspaceContext
from agent.skills import load_skill_registry
from agent.tools.authority import ApplicationAuthoritySnapshot, TaskAuthoritySnapshot, bind_task_authority
from agent.tools.builtin_adapter import BuiltinToolAdapter
from agent.tools.extension_bootstrap import ApplicationExtensionBootstrap
from agent.tools.invocation_gateway import ToolInvocationGateway
from agent.tools.tool_registry import ToolRegistry

_RUN_LOCK = threading.RLock()
class AgentApplication(ApplicationOperationalModeMixin):
    """Owns one configured assistant runtime and its resources."""
    def __init__(
        self,
        *,
        paths: AppPaths,
        workspace: WorkspaceContext,
        workspace_paths: WorkspacePaths,
        config: dict[str, Any],
        session: ChatSession,
        orchestrator: Orchestrator,
        instance_lock: InstanceLock,
        approval_policy: ApprovalPort,
        owns_logging: bool,
        tool_registry: ToolRegistry,
        tool_invocation_gateway: ToolInvocationGateway,
        bootstrap_diagnostics: tuple[object, ...] = (),
        application_authority: ApplicationAuthoritySnapshot | None = None,
        task_authority: TaskAuthoritySnapshot | None = None,
        observation_session: ObservationSession | None = None,
        observability_mode: ObservabilityMode | str = ObservabilityMode.NORMAL,
    ) -> None:
        self.paths = paths
        self.workspace = workspace
        self.workspace_paths = workspace_paths
        self.config = config
        self.session = session
        self.orchestrator = orchestrator
        self._instance_lock = instance_lock
        self.approval_policy = approval_policy
        self.tool_registry = tool_registry
        self.tool_invocation_gateway = tool_invocation_gateway
        self.bootstrap_diagnostics = tuple(bootstrap_diagnostics)
        self.application_authority = application_authority
        self.task_authority = task_authority
        self.observation_session = observation_session
        self.observability_mode = resolve_observability_mode(observability_mode)
        self._owns_logging = owns_logging
        self._closed = False
        self._task_attempted = False
        self.orchestrator._on_observation_run_started = lambda correlation, resumed=False: start_observation_session(
            self, correlation, resumed=resumed
        )
    @classmethod
    def create(
        cls,
        *,
        workspace: str | Path,
        paths: AppPaths | None = None,
        config_path: str | Path | None = None,
        profile: str | None = None,
        overrides: Mapping[str, Any] | None = None,
        gateway: ModelGateway | None = None,
        approval_policy: ApprovalPort | None = None,
        task_authority: TaskAuthoritySnapshot | None = None,
        task_authority_capabilities: Iterable[str] | None = None,
        operational_mode: OperationalMode | None = None,
        configure_logging: bool = True,
        debug_mode: int = 0,
        observability_mode: ObservabilityMode | str | None = None,
    ) -> "AgentApplication":
        app_paths = paths or AppPaths.discover()
        workspace_context = WorkspaceContext.create(workspace)
        workspace_paths = app_paths.for_workspace(workspace_context.workspace_id)
        config_overrides = dict(overrides or {})
        if profile is not None:
            config_overrides["default_model_profile"] = profile
        repository = ConfigRepository(app_paths, config_path=config_path)
        config = repository.load(overrides=config_overrides).to_dict()
        app_paths.ensure_base_directories()
        workspace_paths.ensure_directories()
        instance_lock = InstanceLock.create(workspace_paths.lock_file)
        instance_lock.acquire()
        logging_acquired = False
        try:
            if configure_logging:
                setup_logger(debug_mode, log_file=app_paths.log_file)
                logging_acquired = True
            cls._apply_workspace_paths(config, workspace_paths)
            selected_approval = approval_policy or RequireExplicitApproval()
            model_gateway = gateway
            session = ChatSession(
                config["default_system_prompt"],
                config,
                gateway=model_gateway,
            )
            skill_registry = load_skill_registry(
                base_dir=workspace_context.root,
                scratch_dir=workspace_paths.scratch_dir,
                model_gateway=session.gateway,
                config=config,
                approval_policy=selected_approval,
            )
            extension_bootstrap = ApplicationExtensionBootstrap(
                app_paths,
                workspace_context.workspace_id,
                workspace_context.root,
            ).build(BuiltinToolAdapter(skill_registry))
            if task_authority is not None and task_authority_capabilities is not None:
                raise ValueError("forneca task_authority ou task_authority_capabilities, nao ambos")
            selected_task_authority = task_authority if task_authority_capabilities is None else bind_task_authority(task_authority_capabilities, extension_bootstrap.authority, policy_source="cli.task_authority")
            tool_registry = extension_bootstrap.registry
            orchestrator = Orchestrator(
                session,
                skill_registry=skill_registry,
                tool_registry=tool_registry,
                verbose=False,
                workspace_root=workspace_context.root,
                workspace_paths=workspace_paths,
                application_authority=extension_bootstrap.authority,
                task_authority=selected_task_authority,
            )
            tool_invocation_gateway = ToolInvocationGateway(
                tool_registry,
                budget_ledger=orchestrator.task_budget,
                application_authority=extension_bootstrap.authority,
                task_authority=selected_task_authority,
                approval_port=selected_approval,
                event_dispatcher=orchestrator.event_dispatcher,
                correlation_provider=lambda: orchestrator.run_correlation,
                event_fields_provider=lambda: {
                    "plan_id": getattr(orchestrator.agent_state, "plan_identity", None),
                    "step_id": getattr(orchestrator.agent_state, "current_step_id", None),
                },
                correlated_state_recorder=lambda name, args, res, correlation: orchestrator.agent_state.record_tool_result(
                    name, args, res, correlation=correlation
                ),
                incident_recorder=orchestrator.agent_state.record_execution_incident,
            )
            orchestrator.tool_registry = tool_registry
            orchestrator.tool_invocation_gateway = tool_invocation_gateway
            orchestrator._restore_memory_from_file()
            for skill in skill_registry.skills():
                if hasattr(skill, "orchestrator"):
                    skill.orchestrator = orchestrator
            app = cls(
                paths=app_paths,
                workspace=workspace_context,
                workspace_paths=workspace_paths,
                config=config,
                session=session,
                orchestrator=orchestrator,
                instance_lock=instance_lock,
                approval_policy=selected_approval,
                owns_logging=logging_acquired,
                tool_registry=tool_registry,
                tool_invocation_gateway=tool_invocation_gateway,
                bootstrap_diagnostics=extension_bootstrap.diagnostics,
                application_authority=extension_bootstrap.authority,
                task_authority=selected_task_authority,
                observability_mode=resolve_observability_mode(observability_mode),
            )
            if operational_mode is not None:
                app.orchestrator.set_operational_mode(operational_mode)
            return app
        except Exception:
            abort_startup(instance_lock, logging_acquired)
            raise
    @staticmethod
    def _apply_workspace_paths(
        config: dict[str, Any],
        workspace_paths: WorkspacePaths,
    ) -> None:
        config["checkpoint_file"] = str(workspace_paths.checkpoint_file)
        raw_report = config.get("task_report")
        report = raw_report if isinstance(raw_report, dict) else {}
        report["output_dir"] = str(workspace_paths.reports_dir)
        config["task_report"] = report

    def inspection_service(self) -> Any:
        """Return the shared read API for the interactive inspector adapter."""
        return build_inspection_service(self)

    def run(
        self,
        objective: str | None,
        *,
        stream_callback: Callable[[str], None] | None = None,
        explicit_resume: bool = False,
        task_run_directive: TaskRunDirective | None = None,
    ) -> AgentRunResult:
        if self._closed:
            raise RuntimeError("A aplicação já foi encerrada.")
        with _RUN_LOCK:
            return self._run_locked(
                objective,
                stream_callback=stream_callback,
                explicit_resume=explicit_resume,
                task_run_directive=task_run_directive,
            )

    def resume(
        self,
        *,
        stream_callback: Callable[[str], None] | None = None,
    ) -> AgentRunResult:
        """Resume the canonical checkpoint through the normal run boundary."""

        return self.run(
            None,
            stream_callback=stream_callback,
            explicit_resume=True,
        )

    def _run_locked(
        self,
        objective: str | None,
        *,
        stream_callback: Callable[[str], None] | None = None,
        explicit_resume: bool = False,
        task_run_directive: TaskRunDirective | None = None,
    ) -> AgentRunResult:
        from agent.application_run import run_locked

        return run_locked(
            self,
            objective,
            stream_callback=stream_callback,
            explicit_resume=explicit_resume,
            task_run_directive=task_run_directive,
        )
    def _result(
        self,
        status: str,
        answer: str,
        *,
        error: str | None = None,
        diagnostics: tuple[dict[str, Any], ...] = (),
        metadata: dict[str, Any] | None = None,
        receipt: dict[str, Any] | None = None,
        report_path: str | None = None,
    ) -> AgentRunResult:
        result = finalize_application_result(
            self,
            status,
            answer,
            error=error,
            diagnostics=diagnostics,
            metadata=metadata,
            receipt=receipt,
            report_path=report_path,
        )
        finish_observation(self, result)
        return result
    def cancel(self) -> None:
        if not self._closed:
            self.orchestrator.cancel_task()
    def close(self) -> None:
        close_application(self, _RUN_LOCK)

    def __enter__(self) -> "AgentApplication": return self
    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None: self.close()
__all__ = ["AgentApplication", "AgentRunResult"]
