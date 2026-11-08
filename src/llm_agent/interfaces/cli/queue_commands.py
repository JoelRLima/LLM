"""Command forwarding for the standalone test queue CLI."""

from __future__ import annotations

import argparse
from typing import Any, cast


def _value(args: argparse.Namespace, name: str, default: Any = None) -> Any:
    return getattr(args, name, default)


def _run_test_queue(args: argparse.Namespace) -> int:
    """Forward the unified CLI entrypoint to the existing queue adapter."""
    from llm_agent.interfaces.test_queue import main as run_test_queue

    forwarded = list(_value(args, "queue_args", ()))
    if _value(args, "queue_help", False):
        if not forwarded:
            return cast(int, run_test_queue(["--help"]))
        forwarded.append("--help")
    config_path = _value(args, "config")
    profile = _value(args, "profile")
    unsupported = ("home", "workspace", "observability_mode")
    if any(_value(args, name) is not None for name in unsupported):
        raise ValueError(
            "A fila recebe a pasta nos próprios argumentos; --home, --workspace e --observability-mode não se aplicam"
        )
    if not forwarded:
        if config_path is not None or profile is not None:
            raise ValueError("Abra o menu sem flags globais de modelo ou use-as em queue run/check")
        return cast(int, run_test_queue([]))

    _forward_queue_model_flags(forwarded, config_path, profile)

    return cast(int, run_test_queue(forwarded))


def _forward_queue_model_flags(forwarded: list[str], config_path: Any, profile: Any) -> None:
    if config_path is None and profile is None:
        return
    if forwarded[0] not in {"run", "check"}:
        raise ValueError("Na fila, use --config e --profile em run ou check")
    if "--config" in forwarded or "--profile" in forwarded:
        raise ValueError("Informe --config e --profile antes de 'queue' ou dentro do comando, sem repetir")
    if config_path is not None:
        forwarded.extend(("--config", str(config_path)))
    if profile is not None:
        forwarded.extend(("--profile", str(profile)))
