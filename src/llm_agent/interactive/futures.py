"""Future adapter that settles through the existing controller, without a worker."""

from concurrent.futures import Future, TimeoutError
from threading import RLock
from time import monotonic
from typing import Callable, TypeVar

T = TypeVar("T")


class SettlementPending(TimeoutError):
    """The wait expired while the operation still owns its worker."""


class SettlementFuture(Future[T]):
    def __init__(self, resolve: Callable[[float | None], T], lock: RLock) -> None:
        super().__init__()
        self._resolve = resolve
        self._settlement_lock = lock
        self.set_running_or_notify_cancel()

    def result(self, timeout: float | None = None) -> T:
        deadline = None if timeout is None else monotonic() + max(0, timeout)
        acquired = (
            self._settlement_lock.acquire()
            if deadline is None
            else self._settlement_lock.acquire(timeout=max(0, deadline - monotonic()))
        )
        if not acquired:
            raise TimeoutError("interactive operation has not settled")
        try:
            if not self.done():
                # Timeouts leave the handle pending; a later result/close can
                # still collect it. A query is never cancelled by merely waiting.
                try:
                    value = self._resolve(None if deadline is None else max(0, deadline - monotonic()))
                except SettlementPending:
                    raise TimeoutError("interactive operation has not settled") from None
                except BaseException as exc:
                    self.set_exception(exc)
                else:
                    self.set_result(value)
        finally:
            self._settlement_lock.release()
        return super().result(timeout=0)


def completed(value: T | None = None, error: BaseException | None = None) -> Future[T | None]:
    future: Future[T | None] = Future()
    if error is None:
        future.set_result(value)
    else:
        future.set_exception(error)
    return future
