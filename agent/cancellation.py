class CancellationToken:
    def __init__(self) -> None:
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def reset(self) -> None:
        self._cancelled = False

    @property
    def cancelled(self) -> bool:
        return bool(self._cancelled)


def is_cancellation_requested(token: object | None, event: object | None = None) -> bool:
    """Read either the task token or a per-invocation event defensively."""

    if event is not None and bool(getattr(event, "is_set", lambda: False)()):
        return True
    return bool(getattr(token, "cancelled", False))
