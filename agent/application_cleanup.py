"""Resource cleanup helpers for the standalone application boundary."""

from __future__ import annotations

from typing import Any

from agent.runtime.logging import logger, teardown_logger


class StartupCleanupError(RuntimeError):
    """Expose every cleanup failure while retaining the startup failure."""

    def __init__(
        self, original: BaseException, cleanup_failures: tuple[BaseException, ...]
    ) -> None:
        self.original = original
        self.cleanup_failures = cleanup_failures
        super().__init__(
            f"startup failed with {type(original).__name__}; "
            f"{len(cleanup_failures)} cleanup operation(s) also failed"
        )


def abort_startup(
    instance_lock: Any | None,
    logging_acquired: bool,
    home_lease: Any | None = None,
) -> tuple[BaseException, ...]:
    failures: list[BaseException] = []
    try:
        if instance_lock is not None:
            instance_lock.release()
    except BaseException as exc:
        failures.append(exc)
        logger.exception("Falha ao liberar lock durante startup abortado.")
    if logging_acquired:
        try:
            teardown_logger()
        except BaseException as exc:
            failures.append(exc)
            logger.exception("Falha ao desmontar logging durante startup abortado.")
    if home_lease is not None:
        try:
            home_lease.close()
        except BaseException as exc:
            failures.append(exc)
            logger.exception("Falha ao liberar lease da home durante startup abortado.")
    return tuple(failures)


def release_resources(
    instance_lock: Any,
    owns_logging: bool,
    home_lease: Any | None = None,
) -> BaseException | None:
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
    try:
        if home_lease is not None:
            home_lease.close()
    except BaseException as exc:
        if cleanup_error is None:
            cleanup_error = exc
        logger.exception("Falha ao liberar lease da home durante close.")
    return cleanup_error


__all__ = ["StartupCleanupError", "abort_startup", "release_resources"]
