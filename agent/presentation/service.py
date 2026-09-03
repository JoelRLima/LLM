"""Read-only presentation service shared by CLI, chat, and future frontends."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from agent.observability.bookmarks import BookmarkStore
from agent.observability.envelopes import ObservationSource
from agent.observability.live import SilencePolicy, SilenceStatus
from agent.observability.liveness import TraceLivenessPolicy
from agent.observability.silence import clock_now
from agent.observability.trace_store import (
    TraceCatalog,
    TraceMetadata,
    TraceReadResult,
)
from agent.presentation.models import (
    MAX_QUERY_LIMIT,
    Activity,
    InspectionQuery,
    InspectorSnapshot,
    RunSummary,
    unavailable_section,
)
from agent.presentation.projector import project_activities
from agent.presentation.query import matches_activity
from agent.presentation.sections import derive_sections, merge_sections


@dataclass(frozen=True, slots=True)
class SelectedTrace:
    metadata: TraceMetadata
    read_result: TraceReadResult


class InspectionService:
    """Workspace-scoped, side-effect-free trace read API."""

    def __init__(
        self,
        workspace_paths: Any,
        *,
        canonical_reader: Callable[[], Mapping[str, Any]] | None = None,
        silence_reader: Callable[[TraceMetadata], SilenceStatus | Mapping[str, Any] | None] | None = None,
        bookmark_reader: Callable[[str], Iterable[Mapping[str, Any]]] | None = None,
        clock: Callable[[], Any] | None = None,
    ) -> None:
        self.workspace_paths = workspace_paths
        self.catalog = TraceCatalog(workspace_paths)
        self._canonical_reader = canonical_reader
        self._silence_reader = silence_reader
        self._bookmark_reader = bookmark_reader or BookmarkStore(workspace_paths).reader
        self._clock = clock
        self._liveness_policy = TraceLivenessPolicy()

    def list_runs(self, *, limit: int = MAX_QUERY_LIMIT) -> tuple[RunSummary, ...]:
        bounded = self._limit(limit)
        return tuple(
            RunSummary.from_metadata(
                item,
                now=self._now(item),
                liveness_policy=self._liveness_policy,
            )
            for item in self.catalog.list_runs(limit=bounded)
        )

    def select(self, run_id: str | None = None, *, active_first: bool = True) -> SelectedTrace:
        now = self._now()
        metadata = (
            self.catalog.latest(
                active_first=active_first,
                now=now,
                liveness_policy=self._liveness_policy,
            )
            if run_id is None
            else self.catalog.find(run_id)
        )
        store = self.catalog.open(metadata.run_id)
        result = store.read_result()
        return SelectedTrace(result.metadata or metadata, result)

    def read_result(self, run_id: str | None = None) -> TraceReadResult:
        return self.select(run_id).read_result

    @staticmethod
    def _limit(limit: int) -> int:
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise TypeError("inspection limit must be an integer")
        if limit < 0:
            raise ValueError("inspection limit must be non-negative")
        return min(limit, MAX_QUERY_LIMIT)

    def _now(self, metadata: TraceMetadata | None = None) -> Any:
        if self._clock is not None:
            return clock_now(self._clock)
        if metadata is not None and not metadata.active and metadata.end_time:
            return metadata.end_time
        return datetime.now(timezone.utc)

    def query(
        self,
        run_id: str | None = None,
        *,
        query: InspectionQuery | None = None,
        limit: int = MAX_QUERY_LIMIT,
        after_sequence: int = 0,
    ) -> tuple[Activity, ...]:
        selected_query = query or InspectionQuery()
        bounded = self._limit(limit)
        if bounded == 0:
            return ()
        if isinstance(after_sequence, bool) or not isinstance(after_sequence, int) or after_sequence < 0:
            raise ValueError("after_sequence must be a non-negative integer")
        selected = self.select(run_id)
        bookmark_records = self._bookmark_records(selected.metadata.run_id)
        bookmark_sequences: list[int] = []
        for item in bookmark_records:
            sequence = item.get("sequence")
            if isinstance(sequence, int) and not isinstance(sequence, bool):
                bookmark_sequences.append(sequence)
        bookmarks = tuple(bookmark_sequences)
        projected: list[Activity] = []
        for activity in project_activities(selected.read_result.records, bookmarks=bookmarks):
            if not matches_activity(activity, selected_query, after_sequence):
                continue
            projected.append(activity)
            if len(projected) >= bounded:
                break
        return tuple(projected)

    def detail(self, run_id: str | None, sequence: int) -> Mapping[str, Any] | None:
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
            raise ValueError("sequence must be a positive integer")
        selected = self.select(run_id)
        for envelope in selected.read_result.records:
            if envelope.sequence == sequence:
                return envelope.to_dict()
        return None

    def replay(
        self,
        run_id: str | None = None,
        *,
        query: InspectionQuery | None = None,
        limit: int = MAX_QUERY_LIMIT,
        after_sequence: int = 0,
    ) -> tuple[Activity, ...]:
        return self.query(run_id, query=query, limit=limit, after_sequence=after_sequence)

    def snapshot(
        self,
        run_id: str | None = None,
        *,
        query: InspectionQuery | None = None,
        limit: int = 100,
        selected_sequence: int | None = None,
        after_sequence: int = 0,
    ) -> InspectorSnapshot:
        selected = self.select(run_id)
        selected_query = query or InspectionQuery()
        activities = self.query(
            selected.metadata.run_id,
            query=selected_query,
            limit=limit,
            after_sequence=after_sequence,
        )
        bookmark_records = self._bookmark_records(selected.metadata.run_id)
        detail = self.detail(selected.metadata.run_id, selected_sequence) if selected_sequence is not None else None
        current = self._current_activity(activities)
        sections = self._canonical_sections(selected.read_result.records)
        warnings = tuple(item for item in activities if item.category in {"warning/error", "observer/diagnostic"} or item.gap)
        heartbeat = self._heartbeat(selected.metadata, selected.read_result.records)
        if selected.read_result.issues:
            heartbeat = dict(heartbeat)
            heartbeat["issues"] = list(selected.read_result.issues)
        return InspectorSnapshot(
            run=RunSummary.from_metadata(
                selected.metadata,
                now=self._now(selected.metadata),
                liveness_policy=self._liveness_policy,
            ),
            current=current,
            plan_steps=sections["plan_steps"],
            timeline=activities,
            model_calls=sections["model_calls"],
            tools=sections["tools"],
            validation=sections["validation"],
            recovery=sections["recovery"],
            changes=sections["changes"],
            metrics=sections["metrics"],
            warnings=warnings,
            heartbeat=heartbeat,
            selected_detail=detail,
            query=selected_query,
            bookmarks=bookmark_records,
            issues=tuple(selected.read_result.issues),
        )

    @staticmethod
    def _current_activity(activities: tuple[Activity, ...]) -> Mapping[str, Any]:
        if not activities:
            return unavailable_section("no persisted activity")
        active = next((item for item in reversed(activities) if item.active), None)
        selected = active or activities[-1]
        return selected.to_dict()

    def _canonical_sections(self, records: Iterable[Any]) -> dict[str, Mapping[str, Any]]:
        return merge_sections(derive_sections(records), self._canonical_reader)

    def _heartbeat(self, metadata: TraceMetadata, records: Iterable[Any] = ()) -> Mapping[str, Any]:
        heartbeat: dict[str, Any] | None = None
        if self._silence_reader is not None:
            try:
                value = self._silence_reader(metadata)
                if isinstance(value, SilenceStatus):
                    heartbeat = value.to_dict()
                elif isinstance(value, Mapping):
                    heartbeat = dict(value)
            except Exception:
                heartbeat = unavailable_section("heartbeat source read failed")
        if heartbeat is None:
            watchdog = None
            for record in records:
                payload = getattr(record, "payload", {})
                if (
                    getattr(record, "source", None) is ObservationSource.RUNTIME_EVENT
                    and payload.get("type") == "watchdog"
                ):
                    watchdog = record.timestamp
            selected_now = metadata.end_time if metadata.end_time and not metadata.active else self._now()
            heartbeat = SilencePolicy().evaluate(
                last_semantic_activity=metadata.last_semantic_activity,
                last_observer_heartbeat=metadata.last_observer_heartbeat,
                now=selected_now,
                canonical_watchdog=watchdog,
            ).to_dict()
        heartbeat["liveness"] = self._liveness_policy.evaluate(metadata, self._now(metadata)).to_dict()
        return heartbeat

    def _bookmark_records(self, run_id: str) -> tuple[Mapping[str, Any], ...]:
        if self._bookmark_reader is None:
            return ()
        try:
            selected: list[Mapping[str, Any]] = []
            for item in self._bookmark_reader(run_id):
                sequence = item.get("sequence") if isinstance(item, Mapping) else None
                if isinstance(sequence, int) and not isinstance(sequence, bool) and sequence > 0 and isinstance(item, Mapping):
                    selected.append(dict(item))
            return tuple(selected[:256])
        except Exception:
            return ()


__all__ = ["InspectionService", "SelectedTrace"]
