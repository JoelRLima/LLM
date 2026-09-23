"""Deterministic local Discovery normalization and ranking."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Iterable, Mapping
from difflib import SequenceMatcher

from agent.discovery.contracts import (
    MAX_DISCOVERY_QUERY_CHARS,
    DiscoveryAvailability,
    DiscoveryCandidateV1,
    DiscoveryEntryV1,
    DiscoveryMatchKind,
)

_WS = re.compile(r"\s+")
_KIND_RANK = {
    DiscoveryMatchKind.NONE: 0,
    DiscoveryMatchKind.FUZZY: 1,
    DiscoveryMatchKind.SUBSTRING: 2,
    DiscoveryMatchKind.TOKEN: 3,
    DiscoveryMatchKind.PREFIX: 4,
    DiscoveryMatchKind.EXACT: 5,
}
_FIELDS = ("preferred_invocation", "alias", "title", "keyword", "description", "example")


def normalize_query(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("discovery query must be text")
    if len(value) > MAX_DISCOVERY_QUERY_CHARS:
        raise ValueError("discovery query exceeds its bound")
    return _WS.sub(" ", unicodedata.normalize("NFKC", value).strip().casefold())


def _field_values(entry: DiscoveryEntryV1) -> tuple[tuple[str, str], ...]:
    return (
        ("preferred_invocation", entry.preferred_invocation),
        *(('alias', value) for value in entry.aliases),
        ("title", entry.title),
        *(('keyword', value) for value in entry.keywords),
        ("description", entry.description),
        *(('example', value) for value in entry.examples),
    )


def _field_rank(kind: str) -> int:
    return _FIELDS.index(kind) if kind in _FIELDS else len(_FIELDS)


def _best_match(entry: DiscoveryEntryV1, query: str) -> tuple[DiscoveryMatchKind, int, int, int]:
    if not query:
        return DiscoveryMatchKind.NONE, len(_FIELDS), 0, 0
    query_tokens = tuple(dict.fromkeys(query.split()))
    best = (DiscoveryMatchKind.NONE, len(_FIELDS), 0, 0)
    for name, raw_value in _field_values(entry):
        value = normalize_query(raw_value)
        if not value:
            continue
        kind = DiscoveryMatchKind.NONE
        token_score = 0
        fuzzy_score = 0
        if value == query:
            kind = DiscoveryMatchKind.EXACT
        elif value.startswith(query):
            kind = DiscoveryMatchKind.PREFIX
        else:
            value_tokens = set(value.split())
            token_score = round(
                (sum(token in value_tokens for token in query_tokens) / len(query_tokens)) * 1000
            ) if query_tokens else 0
            if token_score:
                kind = DiscoveryMatchKind.TOKEN
            elif query in value:
                kind = DiscoveryMatchKind.SUBSTRING
            fuzzy_score = round(SequenceMatcher(None, query, value, autojunk=False).ratio() * 1000)
            if kind is DiscoveryMatchKind.NONE and fuzzy_score >= 650:
                kind = DiscoveryMatchKind.FUZZY
        candidate = (kind, _field_rank(name), token_score, fuzzy_score)
        if (
            _KIND_RANK[candidate[0]],
            -candidate[1],
            candidate[2],
            candidate[3],
        ) > (
            _KIND_RANK[best[0]],
            -best[1],
            best[2],
            best[3],
        ):
            best = candidate
    return best


def score_entry(
    entry: DiscoveryEntryV1,
    query: str,
    *,
    availability: DiscoveryAvailability,
    frecency_score: int,
) -> DiscoveryCandidateV1:
    kind, field_rank, token_score, fuzzy_score = _best_match(entry, query)
    return DiscoveryCandidateV1(
        entry=entry,
        available=availability.available,
        disabled_reason=availability.disabled_reason,
        match_kind=kind,
        local_score={
            "match_kind_rank": _KIND_RANK[kind],
            "field_rank": field_rank,
            "token_score": token_score,
            "fuzzy_score": fuzzy_score,
        },
        frecency_score=frecency_score,
    )


def candidate_sort_key(item: DiscoveryCandidateV1) -> tuple[object, ...]:
    score = item.local_score
    return (
        -int(score.get("match_kind_rank", 0)),
        int(score.get("field_rank", len(_FIELDS))),
        -int(score.get("token_score", 0)),
        -int(score.get("fuzzy_score", 0)),
        -item.frecency_score,
        0 if item.available else 1,
        item.entry.title.casefold(),
        item.entry.entry_id,
    )


def rank_entries(
    entries: Iterable[DiscoveryEntryV1],
    query: str,
    *,
    availability_by_entry_id: Mapping[str, DiscoveryAvailability] | None = None,
    frecency: Callable[[str], int] | None = None,
) -> tuple[tuple[DiscoveryCandidateV1, ...], tuple[DiscoveryCandidateV1, ...]]:
    """Return (displayable, semantic-pool) in the frozen local order."""

    normalized = normalize_query(query)
    availability_map = availability_by_entry_id or {}
    score = frecency or (lambda _entry_id: 0)
    values = [
        score_entry(
            entry,
            normalized,
            availability=availability_map.get(entry.entry_id, DiscoveryAvailability(False, "DISCOVERY_AVAILABILITY_UNKNOWN")),
            frecency_score=max(0, min(100, int(score(entry.entry_id)))),
        )
        for entry in entries
    ]
    values.sort(key=candidate_sort_key)
    if not normalized:
        return tuple(values), tuple(values)
    display: list[DiscoveryCandidateV1] = []
    for item in values:
        kind = item.match_kind
        fuzzy_score = int(item.local_score.get("fuzzy_score", 0))
        if kind is not DiscoveryMatchKind.NONE or fuzzy_score >= 650:
            display.append(item)
    return tuple(display), tuple(values)


def rank_candidates(values: Iterable[DiscoveryCandidateV1]) -> tuple[DiscoveryCandidateV1, ...]:
    return tuple(sorted(values, key=candidate_sort_key))


__all__ = [
    "candidate_sort_key",
    "normalize_query",
    "rank_candidates",
    "rank_entries",
    "score_entry",
]
