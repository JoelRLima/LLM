"""First-run recovery for the human CLI boundary."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from agent.interfaces.cli import maintenance
from agent.runtime.config_errors import ConfigError, ConfigNotFound
from agent.runtime.config_repository import ConfigRepository
from agent.runtime.home_lifecycle import HomeLifecycleLease
from agent.runtime.storage_bootstrap import StorageBootstrap


class InteractiveTTYRequiredError(ValueError):
    """The human chat surface cannot consume non-TTY input."""

    reason_code = "INTERACTIVE_TTY_REQUIRED"

    def __init__(self) -> None:
        super().__init__(
            "O chat interativo exige stdin e stdout TTY; use 'llm-agent run' "
            "ou outra superfÃ­cie headless para automaÃ§Ã£o."
        )


def is_interactive_terminal() -> bool:
    return bool(sys.stdin.isatty() and sys.stdout.isatty())


def config_init_command(args: argparse.Namespace) -> str:
    command = "llm-agent config init"
    explicit = getattr(args, "config", None)
    if explicit is not None:
        command += f" --config {Path(explicit).expanduser()}"
    app_home = getattr(args, "home", None)
    if app_home is not None:
        command += f" --home {Path(app_home).expanduser()}"
    return command


def actionable_missing_config(args: argparse.Namespace, error: Exception) -> str:
    return f"{error}\nPara criar a configuração padrão, execute:\n  {config_init_command(args)}"


def _complete_guided_setup(args: argparse.Namespace, repository: Any, console: Any, prompt: Any) -> None:
    resolved = repository.load(environment={})
    document = resolved.to_dict()
    profiles = document.get("model_profiles", {})
    profile_names = tuple(profiles) if isinstance(profiles, dict) else ()
    selected_default = str(document.get("default_model_profile", profile_names[0] if profile_names else ""))
    console.print(f"Profiles disponíveis: {', '.join(profile_names) or '(nenhum)'}")

    def ask(message: str, default: str = "") -> str:
        try:
            value = prompt(message, default=default)
        except TypeError:
            value = prompt(message)
        return (value or "").strip()

    selected = ask(f"Profile [{selected_default}]: ", selected_default) or selected_default
    if selected not in profile_names:
        raise ConfigError(f"Profile desconhecido: {selected}")
    raw_profile = profiles.get(selected, {})
    if not isinstance(raw_profile, dict):
        raise ConfigError(f"Profile inválido: {selected}")
    current_model = str(raw_profile.get("model") or document.get("model") or "default")
    current_endpoint = str(raw_profile.get("base_url") or raw_profile.get("api_url") or document.get("api_url") or "")
    model = ask(f"Modelo [{current_model}]: ", current_model) or current_model
    endpoint = ask(f"Endpoint compatível [{current_endpoint}]: ", current_endpoint) or current_endpoint
    repository.update(
        {
            "default_model_profile": selected,
            "model_profiles": {selected: {"model": model, "base_url": endpoint}},
        }
    )
    repository.load(environment={})
    args._first_run_guided = True


def recover_first_run_config(
    args: argparse.Namespace,
    *,
    console: Any,
    app_paths: Any,
    prompt: Any | None = None,
) -> int:
    repository = maintenance.config_repository(app_paths, None)
    console.print(f"[yellow]Configuração do Agent não encontrada:[/yellow]\n{repository.path}")
    console.print("\nParece ser o primeiro uso neste perfil.")
    try:
        if prompt is None:
            from agent.interfaces.cli.interactive_shell import prompt_from

            answer = prompt_from(console, "Deseja criar a configuração padrão agora? [Y/n] ") or ""
        else:
            answer = prompt("Deseja criar a configuração padrão agora? [Y/n] ") or ""
    except (EOFError, KeyboardInterrupt):
        console.print(f"\nNenhum arquivo foi criado. Execute quando desejar:\n  {config_init_command(args)}")
        return 0
    if answer.strip().casefold() not in {"", "y", "yes", "s", "sim"}:
        console.print(f"Nenhum arquivo foi criado. Execute quando desejar:\n  {config_init_command(args)}")
        return 0
    created = maintenance.initialize_config(app_paths, None)
    console.print(f"Configuração criada em {created}.")
    repository = maintenance.config_repository(app_paths, None)

    # A test/embedder may project an interactive flag while not providing a
    # real TTY. Keep that compatibility path actionable; the supported TTY
    # path below receives the PTK composer and is guided.
    if prompt is None:
        console.print("Para validar: llm-agent config validate")
        console.print("Para diagnóstico: llm-agent doctor")
        console.print("Depois, abra: llm-agent chat")
        return 0

    try:
        lease = HomeLifecycleLease.begin_transient(app_paths.home_dir)
        try:
            StorageBootstrap().prepare(app_paths)
            _complete_guided_setup(args, repository, console, prompt)
        finally:
            lease.close()
        console.print("Configuração guiada validada. Diagnóstico de conectividade é opcional; entrando no chat.")
    except (ConfigError, ConfigNotFound, OSError, ValueError) as exc:
        console.print(f"[red]Configuração guiada não concluída:[/red] {exc}")
        console.print("Use os comandos avançados de config para corrigir e tente novamente.")
        return 0
    return 0


def prepare_chat_workspace(
    args: argparse.Namespace,
    *,
    console: Any,
    app_paths: Any,
    prompt: Any | None = None,
    prompt_path: Any | None = None,
) -> bool:
    """Offer workspace entry only after an existing config is valid."""

    if getattr(args, "workspace", None) is not None:
        return False
    if not is_interactive_terminal():
        from agent.interfaces.cli.workspace_entry import require_task_workspace

        require_task_workspace(args)
        return False
    config_path = getattr(args, "config", None)
    config_file = Path(config_path).expanduser().resolve() if config_path is not None else app_paths.config_file
    if not config_file.is_file():
        return False
    try:
        ConfigRepository(app_paths, config_path=config_path).load()
    except (ConfigError, ConfigNotFound, OSError, ValueError):
        return False
    from agent.interfaces.cli.workspace_entry import choose_workspace, load_last_workspace
    from agent.interfaces.cli.workspace_recents import load_recent_workspaces

    args.workspace = str(
        choose_workspace(
            console=console,
            last_workspace=load_last_workspace(app_paths),
            recent_workspaces=load_recent_workspaces(app_paths),
            prompt=prompt,
            path_prompt=prompt_path,
        )
    )
    return True


__all__ = [
    "actionable_missing_config",
    "config_init_command",
    "InteractiveTTYRequiredError",
    "is_interactive_terminal",
    "recover_first_run_config",
    "prepare_chat_workspace",
]
