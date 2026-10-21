from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from llm_agent.agent.runtime.logging import logger as canonical_logger
from llm_agent.agent.runtime.logging import setup_logger, teardown_logger
from llm_agent.application.session_diagnostics import apply_interactive_diagnostic_mode
from llm_agent.application.task_execution import _retain_runtime


def _runtime(*, verbose: bool = False):
    orchestrator = SimpleNamespace(
        verbose=verbose,
        context_manager=SimpleNamespace(verbose=verbose),
    )
    owner = SimpleNamespace(orchestrator=orchestrator)
    return _retain_runtime(owner), orchestrator


@pytest.mark.parametrize(
    "module_name",
    [
        "llm_agent.interfaces.cli.chat",
        "llm_agent.interfaces.cli.streaming",
    ],
)
def test_interface_logger_is_the_canonical_agent_logger(module_name: str) -> None:
    from importlib import import_module

    module = import_module(module_name)

    assert module._logger is canonical_logger
    assert module._logger.name == "LLM_Agent"


def test_interactive_mode_cycle_preserves_session_and_console_logging(tmp_path, capsys) -> None:
    runtime, orchestrator = _runtime()
    log_file = tmp_path / "logs" / "agent.log"
    setup_logger(log_file=log_file, console=True)
    console_handler = next(
        handler
        for handler in canonical_logger.handlers
        if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler)
    )
    try:
        expected = (
            (0, False, logging.WARNING),
            (1, True, logging.DEBUG),
            (2, True, logging.DEBUG),
            (0, False, logging.WARNING),
        )
        for index, (mode, session_enabled, console_level) in enumerate(expected):
            apply_interactive_diagnostic_mode(
                runtime,
                mode,
                session_diagnostics_enabled=session_enabled,
            )
            assert console_handler.level == console_level
            assert orchestrator.verbose is session_enabled
            assert orchestrator.context_manager.verbose is session_enabled

            marker = f"session-diagnostic-mode-{index}"
            canonical_logger.debug(marker)
            for handler in canonical_logger.handlers:
                handler.flush()
            stderr = capsys.readouterr().err
            assert (marker in stderr) is (console_level == logging.DEBUG)
            assert marker in log_file.read_text(encoding="utf-8")
    finally:
        teardown_logger()


def test_controller_managed_mode_leaves_session_worker_verbosity_unchanged(tmp_path) -> None:
    runtime, orchestrator = _runtime(verbose=True)
    log_file = tmp_path / "agent.log"
    setup_logger(log_file=log_file, console=False)
    try:
        apply_interactive_diagnostic_mode(
            runtime,
            2,
            session_diagnostics_enabled=None,
        )
        assert orchestrator.verbose is True
        assert orchestrator.context_manager.verbose is True
    finally:
        teardown_logger()


@pytest.mark.parametrize("mode", [-1, 3])
def test_interactive_mode_rejects_values_outside_the_command_cycle(mode: int) -> None:
    runtime, _ = _runtime()
    with pytest.raises(ValueError, match="must be 0, 1, or 2"):
        apply_interactive_diagnostic_mode(runtime, mode, session_diagnostics_enabled=None)
