"""UI-independent composition root for the standalone assistant."""

from __future__ import annotations

import io
import threading
import time
from collections.abc import Callable
from contextlib import nullcontext, redirect_stdout
from pathlib import Path
from typing import Any, Iterable, Mapping, cast
from uuid import uuid4

from agent.application_result import AgentRunResult
from agent.approval import ApprovalPort, RequireExplicitApproval
from agent.llm.contracts import LegacyPayloadGateway
from agent.llm.session import ChatSession
from agent.orchestration.operational_modes import ApplicationOperationalModeMixin, OperationalMode
from agent.orchestrator import Orchestrator
from agent.planning.completion_observations import publish_outcome
from agent.planning.task_completion import mark_terminal_blocked
from agent.reporting.run_receipt import (
    canonical_public_status,
    derive_error,
    derive_status,
    finalize_run_result,
    public_exception_message,
)
from agent.runtime.budget import BudgetExhausted
from agent.runtime.config_repository import ConfigRepository
from agent.runtime.instance_lock import InstanceLock
from agent.runtime.logging import setup_logger, teardown_logger
from agent.runtime.paths import AppPaths, WorkspacePaths
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
        self._owns_logging = owns_logging
        self._closed = False
        self._task_attempted = False
    @classmethod
    def create(
        cls,
        *,
        workspace: str | Path,
        paths: AppPaths | None = None,
        config_path: str | Path | None = None,
        profile: str | None = None,
        overrides: Mapping[str, Any] | None = None,
        gateway: LegacyPayloadGateway | None = None,
        approval_policy: ApprovalPort | None = None,
        task_authority: TaskAuthoritySnapshot | None = None,
        task_authority_capabilities: Iterable[str] | None = None,
        operational_mode: OperationalMode | None = None,
        configure_logging: bool = True,
        debug_mode: int = 0,
    ) -> "AgentApplication":
        app_paths = paths or AppPaths.discover()
        workspace_context = WorkspaceContext.create(workspace)
        workspace_paths = app_paths.for_workspace(workspace_context.workspace_id)
        config_overrides = dict(overrides or {})
        if profile is not None:
            config_overrides["default_model_profile"] = profile
        repository = ConfigRepository(app_paths, config_path=config_path)
        config = repository.load_legacy(overrides=config_overrides)
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
                event_emitter=orchestrator._emit,
                state_recorder=lambda name, args, res: orchestrator.agent_state.record_tool_result(
                    name, args, res.to_legacy_dict(include_details=True)
                ),
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
            )
            if operational_mode is not None:
                app.orchestrator.set_operational_mode(operational_mode)
            return app
        except Exception:
            instance_lock.release()
            if logging_acquired:
                teardown_logger()
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
    def run(self, objective: str | None, *, stream_callback: Callable[[str], None] | None = None) -> AgentRunResult:
        with _RUN_LOCK:
            return self._run_locked(objective, stream_callback=stream_callback)

    def _run_locked(self, objective: str | None, *, stream_callback: Callable[[str], None] | None = None) -> AgentRunResult:
        if self._closed:
            raise RuntimeError("A aplicação já foi encerrada.")
        captured = io.StringIO()
        self._task_attempted = True
        vars(self.orchestrator)["_last_failure_code"] = None
        vars(self.orchestrator)["_last_failure_layer"] = None
        vars(self.orchestrator)["_run_id"] = None
        vars(self.orchestrator)["_task_start_time"] = 0.0
        vars(self.orchestrator)["_run_metric_recorded"] = False
        vars(self.orchestrator)["_metrics_start_line"] = None
        try:
            output_context = redirect_stdout(captured) if stream_callback is None else nullcontext()
            callback_args = {} if stream_callback is None else {"stream_callback": stream_callback}
            with output_context:
                answer = str(self.orchestrator.run(objective, **callback_args))
        except KeyboardInterrupt:
            self.cancel()
            return self._result("cancelled", "", error="Tarefa cancelada pelo usuário.")
        except BudgetExhausted as exc:
            vars(self.orchestrator)["_last_failure_code"] = BudgetExhausted.code
            vars(self.orchestrator)["_last_failure_layer"] = "budget"
            message = mark_terminal_blocked(
                self.orchestrator,
                reason_code=BudgetExhausted.code,
                message="A tarefa atingiu o limite de execucao e nao pode prosseguir agora.",
                status="block",
            )
            return self._result("blocked", "", error=message or public_exception_message(exc))
        except Exception as exc:
            vars(self.orchestrator)["_last_failure_code"] = getattr(exc, "code", None)
            vars(self.orchestrator)["_last_failure_layer"] = getattr(exc, "layer", None)
            fail_task = getattr(self.orchestrator, "fail_task", None)
            if callable(fail_task):
                fail_task()
            publish_outcome(self.orchestrator)
            return self._result("failed", "", error=public_exception_message(exc))
        status = derive_status(self.orchestrator)
        metadata: dict[str, Any] = {}
        legacy_output = captured.getvalue().strip()
        if legacy_output:
            metadata["legacy_output"] = legacy_output
        error = derive_error(self.orchestrator, status)
        return self._result(status, answer, error=error, metadata=metadata)
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
        effective_status = canonical_public_status(self.orchestrator, status)
        record_metric = getattr(self.orchestrator, "_record_canonical_run_metric", None)
        if callable(record_metric):
            metrics_recorder = getattr(self.orchestrator, "metrics_recorder", None)
            if metrics_recorder is not None:
                if not getattr(self.orchestrator, "_run_id", None):
                    self.orchestrator._run_id = uuid4().hex
                if not getattr(self.orchestrator, "_task_start_time", 0.0):
                    self.orchestrator._task_start_time = time.monotonic()
                if getattr(self.orchestrator, "_metrics_start_line", None) is None:
                    self.orchestrator._metrics_start_line = self.orchestrator._count_metrics_lines()
            record_metric(effective_status == "succeeded")
        return cast(AgentRunResult, finalize_run_result(
            AgentRunResult, self.workspace.root, self.orchestrator, effective_status, answer,
            error=error, diagnostics=diagnostics, metadata=metadata,
            receipt=receipt, report_path=report_path,
        ))
    def cancel(self) -> None:
        if not self._closed:
            self.orchestrator.cancel_task()
    def close(self) -> None:
        with _RUN_LOCK:
            if self._closed:
                return
            try:
                if not self._task_attempted:
                    self.orchestrator._persist_memory_to_file()
            finally:
                self._instance_lock.release()
                if self._owns_logging:
                    teardown_logger()
                self._closed = True
    def __enter__(self) -> "AgentApplication":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        del exc_type, exc, traceback
        self.close()
__all__ = ["AgentApplication", "AgentRunResult"]
