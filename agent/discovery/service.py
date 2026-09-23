"""Local-first Discovery service with deterministic semantic fallback."""

from __future__ import annotations

import importlib.util

from agent.discovery.contracts import (
    DISCOVERY_AVAILABILITY_UNKNOWN,
    DISCOVERY_NO_MATCH,
    DISCOVERY_QUERY_EMPTY,
    DISCOVERY_SEMANTIC_UNAVAILABLE,
    MAX_DISCOVERY_RESULTS,
    DiscoveryAvailability,
    DiscoveryExecutionContext,
    DiscoveryResultV1,
    availability_for,
)
from agent.discovery.index import DiscoveryCatalog
from agent.discovery.ranking import normalize_query, rank_entries
from agent.discovery.semantic import SemanticCommandDiscovery
from agent.discovery.store import FrecencyStore


class DiscoveryService:
    """Purely descriptive local discovery over one immutable catalog."""

    def __init__(
        self,
        catalog: DiscoveryCatalog,
        *,
        frecency: FrecencyStore | None = None,
        semantic: SemanticCommandDiscovery | None = None,
    ) -> None:
        self.catalog = catalog
        self.frecency = frecency or FrecencyStore()
        self.semantic = semantic

    def search(
        self,
        query: str = "",
        *,
        limit: int = MAX_DISCOVERY_RESULTS,
        context: DiscoveryExecutionContext | None = None,
        semantic_requested: bool = False,
        semantic_allowed: bool = False,
    ) -> DiscoveryResultV1:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 0 <= limit <= MAX_DISCOVERY_RESULTS:
            raise ValueError("discovery limit is invalid")
        normalized = normalize_query(query)
        execution = context or DiscoveryExecutionContext()
        availability = {
            entry.entry_id: availability_for(entry, execution)
            for entry in self.catalog.entries()
        }
        display, candidate_pool = rank_entries(
            self.catalog.entries(),
            normalized,
            availability_by_entry_id=availability,
            frecency=self.frecency.score,
        )
        reasons: list[str] = [DISCOVERY_QUERY_EMPTY] if not normalized else []
        if any(item.disabled_reason == DISCOVERY_AVAILABILITY_UNKNOWN for item in (*display, *candidate_pool)):
            reasons.append(DISCOVERY_AVAILABILITY_UNKNOWN)
        if normalized and not display:
            reasons.append(DISCOVERY_NO_MATCH)
        result = DiscoveryResultV1(
            normalized,
            tuple(display[:limit]),
            tuple(reasons),
            bool(semantic_requested),
            False,
        )
        if semantic_requested and self.semantic is not None:
            result = self.semantic.search(
                result,
                semantic_allowed=semantic_allowed,
                candidate_pool=candidate_pool,
            )
        elif semantic_requested and candidate_pool:
            result = DiscoveryResultV1(
                result.query,
                result.candidates,
                (*result.reasons, DISCOVERY_SEMANTIC_UNAVAILABLE),
                True,
                False,
            )
        elif semantic_requested and not candidate_pool:
            result = DiscoveryResultV1(
                result.query,
                result.candidates,
                (*result.reasons, DISCOVERY_NO_MATCH),
                True,
                False,
            )
        return result

    def record_use(self, entry_id: str) -> None:
        self.frecency.record(entry_id)


def mcp_engineering_availability() -> DiscoveryAvailability:
    available = importlib.util.find_spec("mcp") is not None
    return DiscoveryAvailability(available, None if available else "ENGINEERING_MCP_EXTRA_REQUIRED")


LocalDiscovery = DiscoveryService


def default_service(*, app_paths: object | None = None, catalog: DiscoveryCatalog | None = None) -> DiscoveryService:
    """Create a neutral empty service unless a trusted catalog is supplied."""

    frecency = FrecencyStore.for_app_paths(app_paths) if app_paths is not None else FrecencyStore()
    return DiscoveryService(catalog or DiscoveryCatalog(), frecency=frecency)


__all__ = [
    "DiscoveryService",
    "LocalDiscovery",
    "default_service",
    "mcp_engineering_availability",
]
