"""Administrative commands for the standalone CLI."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, cast

from agent.runtime.paths import AppPaths
from agent.tools.extension_registry import ExtensionRegistry
from agent.tools.stdio_adapter import load_extension_manifest


def run_doctor(
    *,
    app_paths: AppPaths,
    workspace: Path,
    config_path: str | Path | None,
    profile: str | None,
    json_output: bool,
    write_report: bool,
) -> int:
    from agent.health_check import run_health_check

    report = run_health_check(
        write_report=write_report,
        verbose=not json_output,
        app_paths=app_paths,
        workspace=workspace,
        config_path=config_path,
        profile=profile,
    )
    if json_output:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    readiness = report.get("readiness", {})
    return 0 if isinstance(readiness, dict) and readiness.get("offline_ready") is True else 1


def config_repository(
    app_paths: AppPaths,
    config_path: str | Path | None,
) -> Any:
    from agent.runtime.config_repository import ConfigRepository

    return ConfigRepository(app_paths, config_path=config_path)


def run_config(
    args: argparse.Namespace,
    *,
    app_paths: AppPaths,
    config_path: str | Path | None,
    profile: str | None,
) -> int:
    repository = config_repository(app_paths, config_path)
    if args.config_command == "path":
        print(repository.path)
    elif args.config_command == "init":
        print(repository.initialize())
    elif args.config_command == "validate":
        repository.load(
            overrides=(
                {"default_model_profile": profile}
                if profile is not None
                else None
            )
        )
        print(f"Configuração válida: {repository.path}")
    elif args.config_command == "migrate":
        print(repository.migrate(args.source))
    else:  # pragma: no cover - argparse enforces the command set.
        raise ValueError(f"Comando de configuração desconhecido: {args.config_command}")
    return 0


def run_state(
    args: argparse.Namespace,
    *,
    app_paths: AppPaths,
    workspace: Path,
) -> int:
    from agent.runtime.state_migration import migrate_legacy_state
    from agent.runtime.workspace_context import WorkspaceContext

    if args.state_command != "migrate":  # pragma: no cover - argparse enforces it.
        raise ValueError(f"Comando de estado desconhecido: {args.state_command}")
    workspace_context = WorkspaceContext.create(workspace)
    destination = app_paths.for_workspace(workspace_context.workspace_id)
    report = migrate_legacy_state(args.source, destination)
    print(
        f"Migração concluída: {len(report.copied)} copiado(s), "
        f"{len(report.skipped)} preservado(s). Origem mantida em {report.source}."
    )
    return 0


def _registry_path(args: argparse.Namespace, app_paths: AppPaths) -> Path:
    state_path = getattr(args, "state", None)
    if state_path:
        return Path(str(state_path)).expanduser().resolve()
    return cast(Path, app_paths.extensions_dir / "registry.json")


def run_tools(
    args: argparse.Namespace,
    *,
    app_paths: AppPaths,
    workspace: Path,
) -> int:
    del workspace
    registry = ExtensionRegistry(_registry_path(args, app_paths))

    if args.tools_command == "list":
        for entry in registry.list():
            status = "enabled" if entry.enabled else "disabled"
            print(f"{entry.id} [{status}] -> {entry.manifest_path}")
        return 0

    if args.tools_command == "add":
        registry.add(id=args.id, manifest_path=args.manifest, enabled=not args.disabled)
        print(f"Extensão registrada: {args.id}")
        return 0

    if args.tools_command == "enable":
        registry.set_enabled(args.id, True)
        print(f"Extensão habilitada: {args.id}")
        return 0

    if args.tools_command == "disable":
        registry.set_enabled(args.id, False)
        print(f"Extensão desabilitada: {args.id}")
        return 0

    if args.tools_command == "doctor":
        for entry in registry.list():
            manifest_path = entry.manifest_path
            exists = manifest_path.exists()
            if exists:
                manifest = load_extension_manifest(manifest_path)
                print(f"{entry.id}: OK ({manifest.id}@{manifest.version})")
            else:
                print(f"{entry.id}: MISSING MANIFEST")
        return 0

    raise ValueError(f"Comando de ferramentas desconhecido: {args.tools_command}")


__all__ = [
    "config_repository",
    "run_config",
    "run_doctor",
    "run_state",
    "run_tools",
]
