"""One-use handoff of recent request measurements from fitting to admission."""
from __future__ import annotations

import threading
import time
import weakref
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from llm_agent.agent.runtime.request_measurement import (
    PROVIDER_CHAT_INPUT_TOKENS,
    PROVIDER_TEXT_TOKENIZER,
    RequestInputMeasurement,
)


@dataclass(frozen=True)
class _PendingMeasurement:
    request: weakref.ReferenceType[Any]
    fingerprint: str
    expires: float
    measurement: RequestInputMeasurement


class RequestMeasurementReuse:
    """Bounded, volatile handoff; never a cache of model responses or prompts."""

    def __init__(
        self, fingerprint: Callable[[Any], str | None], *,
        clock: Callable[[], float] = time.monotonic, ttl_seconds: float = 1.0, capacity: int = 8,
    ) -> None:
        if ttl_seconds <= 0 or capacity < 1:
            raise ValueError("measurement handoff limits must be positive")
        self._fingerprint = fingerprint
        self._clock, self._ttl, self._capacity = clock, ttl_seconds, capacity
        self._pending: OrderedDict[int, _PendingMeasurement] = OrderedDict()
        self._lock = threading.Lock()
        self._dispatch_generation = 0

    def _prune_locked(self, now: float) -> None:
        for key in tuple(self._pending):
            item = self._pending[key]
            if item.expires <= now or item.request() is None:
                del self._pending[key]

    def discard(self, request: Any) -> None:
        """End the handoff before dispatch, including failed dispatch attempts."""
        with self._lock:
            self._pending.pop(id(request), None)
            self._dispatch_generation += 1

    def measure(
        self, request: Any, measure: Callable[[], RequestInputMeasurement],
    ) -> RequestInputMeasurement:
        fingerprint = self._fingerprint(request)
        if fingerprint is None:
            self.discard(request)
            return measure()
        with self._lock:
            self._prune_locked(self._clock())
            cached = self._pending.pop(id(request), None)
            generation = self._dispatch_generation
        if cached is not None and cached.request() is request and cached.fingerprint == fingerprint:
            return cached.measurement
        result = measure()
        if (
            not result.available
            or result.source not in {PROVIDER_CHAT_INPUT_TOKENS, PROVIDER_TEXT_TOKENIZER}
            or fingerprint != self._fingerprint(request)
        ):
            return result
        try:
            reference = weakref.ref(request)
        except TypeError:
            return result
        with self._lock:
            if generation != self._dispatch_generation:
                return result
            now = self._clock()
            self._prune_locked(now)
            self._pending[id(request)] = _PendingMeasurement(reference, fingerprint, now + self._ttl, result)
            while len(self._pending) > self._capacity:
                self._pending.popitem(last=False)
        return result


__all__ = ["RequestMeasurementReuse"]
