"""Bounded publication helpers for the trace writer control path."""

from __future__ import annotations

import threading
from typing import Any

from agent.observability.trace_types import TraceCompleteness


def _write_snapshot(owner: Any, metadata: dict[str, Any], *, final: bool) -> None:
    if final:
        owner._update_index(metadata)
        owner._write_metadata(metadata)
    else:
        owner._write_metadata(metadata)
        owner._update_index(metadata)


def _acknowledge_snapshot(
    owner: Any,
    *,
    written: dict[str, Any],
    revision: int,
    recovery_publication: bool,
    followup: bool,
) -> tuple[dict[str, Any], bool, bool]:
    with owner._condition:
        current = owner._metadata_snapshot().to_dict()
        needs_reconcile = bool(owner._finalization_timed_out) and (
            current != written or written.get("completeness") != TraceCompleteness.UNCLEAN.value
        )
        revision_unchanged = owner._metadata_revision == revision
        snapshot_matches = current == written
        heartbeat_followup = (
            not needs_reconcile
            and not recovery_publication
            and not followup
            and revision_unchanged
            and not snapshot_matches
        )
        if not needs_reconcile:
            if revision_unchanged and snapshot_matches:
                owner._metadata_dirty = False
                owner._heartbeat_quiesced = False
            else:
                owner._metadata_dirty = True
                owner._heartbeat_quiesced = bool(recovery_publication and not followup and revision_unchanged)
                if followup and revision_unchanged and not snapshot_matches:
                    owner._heartbeat_recovery_pending = True
        owner._condition.notify_all()
    return current, needs_reconcile, heartbeat_followup


def _reconcile_late_publication(owner: Any, metadata: dict[str, Any]) -> None:
    owner._update_index(metadata)
    owner._write_metadata(metadata)
    with owner._condition:
        owner._metadata_dirty = owner._metadata_snapshot().to_dict() != metadata
        owner._heartbeat_quiesced = False
        owner._heartbeat_recovery_pending = False
        owner._condition.notify_all()


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
                recovery_publication = bool(getattr(owner, "_heartbeat_recovery_pending", False))
                owner._heartbeat_recovery_pending = False
                revision = owner._metadata_revision
                metadata = owner._metadata_snapshot().to_dict()
                final_publication = final or (
                    bool(owner._closed) and not bool(metadata.get("active", True))
                )
            _write_snapshot(owner, metadata, final=final_publication)
            current, needs_reconcile, heartbeat_followup = _acknowledge_snapshot(
                owner,
                written=metadata,
                revision=revision,
                recovery_publication=recovery_publication,
                followup=False,
            )
            if not needs_reconcile and heartbeat_followup:
                # Heartbeats are observer projections.  Publish one latest
                # coalesced snapshot, then leave a newer tick explicitly dirty
                # for one normal writer-control cycle.
                followup_metadata = current
                _write_snapshot(owner, followup_metadata, final=final_publication)
                current, needs_reconcile, _ = _acknowledge_snapshot(
                    owner,
                    written=followup_metadata,
                    revision=revision,
                    recovery_publication=recovery_publication,
                    followup=True,
                )
            if needs_reconcile:
                # A late publisher must leave the unclean terminal state as its
                # last metadata publication after a shutdown timeout.
                _reconcile_late_publication(owner, current)
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
