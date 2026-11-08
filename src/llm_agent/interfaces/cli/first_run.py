"""First-run recovery for the human CLI boundary."""

from __future__ import annotations

import argparse
import sys
from functools import partial
from pathlib import Path
from typing import Any

from llm_agent.application.configuration_admin import (
    configuration_path,
    initialize_configuration,
)
from llm_agent.application.configuration_errors import ConfigurationError
from llm_agent.application.context import AppPaths
from llm_agent.application.first_run_configuration import (
    FirstRunConfigurationView,
    configuration_ready_for_chat_entry,
    read_first_run_configuration,
    update_first_run_configuration,
)
from llm_agent.interfaces.cli.interactive_input import read_prompt


class InteractiveTTYRequiredError(ValueError):
    """The human chat surface cannot consume non-TTY input."""

    reason_code = "INTERACTIVE_TTY_REQUIRED"

    def __init__(self) -> None:
        super().__init__(
            "O chat interativo exige stdin e stdout TTY; use 'llm-agent run' "
            "ou outra superfície headless para automação."
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


def _complete_guided_setup(
    args: argparse.Namespace,
    view: FirstRunConfigurationView,
    app_paths: AppPaths,
    console: Any,
    prompt: Any,
) -> bool:
    profiles = {profile.name: profile for profile in view.profiles}
    profile_names = tuple(profiles)
    selected_default = view.default_profile
    console.print(f"Profiles dispon\u00edveis: {', '.join(profile_names) or '(nenhum)'}")

    selected = read_prompt(prompt, f"Profile [{selected_default}]: ", default=selected_default)
    if selected is None:
        return False

    selected = selected or selected_default
    if selected not in profile_names:
        raise ConfigurationError(f"Profile desconhecido: {selected}")
    selected_view = profiles.get(selected)
    if selected_view is None:
        raise ConfigurationError(f"Profile inv\u00e1lido: {selected}")
    model = read_prompt(prompt, f"Modelo [{selected_view.model}]: ", default=selected_view.model)
    if model is None:
        return False
    endpoint = read_prompt(prompt, f"Endpoint compat\u00edvel [{selected_view.endpoint}]: ", default=selected_view.endpoint)
    if endpoint is None:
        return False
    update_first_run_configuration(app_paths, selected, model or selected_view.model, endpoint or selected_view.endpoint, config_path=None)
    args._first_run_guided = True
    return True


def recover_first_run_config(
    args: argparse.Namespace,
    *,
    console: Any,
    app_paths: AppPaths,
    prompt: Any | None = None,
) -> int:
    config_file = configuration_path(app_paths, None)
    console.print(f"[yellow]Configura\u00e7\u00e3o do Agent n\u00e3o encontrada:[/yellow]\n{config_file}")
    console.print("\nParece ser o primeiro uso neste perfil.")
    args._first_run_guided = False
    reader = prompt
    if reader is None:
        from llm_agent.interfaces.cli.interactive_shell import prompt_from

        reader = partial(prompt_from, console)
    answer = read_prompt(reader, "Deseja criar a configuração padrão agora? [Y/n] ")
    if answer is None or answer.casefold() not in {"", "y", "yes", "s", "sim"}:
        console.print(f"Nenhum arquivo foi criado. Execute quando desejar:\n  {config_init_command(args)}")
        return 0
    created = initialize_configuration(app_paths, None)
    console.print(f"Configuração criada em {created}.")

    # A test/embedder may project an interactive flag while not providing a
    # real TTY. Keep that compatibility path actionable; the supported TTY
    # path below receives the PTK composer and is guided.
    if prompt is None:
        console.print("Para validar: llm-agent config validate")
        console.print("Para diagnóstico: llm-agent doctor")
        console.print("Depois, abra: llm-agent chat")
        return 0

    try:
        from llm_agent.application.home_operations import transient_home_operation

        with transient_home_operation(app_paths):
            view = read_first_run_configuration(app_paths, None)
            completed = _complete_guided_setup(args, view, app_paths, console, prompt)
        if not completed:
            console.print("Configuração guiada cancelada. A configuração padrão já confirmada foi preservada; nenhum ajuste foi aplicado.")
            return 0
        console.print("Configuração guiada validada. Diagnóstico de conectividade é opcional; entrando no chat.")
    except (ConfigurationError, OSError, ValueError) as exc:
        console.print(f"[red]Configuração guiada não concluída:[/red] {exc}")
        console.print("Use os comandos avançados de config para corrigir e tente novamente.")
        return 0
    return 0


def prepare_chat_workspace(
    args: argparse.Namespace,
    *,
    console: Any,
    app_paths: AppPaths,
    prompt: Any | None = None,
    prompt_path: Any | None = None,
) -> bool:
    """Offer workspace entry only after an existing config is valid."""

    if getattr(args, "workspace", None) is not None:
        return False
    if not is_interactive_terminal():
        from llm_agent.interfaces.cli.workspace_entry import require_task_workspace

        require_task_workspace(args)
        return False
    config_path = getattr(args, "config", None)
    if not configuration_ready_for_chat_entry(app_paths, config_path):
        return False
    from llm_agent.application.workspace_recents import list_recent_workspaces
    from llm_agent.interfaces.cli.workspace_entry import choose_workspace, load_last_workspace

    args.workspace = str(
        choose_workspace(
            console=console,
            last_workspace=load_last_workspace(app_paths),
            recent_workspaces=list_recent_workspaces(app_paths),
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
