"""Seleção compacta de memória para o prompt do modelo."""

from __future__ import annotations

import re
from typing import Any, Mapping


def _analyzed_files_context(
    state: Mapping[str, Any],
    budget_tokens: int,
) -> tuple[str, int]:
    analyzed = state.get("analyzed_files", {})
    if not isinstance(analyzed, dict) or not analyzed:
        return "", 0
    lines = [
        f"- {path}: {summary}"
        for path, summary in list(analyzed.items())[:30]
    ]
    text = "\n".join(lines)
    if len(text) // 4 > budget_tokens * 0.6:
        text = "\n".join(lines[:15])
    return f"--- ARQUIVOS JÁ ANALISADOS ---\n{text}", len(text) // 4


def _relevant_summaries_context(
    state: Mapping[str, Any],
    objective: str,
    remaining_budget: int,
) -> str:
    if remaining_budget <= 100 or not objective:
        return ""
    mentioned = set(re.findall(r"[\w\-/]+\.\w+", objective))
    summaries = state.get("file_summaries", {})
    if not isinstance(summaries, dict):
        return ""
    relevant = [
        (path, summary)
        for path, summary in summaries.items()
        if path in mentioned or path.split("/")[-1] in mentioned
    ]
    text = "\n".join(
        f"- {path}: {str(summary)[:300]}"
        for path, summary in relevant[:5]
    )
    if text and len(text) // 4 <= remaining_budget:
        return f"--- RESUMOS DETALHADOS ---\n{text}"
    return ""


def build_memory_prompt_context(
    state: Mapping[str, Any],
    objective: str = "",
    budget_tokens: int = 800,
) -> str:
    """Retorna somente o subconjunto relevante ao objetivo e ao orçamento."""

    parts: list[str] = []
    index_text, budget_used = _analyzed_files_context(state, budget_tokens)
    if index_text:
        parts.append(index_text)
    summary_text = _relevant_summaries_context(
        state,
        objective,
        budget_tokens - budget_used,
    )
    if summary_text:
        parts.append(summary_text)
    return "\n\n".join(parts)


__all__ = ["build_memory_prompt_context"]
