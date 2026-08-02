"""Explicit, side-effect-free application logging."""

from __future__ import annotations

import logging
import sys
import threading
from pathlib import Path

from agent.runtime.paths import LOG_FILE

LOGGER_NAME = "LLM_Agent"
_HANDLER_MARKER = "_llm_agent_owned"
_console_handler: logging.Handler | None = None
_active_log_file: Path | None = None
_active_console = False
_lease_count = 0
_lock = threading.RLock()


class LoggingConfigurationError(RuntimeError):
    """Raised when live applications request incompatible process logging."""

logger = logging.getLogger(LOGGER_NAME)
logger.addHandler(logging.NullHandler())


def _owned(handler: logging.Handler) -> bool:
    return bool(getattr(handler, _HANDLER_MARKER, False))


def _mark_owned(handler: logging.Handler) -> logging.Handler:
    setattr(handler, _HANDLER_MARKER, True)
    return handler


def _close_owned_handlers() -> None:
    global _console_handler
    for handler in list(logger.handlers):
        if not _owned(handler):
            continue
        logger.removeHandler(handler)
        handler.close()
    _console_handler = None
    if not logger.handlers:
        logger.addHandler(logging.NullHandler())


def teardown_logger() -> None:
    """Release one logging lease and close handlers after the final owner."""

    global _active_console, _active_log_file, _lease_count
    with _lock:
        if _lease_count > 1:
            _lease_count -= 1
            return
        _lease_count = 0
        _active_log_file = None
        _active_console = False
        _close_owned_handlers()


def setup_logger(
    debug_mode: int = 0,
    *,
    log_file: str | Path | None = None,
    console: bool = True,
) -> logging.Logger:
    """Configure logging after application paths have been resolved."""

    global _active_console, _active_log_file, _console_handler, _lease_count
    selected = Path(log_file or LOG_FILE).expanduser().resolve()
    with _lock:
        if _lease_count:
            if selected != _active_log_file or console != _active_console:
                raise LoggingConfigurationError(
                    "Outra aplicação já configurou logging incompatível neste processo."
                )
            _lease_count += 1
            set_debug_level(debug_mode)
            return logger

        _close_owned_handlers()
        logger.setLevel(logging.DEBUG)
        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )
        selected.parent.mkdir(parents=True, exist_ok=True)
        try:
            file_handler = _mark_owned(
                logging.FileHandler(selected, encoding="utf-8")
            )
            file_handler.setLevel(logging.DEBUG)
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)

            if console:
                handler = _mark_owned(logging.StreamHandler(sys.stderr))
                handler.setLevel(
                    logging.DEBUG if debug_mode >= 1 else logging.WARNING
                )
                handler.setFormatter(formatter)
                logger.addHandler(handler)
                _console_handler = handler
        except Exception:
            _close_owned_handlers()
            raise
        _active_log_file = selected
        _active_console = console
        _lease_count = 1
        return logger


def set_debug_level(mode: int) -> None:
    """Change the explicitly configured console handler level."""

    with _lock:
        if _console_handler is not None:
            _console_handler.setLevel(
                logging.DEBUG if mode >= 1 else logging.WARNING
            )


__all__ = [
    "LoggingConfigurationError",
    "logger",
    "set_debug_level",
    "setup_logger",
    "teardown_logger",
]
