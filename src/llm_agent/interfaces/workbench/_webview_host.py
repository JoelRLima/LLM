"""Native WebView2 composition; bridge injection retains the public entry point."""
from __future__ import annotations

import argparse
import ctypes
import logging
import os
import sys
from importlib import resources
from pathlib import Path
from typing import Any, Callable

from llm_agent.application.session_diagnostics import configure_interface_logging
from llm_agent.interactive.session import InteractiveSession

logger = logging.getLogger("llm_agent.workbench.webview")
def create_session(**options: Any) -> tuple[InteractiveSession, Path]:
    from llm_agent.interactive.session_factory import create_local_session

    session, workspace = create_local_session(
        workspace=options.get("workspace"),
        home=options.get("home"),
        config_path=options.get("config_path"),
        profile=options.get("profile"),
    )
    return session, workspace


def main(argv: list[str] | None, bridge_factory: Callable[..., Any], acquire: Callable[[], int | None | bool], release: Callable[[int | None], None], parse: Callable[[list[str] | None], argparse.Namespace]) -> None:
    configure_interface_logging()
    arguments = parse(argv)
    if os.name != "nt":
        raise SystemExit("O Workbench WebView2 requer Windows com o WebView2 Runtime instalado.")
    try:
        import webview
    except ImportError as exc:
        raise SystemExit("Instale a interface desktop com: pip install 'local-llm-agent[desktop]'") from exc
    single_instance = acquire()
    if single_instance is False:
        logger.info("A janela LLM Agent Workbench já está aberta; a instância duplicada foi ignorada.")
        return
    bridge = None
    try:
        bridge = bridge_factory(
            workspace=arguments.workspace,
            home=arguments.home,
            config_path=arguments.config,
            profile=arguments.profile,
        )
        resource = resources.files("llm_agent.interfaces.workbench").joinpath("static").joinpath("index.html")
        with resources.as_file(resource) as index:
            webview.create_window(
                "LLM Agent — Workbench",
                url=str(index),
                js_api=bridge,
                width=1280,
                height=800,
                min_size=(850, 560),
                background_color="#101215",
                text_select=True,
                zoomable=False,
            )
            webview.start(gui="edgechromium", debug=False, private_mode=True, http_server=True)
    finally:
        try:
            if bridge is not None:
                bridge.close()
        finally:
            release(single_instance)



def acquire_single_instance(name: str) -> int | None | bool:
    """Hold a per-interactive-session mutex so only one WebView2 host is created."""
    if sys.platform != "win32":
        return None
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p)
    handle = kernel32.CreateMutexW(None, True, name)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        kernel32.CloseHandle(ctypes.c_void_p(handle))
        return False
    return int(handle)


def release_single_instance(handle: int | None) -> None:
    if handle is not None and sys.platform == "win32":
        ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(ctypes.c_void_p(handle))

