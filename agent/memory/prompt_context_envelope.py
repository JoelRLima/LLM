"""Bounded envelope rendering for memory prompt projections."""

from __future__ import annotations

from agent.llm.context_projection import (
    OPTIONAL_AUXILIARY,
    UNTRUSTED_MEMORY,
    ContextSourceRecord,
    render_untrusted_context_envelope,
)

MEMORY_PROMPT_CHARS_PER_TOKEN = 4

def _bounded_envelope(sections: list[tuple[str, list[str]]], budget_tokens: int) -> str:
    budget_chars = max(0, int(budget_tokens)) * MEMORY_PROMPT_CHARS_PER_TOKEN
    if budget_chars == 0:
        return ""

    rendered_lines: list[str] = []
    for label, section_lines in sections:
        if not section_lines:
            continue
        rendered_lines.append(f"[{label}]")
        rendered_lines.extend(section_lines)

    body = "\n".join(rendered_lines)
    if not body:
        return ""

    def render(content: str, truncated: bool) -> str:
        record = ContextSourceRecord(
            source_id="memory:prompt-projection",
            source_kind="memory_projection",
            trust_class=UNTRUSTED_MEMORY,
            reason="bounded objective-scoped memory data",
            estimated_tokens=max(1, len(content) // MEMORY_PROMPT_CHARS_PER_TOKEN),
            truncated=truncated,
            complete=not truncated,
            data={"content": content},
        )
        return render_untrusted_context_envelope((record,), category=OPTIONAL_AUXILIARY)

    full = render(body, False)
    if len(full) <= budget_chars:
        return full

    # Clip the content field before serialization while keeping the outer
    # envelope complete and parseable.  This is local character allocation,
    # not a second model-token ledger or a tokenizer probe loop.
    low, high = 0, len(body)
    best = ""
    while low <= high:
        midpoint = (low + high) // 2
        candidate = render(body[:midpoint], midpoint < len(body))
        if len(candidate) <= budget_chars:
            best = candidate
            low = midpoint + 1
        else:
            high = midpoint - 1
    return best

__all__ = ["_bounded_envelope"]
