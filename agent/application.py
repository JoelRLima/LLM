"""UI-independent composition root for the standalone assistant."""

from __future__ import annotations

import io
import threading
from contextlib import redirect_stdout
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from agent.approval import ApprovalPort, RequireExplicitApproval
from agent.llm.contracts import LegacyPayloadGateway
from agent.llm.session import ChatSession
from agent.orchestrator import Orchestrator
from agent.runtime.config_repository import ConfigRepository
from agent.runtime.instance_lock import InstanceLock
from agent.runtime.logging import setup_logger, teardown_logger
from agent.runtime.paths import AppPaths, WorkspacePaths
from agent.runtime.workspace_context import WorkspaceContext
from agent.skills import load_skill_registry
from agent.tools.authority import ApplicationAuthoritySnapshot
from agent.tools.builtin_adapter import BuiltinToolAdapter
from agent.tools.extension_bootstrap import ApplicationExtensionBootstrap
from agent.tools.invocation_gateway import ToolInvocationGateway
from agent.tools.tool_registry import ToolRegistry

_RUN_LOCK = threading.RLock()


@dataclass(frozen=True)
class AgentRunResult:
    """Structured boundary result shared by headless interfaces."""

    status: str
    answer: str
    workspace: str
    error: str | None = None
    diagnostics: tuple[dict[str, Any], ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def success(self) -> bool:
        return self.status == "succeeded"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "success": self.success,
            "answer": self.answer,
            "workspace": self.workspace,
            "error": self.error,
            "diagnostics": list(self.diagnostics),
            "metadata": dict(self.metadata),
        }


class AgentApplication:
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
            tool_registry = extension_bootstrap.registry
            orchestrator = Orchestrator(
                session,
                skill_registry=skill_registry,
                tool_registry=tool_registry,
                verbose=False,
                workspace_root=workspace_context.root,
                workspace_paths=workspace_paths,
            )
            tool_invocation_gateway = ToolInvocationGateway(
                tool_registry,
                approval_port=selected_approval,
                event_emitter=orchestrator._emit,
                state_recorder=lambda name, args, res: orchestrator.agent_state.record_tool_result(
                    name, args, res.to_legacy_dict()
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
            )
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

    def run(self, objective: str) -> AgentRunResult:
        with _RUN_LOCK:
            return self._run_locked(objective)

    def _run_locked(self, objective: str) -> AgentRunResult:
        if self._closed:
            raise RuntimeError("A aplicação já foi encerrada.")
        captured = io.StringIO()
        self._task_attempted = True
        try:
            with redirect_stdout(captured):
                answer = str(self.orchestrator.run(objective))
        except KeyboardInterrupt:
            self.cancel()
            return self._result("cancelled", "", error="Tarefa cancelada pelo usuário.")
        except Exception as exc:
            return self._result("failed", "", error=f"{type(exc).__name__}: {exc}")

        last_result = self.orchestrator.agent_state.last_result or {}
        tool_status = str(last_result.get("status") or "")
        if tool_status in {"blocked", "unverified", "cancelled"}:
            status = tool_status
        elif self.orchestrator._cancelled:
            status = "cancelled"
        elif self.orchestrator._task_failed:
            status = "failed"
        else:
            status = "succeeded"
        metadata: dict[str, Any] = {}
        legacy_output = captured.getvalue().strip()
        if legacy_output:
            metadata["legacy_output"] = legacy_output
        error = (
            str(last_result.get("error"))
            if status != "succeeded" and last_result.get("error")
            else None
        )
        return self._result(status, answer, error=error, metadata=metadata)

    def _result(
        self,
        status: str,
        answer: str,
        *,
        error: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AgentRunResult:
        return AgentRunResult(
            status=status,
            answer=answer,
            workspace=str(self.workspace.root),
            error=error,
            metadata=metadata or {},
        )

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
