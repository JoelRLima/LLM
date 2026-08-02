"""Individual side-effect-free checks used by standalone doctor."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from agent import __version__
from agent.health.contracts import (
    STATUS_ERROR,
    STATUS_OK,
    STATUS_WARNING,
    CheckResult,
)
from agent.health.state_integrity import check_persistent_state
from agent.runtime.config_errors import ConfigError
from agent.runtime.config_repository import ConfigRepository
from agent.runtime.paths import AppPaths
from agent.runtime.workspace_context import WorkspaceContext


def check_python() -> CheckResult:
    version = ".".join(str(part) for part in sys.version_info[:3])
    supported = sys.version_info[:2] >= (3, 10)
    return CheckResult(
        "Versão do Python",
        STATUS_OK if supported else STATUS_ERROR,
        f"Python {version} {'compatível' if supported else 'não suportado'}; mínimo 3.10.",
        {"version": version, "minimum": "3.10"},
    )


def check_package() -> CheckResult:
    valid = bool(__version__.strip())
    return CheckResult(
        "Versão do pacote",
        STATUS_OK if valid else STATUS_ERROR,
        f"local-llm-agent {__version__}" if valid else "Versão do pacote indisponível.",
        {"package": "local-llm-agent", "version": __version__},
    )


def _nearest_existing(path: Path) -> Path:
    candidate = path
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate


def _path_is_in_site_packages(path: Path) -> bool:
    return any(
        part.casefold() in {"site-packages", "dist-packages"}
        for part in path.parts
    )


def check_paths(app_paths: AppPaths) -> CheckResult:
    resolved = {
        "config_dir": app_paths.config_dir.resolve(),
        "data_dir": app_paths.data_dir.resolve(),
        "state_dir": app_paths.state_dir.resolve(),
        "cache_dir": app_paths.cache_dir.resolve(),
        "log_dir": app_paths.log_dir.resolve(),
    }
    details: dict[str, Any] = {}
    problems: list[str] = []
    for name, path in resolved.items():
        existing = _nearest_existing(path)
        absolute = path.is_absolute()
        writable = existing.is_dir() and os.access(existing, os.W_OK | os.X_OK)
        in_site_packages = _path_is_in_site_packages(path)
        details[name] = {
            "path": str(path),
            "exists": path.exists(),
            "absolute": absolute,
            "writable_parent": writable,
            "in_site_packages": in_site_packages,
        }
        if path.exists() and not path.is_dir():
            problems.append(f"{name} não é diretório")
        if not absolute:
            problems.append(f"{name} não é absoluto")
        if not writable:
            problems.append(f"{name} não possui ancestral gravável")
        if in_site_packages:
            problems.append(f"{name} aponta para site-packages")
    return CheckResult(
        "Paths da aplicação",
        STATUS_ERROR if problems else STATUS_OK,
        "; ".join(problems) if problems else "Paths absolutos, externos ao pacote e graváveis.",
        details,
    )


def check_workspace(
    workspace: WorkspaceContext | str | Path,
) -> tuple[CheckResult, WorkspaceContext | None]:
    try:
        context = (
            workspace
            if isinstance(workspace, WorkspaceContext)
            else WorkspaceContext.create(workspace)
        )
    except (FileNotFoundError, NotADirectoryError, OSError) as exc:
        return (
            CheckResult(
                "Workspace",
                STATUS_ERROR,
                str(exc),
                {"path": str(getattr(workspace, "root", workspace))},
            ),
            None,
        )
    readable = os.access(context.root, os.R_OK | os.X_OK)
    writable = os.access(context.root, os.W_OK | os.X_OK)
    status = STATUS_OK if writable else STATUS_WARNING
    if not readable:
        status = STATUS_ERROR
    if not readable:
        message = "Workspace não está acessível."
    elif not writable:
        message = "Workspace acessível somente para leitura."
    else:
        message = "Workspace resolvido com leitura e escrita."
    return (
        CheckResult(
            "Workspace",
            status,
            message,
            {
                "path": str(context.root),
                "workspace_id": context.workspace_id,
                "readable": readable,
                "writable": writable,
            },
        ),
        context,
    )


def check_config(
    app_paths: AppPaths,
    config_path: str | Path | None,
    *,
    overrides: Mapping[str, Any] | None = None,
    environment: Mapping[str, str] | None = None,
) -> tuple[CheckResult, dict[str, Any] | None, Path]:
    repository = ConfigRepository(app_paths, config_path=config_path)
    path = repository.path
    try:
        resolved = repository.load(
            overrides=overrides,
            environment=environment,
        )
    except ConfigError as exc:
        return (
            CheckResult(
                "Configuração",
                STATUS_ERROR,
                str(exc),
                {"path": str(path), "exists": path.is_file()},
            ),
            None,
            path,
        )
    values = resolved.to_dict()
    return (
        CheckResult(
            "Configuração",
            STATUS_OK,
            f"Schema v{resolved.schema_version} válido.",
            {
                "path": str(path),
                "exists": True,
                "schema_version": resolved.schema_version,
            },
        ),
        values,
        path,
    )


def check_state(
    app_paths: AppPaths,
    workspace: WorkspaceContext | None,
) -> CheckResult:
    return check_persistent_state(app_paths, workspace)


def check_backend(config: dict[str, Any] | None) -> CheckResult:
    from agent.llm.providers import SUPPORTED_MODEL_PROVIDERS

    if config is None:
        return CheckResult(
            "Perfil e backend",
            STATUS_WARNING,
            "Não avaliado porque a configuração é inválida ou ausente.",
            {"configured": False, "connectivity": "not_checked"},
        )
    profile_name = config.get("default_model_profile")
    profiles = config.get("model_profiles")
    profile = profiles.get(profile_name) if isinstance(profiles, dict) else None
    if not isinstance(profile, dict):
        return CheckResult(
            "Perfil e backend",
            STATUS_ERROR,
            f"Perfil '{profile_name}' não encontrado.",
            {"configured": False, "connectivity": "not_checked"},
        )
    endpoint = profile.get("api_url") or profile.get("base_url") or config.get("api_url")
    parsed = urlparse(str(endpoint or ""))
    valid_endpoint = parsed.scheme in {"http", "https"} and bool(parsed.netloc)
    provider = profile.get("provider")
    provider_supported = provider in SUPPORTED_MODEL_PROVIDERS
    configured = all(
        (
            isinstance(provider, str) and bool(provider.strip()),
            provider_supported,
            isinstance(profile.get("model"), str) and bool(profile["model"].strip()),
            valid_endpoint,
        )
    )
    return CheckResult(
        "Perfil e backend",
        STATUS_OK if configured else STATUS_ERROR,
        (
            "Backend configurado; conectividade não foi testada."
            if configured
            else "Provider, perfil, modelo ou endpoint do backend não é suportado."
        ),
        {
            "configured": configured,
            "profile": profile_name,
            "provider": provider,
            "provider_supported": provider_supported,
            "model": profile.get("model"),
            "endpoint": endpoint,
            "hardware_profile": config.get("hardware_profile"),
            "connectivity": "not_checked",
        },
    )
