"""Orchestration for migrating supported legacy state into one workspace."""

from __future__ import annotations

from llm_agent.agent.runtime.home_lifecycle import HomeLifecycleLease
from llm_agent.agent.runtime.state_migration import (
    StateMigrationError,
    migrate_legacy_state,
)
from llm_agent.agent.runtime.storage_bootstrap import StorageBootstrap
from llm_agent.application.context import WorkspaceContext
from llm_agent.application.state_migration.contracts import (
    StateMigrationRequest,
    StateMigrationResult,
)
from llm_agent.application.state_migration.errors import StateMigrationFailedError


def migrate_state(request: StateMigrationRequest) -> StateMigrationResult:
    """Prepare the selected workspace and migrate supported legacy state."""
    workspace_context = WorkspaceContext.create(request.workspace)
    destination = request.app_paths.for_workspace(workspace_context.workspace_id)
    lease = HomeLifecycleLease.begin_transient(request.app_paths.home_dir)
    try:
        StorageBootstrap().prepare(request.app_paths)
        try:
            report = migrate_legacy_state(request.source, destination)
        except StateMigrationError as exc:
            raise StateMigrationFailedError(str(exc)) from exc
        return StateMigrationResult(
            source=report.source,
            copied=report.copied,
            skipped=report.skipped,
        )
    finally:
        lease.close()
