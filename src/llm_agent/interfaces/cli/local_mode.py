"""Local-first terminal entry point for the shared interactive core."""
from __future__ import annotations

from typing import Any

from llm_agent.interactive.session_factory import create_local_session
from llm_agent.interfaces.cli.first_run import is_interactive_terminal
from llm_agent.interfaces.cli.interactive_shell import InteractiveShell
from llm_agent.interfaces.cli.shared_terminal import SharedTerminal
from llm_agent.interfaces.cli.ui import console


def run_local_mode(args: Any) -> int:
    if not is_interactive_terminal():
        console.print("`llm-agent local` exige um terminal interativo. Use `llm-agent commands` ou `llm-agent doctor` para automação.", markup=False)
        return 2
    from llm_agent.interfaces.cli.action_registry import DEFAULT_CLI_ACTION_REGISTRY

    session, _workspace = create_local_session(
        workspace=getattr(args, "workspace", None), home=getattr(args, "home", None),
        config_path=getattr(args, "config", None), profile=getattr(args, "profile", None),
        observability_mode=getattr(args, "observability_mode", None),
    )
    shell = None
    try:
        shell = InteractiveShell(registry=DEFAULT_CLI_ACTION_REGISTRY,
                                 toolbar=lambda: "F2 ou /commands ? texto local = shell ? /exec ? /llm on/off/test ? /status ? /cancel ? /exit")
        SharedTerminal(session, shell).run()
    except KeyboardInterrupt:
        session.cancel()
    finally:
        if shell is not None:
            shell.set_background_pump(None)
        try:
            session.close().result()
        except Exception as exc:
            console.print(f"A sessão terminou com limpeza pendente: {exc}", style="yellow", markup=False)
        finally:
            if shell is not None:
                shell.close()
    return 0


__all__ = ["run_local_mode"]
