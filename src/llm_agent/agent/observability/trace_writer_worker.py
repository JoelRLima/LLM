"""Worker-loop mechanics for the bounded trace writer."""

from __future__ import annotations

import time
from typing import Any


def _terminal_control_item(owner: Any) -> tuple[bool, Any | None] | None:
    if owner._writer_error is not None:
        return False, None
    if owner._finalization_timed_out:
        if owner._metadata_dirty:
            owner._inflight = True
            return True, None
        return False, None
    return None


def _ordinary_control_item(owner: Any) -> tuple[bool, Any | None] | None:
    forced = owner._closing or owner._finalization_pending or bool(owner._flush_targets)
    if forced and owner._metadata_dirty and not getattr(owner, "_heartbeat_quiesced", False):
        owner._inflight = True
        return True, None
    if _metadata_publish_due(owner):
        owner._inflight = True
        return True, None
    return None


def _metadata_publish_due(owner: Any) -> bool:
    if not owner._metadata_dirty or getattr(owner, "_heartbeat_quiesced", False):
        return False
    dirty_since = owner._metadata_dirty_since
    if dirty_since is None:
        return True
    return bool(time.monotonic() - dirty_since >= owner._metadata_coalesce_delay_seconds)


def _flush_frontier_due(owner: Any) -> bool:
    persisted = int(owner._metadata_values["highest_sequence_persisted"])
    return any(
        persisted >= sequence
        and (
            owner._published_metadata_revision < revision
            or owner._published_highest_sequence < sequence
        )
        for revision, sequence in owner._flush_targets
    )


def _ready_item(owner: Any) -> tuple[bool, Any | None] | None:
    terminal = _terminal_control_item(owner)
    if terminal is not None:
        return terminal
    if not owner._pending and owner._pending_gaps:
        owner._materialize_gap_locked()
    if owner._pending and (_flush_frontier_due(owner) or _metadata_publish_due(owner)):
        owner._inflight = True
        return True, None
    if owner._pending:
        item = owner._pending.popleft()
        owner._inflight = True
        owner._materialize_gap_locked()
        owner._condition.notify_all()
        return True, item
    control = _ordinary_control_item(owner)
    if control is not None:
        return control
    if owner._closing and not owner._finalization_pending:
        return False, None
    return None


def _next_timeout(owner: Any) -> float:
    timeout = 0.25
    if owner._metadata_dirty and not getattr(owner, "_heartbeat_quiesced", False):
        dirty_since = owner._metadata_dirty_since
        if dirty_since is not None:
            remaining = owner._metadata_coalesce_delay_seconds - (time.monotonic() - dirty_since)
            if remaining > 0:
                timeout = min(timeout, remaining)
    return timeout


def _next_item(owner: Any) -> tuple[bool, Any | None]:
    with owner._condition:
        while True:
            item = _ready_item(owner)
            if item is not None:
                return item
            owner._condition.wait(timeout=_next_timeout(owner))


def _finish_inflight(owner: Any, *, clear_metadata: bool = False) -> None:
    with owner._condition:
        if clear_metadata:
            owner._metadata_dirty = False
            owner._metadata_dirty_since = None
        owner._inflight = False
        owner._condition.notify_all()


def _persist_control(owner: Any) -> None:
    try:
        owner._persist_dirty_metadata()
    except Exception as exc:
        owner._note_writer_failure(exc)
        _finish_inflight(owner, clear_metadata=True)
    else:
        _finish_inflight(owner)


def _persist_item(owner: Any, item: Any) -> None:
    with owner._condition:
        if owner._writer_error is not None:
            _finish_inflight(owner)
            return
    try:
        owner._persist_envelope(item)
    except Exception as exc:
        owner._note_writer_failure(exc, failed_sequence=item.envelope.sequence)
        _finish_inflight(owner)
    else:
        _finish_inflight(owner)


def run_writer(owner: Any) -> None:
    try:
        owner._file_handle = owner._open_writer_file()
        while True:
            should_continue, item = _next_item(owner)
            if not should_continue:
                break
            if item is None:
                _persist_control(owner)
            else:
                _persist_item(owner, item)
    except Exception as exc:
        owner._note_writer_failure(exc)
    finally:
        handle = owner._file_handle
        owner._file_handle = None
        if handle is not None:
            try:
                handle.flush()
                handle.close()
            except Exception as exc:
                owner._note_writer_failure(exc)


__all__ = ["run_writer"]
