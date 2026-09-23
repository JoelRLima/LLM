"""Bounded, non-authoritative Discovery frecency storage."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Protocol, cast

from agent.memory.json_persistence import read_json_object, write_text_atomic
from agent.runtime.instance_lock import InstanceLock

MAX_DISCOVERY_FRECENCY_ENTRIES = 256
MAX_DISCOVERY_USE_COUNT = 1_000_000
MAX_FUTURE_CLOCK_SKEW_SECONDS = 300


class _DiscoveryAppPaths(Protocol):
    discovery_frecency_file: str | Path


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("frecency timestamp must be UTC")
    return value.astimezone(timezone.utc)


def _stamp(value: datetime) -> str:
    return _utc(value).isoformat().replace("+00:00", "Z")


def _parse_stamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("invalid frecency timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return _utc(parsed)


class FrecencyStore:
    """Canonical frecency document owner; query text is never written."""

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.path = Path(path).resolve() if path is not None else None
        self.lock_path = self.path.with_name(f"{self.path.name}.lock") if self.path is not None else None
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._entries: dict[str, tuple[int, datetime]] = {}
        self.diagnostics: tuple[str, ...] = ()
        self._load()

    @classmethod
    def for_app_paths(cls, app_paths: object, *, clock: Callable[[], datetime] | None = None) -> "FrecencyStore":
        typed_paths = cast(_DiscoveryAppPaths, app_paths)
        return cls(typed_paths.discovery_frecency_file, clock=clock)

    def _now(self) -> datetime:
        return _utc(self._clock())

    def _load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        try:
            payload = read_json_object(self.path)
            if payload is None or payload.get("schema_version") != 1 or not isinstance(payload.get("entries"), list):
                raise ValueError("invalid frecency document")
            merged: dict[str, tuple[int, datetime]] = {}
            now = self._now()
            for item in payload["entries"][:MAX_DISCOVERY_FRECENCY_ENTRIES * 2]:
                if not isinstance(item, dict):
                    raise ValueError("invalid frecency entry")
                entry_id = item.get("entry_id")
                count = item.get("use_count")
                stamp = item.get("last_used_utc")
                if not isinstance(entry_id, str) or not entry_id or isinstance(count, bool) or not isinstance(count, int) or not 0 <= count <= MAX_DISCOVERY_USE_COUNT:
                    raise ValueError("invalid frecency entry")
                timestamp = _parse_stamp(stamp)
                if timestamp > now + timedelta(seconds=MAX_FUTURE_CLOCK_SKEW_SECONDS):
                    continue
                previous = merged.get(entry_id)
                if previous is None:
                    merged[entry_id] = (min(count, MAX_DISCOVERY_USE_COUNT), timestamp)
                else:
                    merged[entry_id] = (
                        min(max(previous[0], count), MAX_DISCOVERY_USE_COUNT),
                        max(previous[1], timestamp),
                    )
            self._entries = dict(sorted(merged.items())[:MAX_DISCOVERY_FRECENCY_ENTRIES])
        except Exception:
            self._entries = {}
            self.diagnostics = ("DISCOVERY_FRECENCY_CORRUPT",)

    def _document(self) -> dict[str, object]:
        ordered = sorted(self._entries.items(), key=lambda item: (-item[1][0], item[0]))
        entries: list[dict[str, object]] = [
            {"entry_id": entry_id, "use_count": count, "last_used_utc": _stamp(timestamp)}
            for entry_id, (count, timestamp) in ordered[:MAX_DISCOVERY_FRECENCY_ENTRIES]
        ]
        return {"schema_version": 1, "entries": entries}

    def _persist(self) -> None:
        if self.path is None or self.lock_path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with InstanceLock.create(self.lock_path, create_parent=False):
            write_text_atomic(
                self.path,
                json.dumps(self._document(), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
                create_parent=False,
            )

    def record(self, entry_id: str, *, now: datetime | None = None) -> None:
        if not isinstance(entry_id, str) or not entry_id:
            raise ValueError("entry_id is required")
        timestamp = _utc(now or self._now())
        count = min(self._entries.get(entry_id, (0, timestamp))[0] + 1, MAX_DISCOVERY_USE_COUNT)
        self._entries[entry_id] = (count, timestamp)
        if len(self._entries) > MAX_DISCOVERY_FRECENCY_ENTRIES:
            ordered = sorted(self._entries.items(), key=lambda item: (item[1][1], item[0]), reverse=True)
            self._entries = dict(ordered[:MAX_DISCOVERY_FRECENCY_ENTRIES])
        self._persist()

    def score(self, entry_id: str, *, now: datetime | None = None) -> int:
        value = self._entries.get(entry_id)
        if value is None:
            return 0
        current = _utc(now or self._now())
        count, last_used = value
        if last_used > current + timedelta(seconds=MAX_FUTURE_CLOCK_SKEW_SECONDS):
            return 0
        age = max(0.0, (current - last_used).total_seconds())
        recency = 40 if age <= 86_400 else 25 if age <= 604_800 else 10 if age <= 2_592_000 else 0
        return min(100, min(count, 20) * 3 + recency)

    def snapshot(self) -> tuple[dict[str, object], ...]:
        entries = self._document()["entries"]
        return tuple(cast(list[dict[str, object]], entries))


__all__ = [
    "FrecencyStore",
    "MAX_DISCOVERY_FRECENCY_ENTRIES",
    "MAX_DISCOVERY_USE_COUNT",
    "MAX_FUTURE_CLOCK_SKEW_SECONDS",
]
