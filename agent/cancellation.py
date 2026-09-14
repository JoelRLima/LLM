class CancellationToken:
    def __init__(self) -> None:
        from threading import Lock

        self._lock = Lock()
        self._cancelled = False

    def cancel(self) -> None:
        with self._lock:
            self._cancelled = True

    def reset(self) -> None:
        with self._lock:
            self._cancelled = False

    @property
    def cancelled(self) -> bool:
        with self._lock:
            return bool(self._cancelled)

    def is_set(self) -> bool:
        """Event-compatible read for narrow interactive cancellation seams."""

        return self.cancelled


def is_cancellation_requested(token: object | None, event: object | None = None) -> bool:
    """Read either the task token or a per-invocation event defensively."""

    if event is not None and bool(getattr(event, "is_set", lambda: False)()):
        return True
    return bool(getattr(token, "cancelled", False))
