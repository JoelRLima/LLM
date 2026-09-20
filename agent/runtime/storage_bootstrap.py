"""The sole automatic W18 -> W19 bootstrap coordinator."""

from __future__ import annotations

from typing import Mapping

from agent.runtime.filesystem_primitives import inspect_final_path
from agent.runtime.paths import AppPaths
from agent.runtime.storage_contracts import (
    StorageBootstrapResult,
    StorageLayoutStatus,
    read_layout_marker,
    write_layout_marker,
)
from agent.runtime.storage_migration import (
    build_migration_plan,
    has_durable_sources,
    migrate_plan,
)


class StorageBootstrap:
    """Probe and materialize exactly one canonical application home."""

    def probe(
        self,
        paths: AppPaths,
        *,
        environment: Mapping[str, str] | None = None,
    ) -> StorageLayoutStatus:
        marker = paths.storage_layout_file
        inspection = inspect_final_path(marker)
        if inspection.exists:
            read_layout_marker(marker)
            return StorageLayoutStatus.CANONICAL
        plan = build_migration_plan(paths, environment)
        return (
            StorageLayoutStatus.MIGRATION_REQUIRED
            if has_durable_sources(plan)
            else StorageLayoutStatus.FRESH
        )

    def prepare(
        self,
        paths: AppPaths,
        *,
        environment: Mapping[str, str] | None = None,
    ) -> StorageBootstrapResult:
        initial = self.probe(paths, environment=environment)
        if initial is StorageLayoutStatus.CANONICAL:
            return StorageBootstrapResult(initial, StorageLayoutStatus.CANONICAL, False, None)
        if initial is StorageLayoutStatus.FRESH:
            paths.ensure_base_directories()
            write_layout_marker(paths.storage_layout_file)
            return StorageBootstrapResult(initial, StorageLayoutStatus.CANONICAL, False, None)
        plan = build_migration_plan(paths, environment)
        paths.ensure_base_directories()
        receipt = migrate_plan(plan, paths)
        return StorageBootstrapResult(initial, StorageLayoutStatus.CANONICAL, True, receipt)


__all__ = ["StorageBootstrap"]
