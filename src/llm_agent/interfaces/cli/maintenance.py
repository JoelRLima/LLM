"""Administrative commands for the standalone CLI."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import cast

from llm_agent.application.configuration_admin import (
    configuration_path,
    initialize_configuration,
    migrate_configuration,
    validate_configuration,
)
from llm_agent.application.context import AppPaths
from llm_agent.application.legacy_extension_registry import (
    add_legacy_extension,
    doctor_legacy_extensions,
    list_legacy_extensions,
    set_legacy_extension_enabled,
)


def run_doctor(
    *,
    app_paths: AppPaths,
    workspace: Path,
    config_path: str | Path | None,
    profile: str | None,
    json_output: bool,
    write_report: bool,
    online: bool = False,
) -> int:
    from llm_agent.application.health import (
        HealthDiagnosticsRequest,
        run_health_diagnostics,
    )

    result = run_health_diagnostics(
        HealthDiagnosticsRequest(
            app_paths=app_paths,
            workspace=workspace,
            config_path=config_path,
            profile=profile,
            write_report=write_report,
            online=online,
        )
    )
    if json_output:
        print(json.dumps(result.structured_report, ensure_ascii=False, sort_keys=True))
    else:
        print(result.rendered_report)
    if not result.offline_ready:
        return 1
    if online and result.online_ready is not True:
        return 1
    return 0


def initialize_config(
    app_paths: AppPaths,
    config_path: str | Path | None,
) -> Path:
    """Compatibility entry point delegating initialization to Application."""
    return cast(Path, initialize_configuration(app_paths, config_path))


def run_config(
    args: argparse.Namespace,
    *,
    app_paths: AppPaths,
    config_path: str | Path | None,
    profile: str | None,
) -> int:
    if args.config_command == "path":
        print(configuration_path(app_paths, config_path))
    elif args.config_command == "init":
        print(initialize_configuration(app_paths, config_path))
    elif args.config_command == "validate":
        path = validate_configuration(app_paths, config_path, profile)
        print(f"Configura\u00e7\u00e3o v\u00e1lida: {path}")
    elif args.config_command == "migrate":
        print(migrate_configuration(app_paths, args.source, config_path=config_path))
    else:  # pragma: no cover - argparse enforces the command set.
        raise ValueError(f"Comando de configuração desconhecido: {args.config_command}")
    return 0


def run_state(
    args: argparse.Namespace,
    *,
    app_paths: AppPaths,
    workspace: Path,
) -> int:
    from llm_agent.application.state_migration import StateMigrationRequest, migrate_state

    if args.state_command != "migrate":  # pragma: no cover - argparse enforces it.
        raise ValueError(f"Comando de estado desconhecido: {args.state_command}")
    result = migrate_state(
        StateMigrationRequest(
            source=args.source,
            workspace=workspace,
            app_paths=app_paths,
        )
    )
    print(
        f"Migração concluída: {result.copied_count} copiado(s), "
        f"{result.skipped_count} preservado(s). Origem mantida em {result.source}."
    )
    return 0


def run_tools(
    args: argparse.Namespace,
    *,
    app_paths: AppPaths,
    workspace: Path,
) -> int:
    del workspace
    state_path = getattr(args, "state", None)

    if args.tools_command == "list":
        for entry in list_legacy_extensions(app_paths, state_path):
            status = "enabled" if entry.enabled else "disabled"
            print(f"{entry.id} [{status}] -> {entry.manifest_path}")
        return 0

    if args.tools_command == "add":
        add_legacy_extension(
            app_paths,
            extension_id=args.id,
            manifest_path=args.manifest,
            enabled=not args.disabled,
            state_path=state_path,
        )
        print(f"Extensão registrada: {args.id}")
        return 0

    if args.tools_command == "enable":
        set_legacy_extension_enabled(app_paths, args.id, True, state_path)
        print(f"Extensão habilitada: {args.id}")
        return 0

    if args.tools_command == "disable":
        set_legacy_extension_enabled(app_paths, args.id, False, state_path)
        print(f"Extensão desabilitada: {args.id}")
        return 0

    if args.tools_command == "doctor":
        for entry in doctor_legacy_extensions(app_paths, state_path):
            if entry.manifest_status == "ok":
                print(f"{entry.id}: OK ({entry.manifest_id}@{entry.manifest_version})")
            elif entry.manifest_status == "missing":
                print(f"{entry.id}: MISSING MANIFEST")
            else:  # pragma: no cover - Application returns only doctor projections.
                raise RuntimeError("Diagnóstico de extensão sem status de manifesto")
        return 0

    raise ValueError(f"Comando de ferramentas desconhecido: {args.tools_command}")


__all__ = [
    "initialize_config",
    "run_config",
    "run_doctor",
    "run_state",
    "run_tools",
]
