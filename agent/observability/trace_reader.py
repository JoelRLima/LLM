"""Read-only JSONL reader mixin for active and retained traces."""

from __future__ import annotations

import json
from typing import Any, cast

from agent.observability.envelopes import ObservationEnvelope, ObservationSource
from agent.observability.trace_paths import TraceCorruptError, TraceStoreError, _assert_safe_path
from agent.observability.trace_types import TraceCompleteness, TraceMetadata, TraceReadResult

MAX_TRACE_READ_BYTES = 64 * 1024 * 1024
MAX_TRACE_QUERY_LIMIT = 1_000


def _missing_trace_result(metadata: TraceMetadata, *, raise_on_corrupt: bool) -> TraceReadResult:
    if metadata.active and metadata.highest_sequence_persisted == 0:
        return TraceReadResult((), metadata.completeness, (), metadata=metadata)
    issue = (
        "trace file is missing after persisted records"
        if metadata.highest_sequence_persisted > 0
        else "trace file is missing"
    )
    if raise_on_corrupt:
        raise TraceCorruptError(issue)
    return TraceReadResult((), TraceCompleteness.CORRUPT, (issue,), metadata=metadata)


def _derive_status(
    metadata: Any,
    records: list[ObservationEnvelope],
    partial_final: bool,
) -> TraceCompleteness:
    if metadata.completeness is TraceCompleteness.CORRUPT:
        return TraceCompleteness.CORRUPT
    if metadata.completeness is TraceCompleteness.UNCLEAN:
        return TraceCompleteness.UNCLEAN
    active_status = _active_status(metadata, records, partial_final)
    if active_status is not None:
        return active_status
    if partial_final:
        return TraceCompleteness.UNCLEAN
    if metadata.completeness is TraceCompleteness.PARTIAL:
        return TraceCompleteness.PARTIAL
    if any(item.source is ObservationSource.GAP for item in records):
        return TraceCompleteness.PARTIAL
    if not records and metadata.highest_sequence_accepted > 0:
        return TraceCompleteness.PARTIAL
    return TraceCompleteness.COMPLETE


def _active_status(
    metadata: Any,
    records: list[ObservationEnvelope],
    partial_final: bool,
) -> TraceCompleteness | None:
    if not metadata.active:
        return None
    has_gap = any(item.source is ObservationSource.GAP for item in records)
    # A reader can observe the writer between append syscalls.  That
    # transient tail does not upgrade known loss to UNCLEAN.
    if metadata.completeness is TraceCompleteness.PARTIAL or has_gap:
        return TraceCompleteness.PARTIAL
    if partial_final or metadata.completeness is TraceCompleteness.ACTIVE:
        return TraceCompleteness.ACTIVE
    return None


def _uncovered_sequences(metadata: Any, records: list[ObservationEnvelope]) -> tuple[int, int]:
    if not records:
        return metadata.highest_sequence_accepted, 0
    previous = 0
    missing = 0
    covered: list[tuple[int, int]] = []
    for item in records:
        if item.source is ObservationSource.GAP:
            start = item.payload.get("start_sequence")
            end = item.payload.get("end_sequence")
            if isinstance(start, int) and isinstance(end, int):
                covered.append((start, end))
        if item.sequence > previous + 1:
            missing += item.sequence - previous - 1
        previous = item.sequence
    if metadata.highest_sequence_accepted > previous:
        missing += metadata.highest_sequence_accepted - previous
    covered_count = sum(max(0, end - start + 1) for start, end in covered)
    return missing, covered_count


def _read_trace_file(store: Any) -> tuple[list[ObservationEnvelope], bool, str | None]:
    records: list[ObservationEnvelope] = []
    partial_final = False
    try:
        _assert_safe_path(store.trace_file, directory=False)
        if store.trace_file.stat().st_size > MAX_TRACE_READ_BYTES:
            raise TraceCorruptError("trace exceeds the bounded read size")
        with store.trace_file.open("rb") as handle:
            previous_sequence = 0
            for raw_line in handle:
                if not raw_line.endswith(b"\n"):
                    partial_final = True
                    break
                if not raw_line.strip():
                    raise TraceCorruptError("blank trace line")
                try:
                    document = json.loads(raw_line.decode("utf-8"))
                    envelope = ObservationEnvelope.from_dict(document)
                    if envelope.run_id != store.run_id or envelope.sequence <= previous_sequence:
                        raise ValueError("sequence/run identity is invalid")
                    previous_sequence = envelope.sequence
                    records.append(envelope)
                except (UnicodeError, json.JSONDecodeError, TypeError, ValueError, KeyError) as exc:
                    raise TraceCorruptError(f"malformed trace record: {type(exc).__name__}") from exc
    except (OSError, TraceStoreError) as exc:
        issue = str(exc) if isinstance(exc, TraceCorruptError) else "trace file cannot be read"
        return records, partial_final, issue
    return records, partial_final, None


class TraceReaderMixin:
    """Bounded parser that never truncates or repairs the writer file."""

    def read_result(self: Any, *, raise_on_corrupt: bool = False) -> TraceReadResult:
        try:
            metadata = self._read_metadata_file()
        except TraceCorruptError as exc:
            if raise_on_corrupt:
                raise
            return TraceReadResult((), TraceCompleteness.CORRUPT, (str(exc),), metadata=None)
        if not self.trace_file.exists():
            return _missing_trace_result(metadata, raise_on_corrupt=raise_on_corrupt)
        try:
            records, partial_final, issue = _read_trace_file(self)
        except TraceCorruptError as exc:
            if raise_on_corrupt:
                raise
            return TraceReadResult((), TraceCompleteness.CORRUPT, (str(exc),), False, metadata)
        if issue is not None:
            if raise_on_corrupt:
                raise TraceCorruptError(issue)
            return TraceReadResult(tuple(records), TraceCompleteness.CORRUPT, (issue,), partial_final, metadata)
        status = _derive_status(metadata, records, partial_final)
        missing, covered_count = _uncovered_sequences(metadata, records)
        if missing > covered_count and status is TraceCompleteness.COMPLETE:
            status = TraceCompleteness.PARTIAL
        return TraceReadResult(tuple(records), status, (), partial_final, metadata)

    def read(self: Any, *, raise_on_corrupt: bool = True) -> tuple[ObservationEnvelope, ...]:
        result = cast(TraceReadResult, self.read_result(raise_on_corrupt=raise_on_corrupt))
        return result.records

    def read_records(self: Any, *, limit: int = MAX_TRACE_QUERY_LIMIT) -> tuple[ObservationEnvelope, ...]:
        bounded = max(0, min(int(limit), MAX_TRACE_QUERY_LIMIT))
        result = cast(TraceReadResult, self.read_result())
        return result.records[:bounded]

    def tail(self: Any, *, after_sequence: int = 0, limit: int = 256) -> tuple[ObservationEnvelope, ...]:
        if isinstance(after_sequence, bool) or not isinstance(after_sequence, int) or after_sequence < 0:
            raise ValueError("after_sequence must be a non-negative integer")
        bounded = max(0, min(int(limit), MAX_TRACE_QUERY_LIMIT))
        return tuple(item for item in self.read_result().records if item.sequence > after_sequence)[:bounded]


__all__ = ["MAX_TRACE_QUERY_LIMIT", "MAX_TRACE_READ_BYTES", "TraceReaderMixin"]
