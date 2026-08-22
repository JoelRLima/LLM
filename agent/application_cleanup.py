"""Resource cleanup helpers for the standalone application boundary."""

from __future__ import annotations

from typing import Any

from agent.runtime.logging import logger, teardown_logger


def abort_startup(instance_lock: Any, logging_acquired: bool) -> None:
    try:
        instance_lock.release()
    except BaseException:
        logger.exception("Falha ao liberar lock durante startup abortado.")
    finally:
        if logging_acquired:
            try:
                teardown_logger()
            except BaseException:
                logger.exception("Falha ao desmontar logging durante startup abortado.")


def release_resources(instance_lock: Any, owns_logging: bool) -> BaseException | None:
    cleanup_error: BaseException | None = None
    try:
        instance_lock.release()
    except BaseException as exc:
        cleanup_error = exc
        logger.exception("Falha ao liberar lock durante close.")
    try:
        if owns_logging:
            teardown_logger()
    except BaseException as exc:
        if cleanup_error is None:
            cleanup_error = exc
        logger.exception("Falha ao desmontar logging durante close.")
    return cleanup_error


__all__ = ["abort_startup", "release_resources"]
