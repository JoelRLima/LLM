"""Bounded publication helpers for the trace writer control path."""

from __future__ import annotations

import threading
from typing import Any

from agent.observability.trace_types import TraceCompleteness


def publish_metadata(
    owner: Any,
    *,
    force: bool = False,
    allow_latched: bool = False,
    final: bool = False,
) -> None:
    """Publish a snapshot without holding the ingestion condition."""

    with owner._condition:
        owner._publication_in_flight += 1
    try:
        with owner._metadata_publish_lock:
            with owner._condition:
                if owner._writer_error is not None and not allow_latched:
                    return
                if not force and not owner._metadata_dirty:
                    return
                revision = owner._metadata_revision
                metadata = owner._metadata_snapshot().to_dict()
                final_publication = final or (
                    bool(owner._closed) and not bool(metadata.get("active", True))
                )
            if final_publication:
                owner._update_index(metadata)
                owner._write_metadata(metadata)
            else:
                owner._write_metadata(metadata)
                owner._update_index(metadata)
            with owner._condition:
                current = owner._metadata_snapshot().to_dict()
                needs_reconcile = bool(owner._finalization_timed_out) and (
                    current != metadata or metadata.get("completeness") != TraceCompleteness.UNCLEAN.value
                )
                if owner._metadata_revision == revision and not needs_reconcile:
                    owner._metadata_dirty = False
                owner._condition.notify_all()
            if not needs_reconcile:
                return

            # A late publisher must leave the unclean terminal state as its last
            # metadata publication after a shutdown timeout.
            owner._update_index(current)
            owner._write_metadata(current)
            with owner._condition:
                if owner._metadata_revision == revision or owner._metadata_values == current:
                    owner._metadata_dirty = False
                owner._condition.notify_all()
    finally:
        with owner._condition:
            owner._publication_in_flight -= 1
            owner._condition.notify_all()


def publish_metadata_bounded(owner: Any, timeout_seconds: float) -> bool:
    """Attempt final publication without extending the caller deadline."""

    finished = threading.Event()
    failure: list[BaseException] = []

    def publish() -> None:
        try:
            owner._publish_metadata(force=True, allow_latched=True, final=True)
        except BaseException as exc:
            failure.append(exc)
            owner._note_writer_failure(exc)
        finally:
            finished.set()

    publisher = threading.Thread(
        target=publish,
        name=f"llm-agent-trace-final-{owner.run_key[:12]}",
        daemon=True,
    )
    publisher.start()
    finished.wait(timeout=max(0.0, timeout_seconds))
    return finished.is_set() and not failure


__all__ = ["publish_metadata", "publish_metadata_bounded"]
