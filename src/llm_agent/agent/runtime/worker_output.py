"""Thread-local output seam for the interactive worker boundary."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Callable, Iterator

WorkerOutput = Callable[[str], None]
FallbackOutput = Callable[[object, str], None]
_sink: ContextVar[WorkerOutput | None] = ContextVar("interactive_worker_output", default=None)


@contextmanager
def bind_worker_output(sink: WorkerOutput) -> Iterator[None]:
    token = _sink.set(sink)
    try:
        yield
    finally:
        _sink.reset(token)


def emit_worker_output(
    value: object = "",
    *,
    end: str = "\n",
    fallback: FallbackOutput | None = None,
) -> None:
    text = f"{value}{end}"
    sink = _sink.get()
    if sink is None:
        if fallback is not None:
            fallback(value, end)
        else:
            print(text, end="")
    else:
        sink(text)


__all__ = ["WorkerOutput", "bind_worker_output", "emit_worker_output"]
