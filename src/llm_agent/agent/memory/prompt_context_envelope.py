"""Bounded envelope rendering for memory prompt projections."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from itertools import chain

from llm_agent.agent.llm.context_projection import (
    OPTIONAL_AUXILIARY,
    UNTRUSTED_MEMORY,
    ContextSourceRecord,
    render_untrusted_context_envelope,
)

MEMORY_PROMPT_CHARS_PER_TOKEN = 4

def _append_section(
    rendered_lines: list[str],
    body_chars: int,
    budget_chars: int,
    label: str,
    section_lines: Iterable[str],
) -> tuple[int, bool]:
    line_iterator = iter(section_lines)
    first_line = next(line_iterator, None)
    if first_line is None:
        return body_chars, False
    header = f"[{label}]"
    separator = 1 if rendered_lines else 0
    if body_chars + separator + len(header) > budget_chars:
        return body_chars, True
    if separator:
        rendered_lines.append("\n")
        body_chars += 1
    rendered_lines.append(header)
    body_chars += len(header)
    for line in chain((first_line,), line_iterator):
        remaining = budget_chars - body_chars - 1
        if remaining <= 0:
            return body_chars, True
        rendered_lines.extend(("\n", line[:remaining]))
        body_chars += 1 + min(len(line), remaining)
        if len(line) > remaining:
            return body_chars, True
    return body_chars, False


def _render_body(sections: Iterable[tuple[str, Iterable[str]]], budget_chars: int) -> tuple[str, bool]:
    rendered_lines: list[str] = []
    body_chars = 0
    truncated = False
    for label, section_lines in sections:
        body_chars, truncated = _append_section(
            rendered_lines, body_chars, budget_chars, label, section_lines
        )
        if truncated:
            break
    return "".join(rendered_lines), truncated


def _fit_envelope(
    body: str,
    truncated: bool,
    budget_chars: int,
    render: Callable[[str, bool], str],
) -> str:
    low, high = 0, len(body)
    best = ""
    while low <= high:
        midpoint = (low + high) // 2
        candidate = render(body[:midpoint], truncated or midpoint < len(body))
        if len(candidate) <= budget_chars:
            best = candidate
            low = midpoint + 1
        else:
            high = midpoint - 1
    return best


def _bounded_envelope(
    sections: Iterable[tuple[str, Iterable[str]]],
    budget_tokens: int,
) -> str:
    budget_chars = max(0, int(budget_tokens)) * MEMORY_PROMPT_CHARS_PER_TOKEN
    if budget_chars == 0:
        return ""
    body, truncated = _render_body(sections, budget_chars)
    if not body:
        return ""

    def render(content: str, is_truncated: bool) -> str:
        record = ContextSourceRecord(
            source_id="memory:prompt-projection",
            source_kind="memory_projection",
            trust_class=UNTRUSTED_MEMORY,
            reason="bounded objective-scoped memory data",
            estimated_tokens=max(1, len(content) // MEMORY_PROMPT_CHARS_PER_TOKEN),
            truncated=is_truncated,
            complete=not is_truncated,
            data={"content": content},
        )
        return render_untrusted_context_envelope((record,), category=OPTIONAL_AUXILIARY)

    full = render(body, truncated)
    if len(full) <= budget_chars:
        return full
    # Clip the content before serialization while keeping the outer envelope valid.
    return _fit_envelope(body, truncated, budget_chars, render)

__all__ = ["_bounded_envelope"]
