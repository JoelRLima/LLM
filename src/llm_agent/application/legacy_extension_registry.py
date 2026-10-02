"""Application operations for the legacy ``llm-agent tools`` registry."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterator, Literal, cast

from llm_agent.agent.runtime.home_lifecycle import HomeLifecycleLease
from llm_agent.agent.runtime.storage_bootstrap import StorageBootstrap
from llm_agent.application.context import AppPaths
from llm_agent.extensions.extension_manifest_parser import load_extension_manifest_bytes
from llm_agent.extensions.extension_registry import ExtensionRegistry, ExtensionState

_ManifestStatus = Literal["not_checked", "missing", "ok"]


@dataclass(frozen=True)
class LegacyExtensionRegistryEntry:
    id: str
    manifest_path: Path
    enabled: bool
    manifest_status: _ManifestStatus = "not_checked"
    manifest_id: str | None = None
    manifest_version: str | None = None


def _target(app_paths: AppPaths, state_path: str | Path | None) -> Path:
    if state_path:
        return Path(str(state_path)).expanduser().resolve()
    return cast(Path, app_paths.extensions_registry_file.resolve())


def _entry(state: ExtensionState) -> LegacyExtensionRegistryEntry:
    return LegacyExtensionRegistryEntry(
        id=state.id,
        manifest_path=state.manifest_path,
        enabled=state.enabled,
    )


def _canonical_target(target: Path, app_paths: AppPaths) -> bool:
    try:
        target.relative_to(app_paths.home_dir.resolve())
    except ValueError:
        return False
    return True


def list_legacy_extensions(
    app_paths: AppPaths,
    state_path: str | Path | None = None,
) -> tuple[LegacyExtensionRegistryEntry, ...]:
    registry = ExtensionRegistry(_target(app_paths, state_path))
    return tuple(_entry(state) for state in registry.list())


def add_legacy_extension(
    app_paths: AppPaths,
    extension_id: str,
    manifest_path: str | Path,
    enabled: bool = True,
    state_path: str | Path | None = None,
) -> LegacyExtensionRegistryEntry:
    target = _target(app_paths, state_path)
    if _canonical_target(target, app_paths):
        lease = HomeLifecycleLease.begin_transient(app_paths.home_dir)
        try:
            StorageBootstrap().prepare(app_paths)
            registry = ExtensionRegistry(target)
            return _entry(registry.add(id=extension_id, manifest_path=manifest_path, enabled=enabled))
        finally:
            lease.close()
    registry = ExtensionRegistry(target)
    return _entry(registry.add(id=extension_id, manifest_path=manifest_path, enabled=enabled))


def set_legacy_extension_enabled(
    app_paths: AppPaths,
    extension_id: str,
    enabled: bool,
    state_path: str | Path | None = None,
) -> LegacyExtensionRegistryEntry:
    target = _target(app_paths, state_path)
    if _canonical_target(target, app_paths):
        lease = HomeLifecycleLease.begin_transient(app_paths.home_dir)
        try:
            StorageBootstrap().prepare(app_paths)
            registry = ExtensionRegistry(target)
            return _entry(registry.set_enabled(extension_id, enabled))
        finally:
            lease.close()
    registry = ExtensionRegistry(target)
    return _entry(registry.set_enabled(extension_id, enabled))


def doctor_legacy_extensions(
    app_paths: AppPaths,
    state_path: str | Path | None = None,
) -> Iterator[LegacyExtensionRegistryEntry]:
    registry = ExtensionRegistry(_target(app_paths, state_path))
    entries = registry.list()

    def diagnostics() -> Iterator[LegacyExtensionRegistryEntry]:
        for state in entries:
            if not state.manifest_path.exists():
                yield replace(_entry(state), manifest_status="missing")
                continue
            manifest = load_extension_manifest_bytes(
                state.manifest_path.read_bytes(),
                mode="strict_catalog",
            )
            yield replace(
                _entry(state),
                manifest_status="ok",
                manifest_id=manifest.id,
                manifest_version=manifest.version,
            )

    return diagnostics()


__all__ = [
    "LegacyExtensionRegistryEntry",
    "list_legacy_extensions",
    "add_legacy_extension",
    "set_legacy_extension_enabled",
    "doctor_legacy_extensions",
]
