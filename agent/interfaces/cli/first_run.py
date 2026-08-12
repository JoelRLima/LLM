"""First-run recovery for the human CLI boundary."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from agent.interfaces.cli import maintenance


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


def recover_first_run_config(
    args: argparse.Namespace,
    *,
    console: Any,
    app_paths: Any,
) -> int:
    repository = maintenance.config_repository(app_paths, None)
    console.print(f"[yellow]Configuração do Agent não encontrada:[/yellow]\n{repository.path}")
    console.print("\nParece ser o primeiro uso neste perfil.")
    try:
        answer = console.input("Deseja criar a configuração padrão agora? [Y/n] ")
    except (EOFError, KeyboardInterrupt):
        console.print(f"\nNenhum arquivo foi criado. Execute quando desejar:\n  {config_init_command(args)}")
        return 0
    if answer.strip().casefold() not in {"", "y", "yes", "s", "sim"}:
        console.print(f"Nenhum arquivo foi criado. Execute quando desejar:\n  {config_init_command(args)}")
        return 0
    created = maintenance.initialize_config(app_paths, None)
    console.print(f"Configuração criada em {created}.")
    console.print(
        "Configure o profile/model endpoint e então execute:\n"
        "  llm-agent config validate\n"
        "  llm-agent doctor\n"
        "  llm-agent chat"
    )
    return 0


__all__ = [
    "actionable_missing_config",
    "config_init_command",
    "is_interactive_terminal",
    "recover_first_run_config",
]
