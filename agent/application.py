"""UI-independent composition root for the standalone assistant."""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable, Mapping

if TYPE_CHECKING:
    from agent.evaluation.feedback import FeedbackService
    from agent.interaction.service import InteractionService
    from agent.interaction.types import AgentInteractionResult
    from agent.outputs.service import OutputService

from agent.actions import DEFAULT_ACTION_CATALOG, ActionCatalog
from agent.application_cleanup import StartupCleanupError, abort_startup
from agent.application_interactive import InteractiveCancellationMixin
from agent.application_lifecycle import close_application
from agent.application_result import AgentRunResult, finalize_application_result
from agent.application_run import run_locked
from agent.application_services.queries import ReadOnlyWorkspaceQueryService
from agent.application_wiring import (
    apply_workspace_paths,
    prepare_application_environment,
    wire_runtime_orchestration,
)
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
from agent.routing.persona.factory import build_persona_router
from agent.runtime.home_lifecycle import HomeLifecycleLease
from agent.runtime.instance_lock import InstanceLock
from agent.runtime.logging import setup_logger
from agent.runtime.paths import AppPaths, WorkspacePaths
from agent.runtime.storage_bootstrap import StorageBootstrap
from agent.runtime.task_directives import TaskRunDirective
from agent.runtime.workspace_context import WorkspaceContext
from agent.skills import load_skill_registry
from agent.tools.authority import ApplicationAuthoritySnapshot, TaskAuthoritySnapshot
from agent.tools.builtin_adapter import BuiltinToolAdapter
from agent.tools.extension_bootstrap import ApplicationExtensionBootstrap
from agent.tools.invocation_gateway import ToolInvocationGateway
from agent.tools.tool_registry import ToolRegistry
from agent.variants.models import VariantComposition, VariantSeam
from agent.variants.preflight import validate_variant_composition

_RUN_LOCK = threading.RLock()

class AgentApplication(InteractiveCancellationMixin, ApplicationOperationalModeMixin):
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
        home_lease: HomeLifecycleLease,
        variant_composition: VariantComposition,
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
        self._home_lease = home_lease
        self.variant_composition = variant_composition
        self.variant_fingerprint = variant_composition.fingerprint
        self.approval_policy = approval_policy
        self.tool_registry = tool_registry
        self.tool_invocation_gateway = tool_invocation_gateway
        self.bootstrap_diagnostics = tuple(bootstrap_diagnostics)
        self.application_authority = application_authority
        self.task_authority = task_authority
        self.observation_session = observation_session
        self.observability_mode = resolve_observability_mode(observability_mode)
        self._owns_logging = owns_logging
        self._closed = self._task_attempted = False
        self._interaction_service: InteractionService | None = None
        self._workspace_query_service: ReadOnlyWorkspaceQueryService | None = None
        self._feedback_service: FeedbackService | None = None
        self._output_service: OutputService | None = None
        self._action_catalog: ActionCatalog = DEFAULT_ACTION_CATALOG
        self._init_interactive_cancellation()
        self.orchestrator._on_observation_run_started = lambda corr, resumed=False: start_observation_session(
            self, corr, resumed=resumed
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
        variant_composition: VariantComposition | None = None,
    ) -> "AgentApplication":
        app_paths = paths or AppPaths.discover()
        home_lease = HomeLifecycleLease.begin_startup(app_paths.home_dir)
        instance_lock: InstanceLock | None = None
        logging_acquired = False
        try:
            StorageBootstrap().prepare(app_paths)
            home_lease.activate()
            selected_composition = validate_variant_composition(
                variant_composition or VariantComposition.production_current()
            )
            config, workspace_context, workspace_paths, instance_lock = prepare_application_environment(
                app_paths, workspace, config_path, profile, overrides
            )
            assert instance_lock is not None
            if configure_logging:
                setup_logger(debug_mode, log_file=app_paths.log_file)
                logging_acquired = True
            apply_workspace_paths(config, workspace_paths)
            selected_approval = approval_policy or RequireExplicitApproval()
            session = ChatSession(config["default_system_prompt"], config, gateway=gateway)
            persona_router = build_persona_router(
                selected_composition.selection(VariantSeam.PERSONA_ROUTER),
                session=session,
            )
            skill_registry = load_skill_registry(
                base_dir=workspace_context.root,
                scratch_dir=workspace_paths.scratch_dir,
                model_gateway=session.gateway,
                config=config,
                approval_policy=selected_approval,
            )
            extension_bootstrap = ApplicationExtensionBootstrap(
                app_paths, workspace_context.workspace_id, workspace_context.root
            ).build(BuiltinToolAdapter(skill_registry))
            runtime = wire_runtime_orchestration(
                workspace_context=workspace_context,
                workspace_paths=workspace_paths,
                session=session,
                persona_router=persona_router,
                selected_approval=selected_approval,
                task_authority=task_authority,
                task_authority_capabilities=task_authority_capabilities,
                extension_bootstrap=extension_bootstrap,
                skill_registry=skill_registry,
            )
            app = cls(
                paths=app_paths,
                workspace=workspace_context,
                workspace_paths=workspace_paths,
                config=config,
                session=session,
                orchestrator=runtime.orchestrator,
                instance_lock=instance_lock,
                home_lease=home_lease,
                variant_composition=selected_composition,
                approval_policy=selected_approval,
                owns_logging=logging_acquired,
                tool_registry=runtime.tool_registry,
                tool_invocation_gateway=runtime.tool_invocation_gateway,
                bootstrap_diagnostics=runtime.bootstrap_diagnostics,
                application_authority=runtime.application_authority,
                task_authority=runtime.task_authority,
                observability_mode=resolve_observability_mode(observability_mode),
            )
            if operational_mode is not None:
                app.orchestrator.set_operational_mode(operational_mode)
            return app
        except BaseException as original:
            cleanup_failures = abort_startup(instance_lock, logging_acquired, home_lease)
            if cleanup_failures:
                raise StartupCleanupError(original, cleanup_failures) from original
            raise

    def inspection_service(self) -> Any:
        return build_inspection_service(self)

    def workspace_query_service(self) -> ReadOnlyWorkspaceQueryService:
        if self._workspace_query_service is None:
            self._workspace_query_service = ReadOnlyWorkspaceQueryService(self.workspace)
        return self._workspace_query_service

    def feedback_service(self) -> FeedbackService:
        if self._feedback_service is None:
            from agent.evaluation.feedback import FeedbackService
            self._feedback_service = FeedbackService(self.workspace_paths)
        return self._feedback_service

    def output_service(self) -> OutputService:
        if self._output_service is None:
            from agent.outputs.service import OutputService
            self._output_service = OutputService(self.workspace_paths)
        return self._output_service

    def action_catalog(self) -> ActionCatalog:
        return self._action_catalog

    def interaction_service(self) -> InteractionService:
        if self._interaction_service is None:
            from agent.interaction.service import InteractionService

            self._interaction_service = InteractionService(self)
        return self._interaction_service

    def interact(
        self,
        text: str,
        *,
        boundary: str = "natural",
        visible_user_text: str | None = None,
        task_payload: str | None = None,
        stream_callback: Callable[[str], None] | None = None,
    ) -> AgentInteractionResult:
        if self._closed:
            raise RuntimeError("A aplicação já foi encerrada.")
        with _RUN_LOCK:
            return self.interaction_service().interact_locked(
                text,
                boundary=boundary,
                visible_user_text=visible_user_text,
                task_payload=task_payload,
                stream_callback=stream_callback,
            )

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

    def resume(self, *, stream_callback: Callable[[str], None] | None = None) -> AgentRunResult:
        return self.run(None, stream_callback=stream_callback, explicit_resume=True)

    def _run_locked(
        self,
        objective: str | None,
        *,
        stream_callback: Callable[[str], None] | None = None,
        explicit_resume: bool = False,
        task_run_directive: TaskRunDirective | None = None,
    ) -> AgentRunResult:
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
        if not self._closed and not self.interaction_service().cancel_active_model_call():
            self.orchestrator.cancel_task()

    def close(self) -> None:
        close_application(self, _RUN_LOCK)

    def __enter__(self) -> "AgentApplication": return self
    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None: self.close()


__all__ = ["AgentApplication", "AgentRunResult"]
