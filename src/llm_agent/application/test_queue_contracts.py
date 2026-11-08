"""Shared typed callbacks and progress values for the durable test queue."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from llm_agent.application.test_queue_manifest import TestCase


class Cancellation(Protocol):
    def is_set(self) -> bool: ...


Executor = Callable[[TestCase, Path, Cancellation, int], dict[str, Any]]


@dataclass(frozen=True)
class QueueProgress:
    case_id: str
    status: str
    position: int
    total: int
    elapsed_seconds: float


ProgressObserver = Callable[[QueueProgress], None]


def notify(observer: ProgressObserver | None, progress: QueueProgress) -> None:
    if observer is None:
        return
    try:
        observer(progress)
    except Exception as exc:
        # Presentation failure cannot change a durable execution result.
        logging.getLogger(__name__).warning("Queue progress observer failed: %s", type(exc).__name__)


class Verifier(Protocol):
    def __call__(
        self,
        workspace: Path,
        checks: tuple[dict[str, Any], ...],
        *,
        cancellation: object | None = None,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]: ...
