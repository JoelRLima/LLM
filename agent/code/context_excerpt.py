"""Deterministic source excerpts used by the code proposal context."""

from __future__ import annotations

from agent.code.contracts import CodeAnalysis


def excerpt_from_source(
    source: str,
    analysis: CodeAnalysis | None,
    terms: frozenset[str],
    max_chars: int,
) -> tuple[str, bool]:
    if len(source) <= max_chars:
        return source, False
    lines = source.splitlines(keepends=True)
    ranges: list[tuple[int, int]] = [(0, min(len(lines), 40))]
    for symbol in analysis.symbols if analysis is not None else ():
        if symbol.name.casefold() in terms or symbol.qualified_name.casefold() in terms:
            ranges.append((max(0, symbol.start_line - 4), min(len(lines), symbol.end_line + 3)))
    selected: list[str] = []
    seen: set[int] = set()
    for start, end in ranges:
        for index in range(start, end):
            if index in seen:
                continue
            seen.add(index)
            selected.append(f"{index + 1:>5}: {lines[index]}")
            if sum(len(item) for item in selected) >= max_chars:
                return "".join(selected)[:max_chars], True
    return "".join(selected)[:max_chars], True
