"""Bounded, deterministic and untrusted memory projection for model prompts."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path
from typing import Any

from llm_agent.agent.memory.prompt_context_envelope import _bounded_envelope
from llm_agent.agent.memory.prompt_context_file_facts import (
    _include_file_fact,
    file_fact_freshness,
)
from llm_agent.agent.memory.prompt_context_file_facts import (
    _stored_source_hash as _stored_source_hash,
)

DEFAULT_MEMORY_PROMPT_BUDGET_TOKENS = 800
MEMORY_PROMPT_CHARS_PER_TOKEN = 4
MAX_MEMORY_PROMPT_HASH_BYTES = 32 * 1024 * 1024
MAX_MEMORY_PROMPT_FILE_HASH_BYTES = 8 * 1024 * 1024

_FILE_REFERENCE_RE = re.compile(r"[\w./\\-]+\.[\w-]+", re.UNICODE)
_TOKEN_RE = re.compile(r"[\w./\\-]+", re.UNICODE)


def _as_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
            separators=(",", ":"),
        )
    except Exception:
        return str(value)


def _entries(value: Any) -> list[tuple[str, Any]]:
    if isinstance(value, Mapping):
        return [
            (str(key), item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if item not in (None, "", {}, [])
        ]
    if isinstance(value, list):
        return [
            (str(index), item)
            for index, item in enumerate(value)
            if item not in (None, "", {}, [])
        ]
    return []


def _objective_tokens(objective: str) -> set[str]:
    return {
        token.casefold()
        for token in _TOKEN_RE.findall(objective)
        if len(token) >= 3
    }


def _entry_tokens(key: str, value: Any) -> set[str]:
    return {token.casefold() for token in _TOKEN_RE.findall(f"{key} {_as_text(value)}")}


def _rank_entries(
    entries: list[tuple[str, Any]],
    objective_tokens: set[str],
) -> list[tuple[str, Any]]:
    if not objective_tokens:
        return entries
    relevant: list[tuple[str, Any]] = []
    unrelated: list[tuple[str, Any]] = []
    for entry in entries:
        target = (
            relevant
            if objective_tokens.intersection(_entry_tokens(entry[0], entry[1]))
            else unrelated
        )
        target.append(entry)
    return relevant + unrelated


def _referenced_file(path: str, objective: str, objective_tokens: set[str]) -> bool:
    normalized = path.replace("\\", "/").casefold()
    basename = normalized.rsplit("/", 1)[-1]
    references = {
        reference.replace("\\", "/").casefold()
        for reference in _FILE_REFERENCE_RE.findall(objective)
    }
    return normalized in references or basename in references or basename in objective_tokens











def _section_lines(
    state: Mapping[str, Any],
    section: str,
    objective_tokens: set[str],
    *,
    only_relevant: bool = False,
) -> Iterator[str]:
    entries = _entries(state.get(section))
    ranked = _rank_entries(entries, objective_tokens)
    for key, value in ranked:
        if only_relevant and objective_tokens and not objective_tokens.intersection(
            _entry_tokens(key, value)
        ):
            continue
        yield f"- {key}: {_as_text(value)}"


def _bounded_file_entries(
    state: Mapping[str, Any],
    entries: Iterable[tuple[str, Any]],
    objective: str,
    objective_tokens: set[str],
    workspace_root: str | Path | None,
    budget_chars: int,
    remaining_hash_bytes: int,
    *,
    referenced_only: bool,
) -> tuple[list[tuple[str, Any]], int, int]:
    selected: list[tuple[str, Any]] = []
    used_chars = 0
    for key, value in entries:
        if referenced_only and not _referenced_file(key, objective, objective_tokens):
            continue
        if used_chars >= budget_chars:
            break
        include, hashed_bytes = _include_file_fact(
            state,
            key,
            workspace_root,
            max_bytes=min(MAX_MEMORY_PROMPT_FILE_HASH_BYTES, remaining_hash_bytes),
        )
        remaining_hash_bytes = max(0, remaining_hash_bytes - hashed_bytes)
        if include:
            selected.append((key, value))
            used_chars += len(f"- {key}: {_as_text(value)}") + 1
    return selected, used_chars, remaining_hash_bytes


def build_memory_prompt_context(
    state: Mapping[str, Any],
    objective: str = "",
    budget_tokens: int = DEFAULT_MEMORY_PROMPT_BUDGET_TOKENS,
    workspace_root: str | Path | None = None,
) -> str:
    """Project relevant persisted state into one bounded untrusted envelope.

    The character budget uses ``chars / 4`` as an explicit token estimate and
    is enforced on the complete rendered envelope, including its trust frame.
    File summaries are selected only when the objective explicitly mentions
    the file.  An analyzed-file entry is omitted when its detailed summary is
    already rendered, so the same artifact is not emitted by two projections.
    """
    if not isinstance(state, Mapping) or budget_tokens <= 0:
        return ""

    objective_tokens = _objective_tokens(objective)
    budget_chars = budget_tokens * MEMORY_PROMPT_CHARS_PER_TOKEN
    detailed_entries, detail_chars, remaining_hash_bytes = _bounded_file_entries(
        state,
        _entries(state.get("file_summaries")),
        objective,
        objective_tokens,
        workspace_root,
        budget_chars,
        MAX_MEMORY_PROMPT_HASH_BYTES,
        referenced_only=True,
    )
    detailed_paths = {key.replace("\\", "/").casefold() for key, _ in detailed_entries}
    analyzed_candidates = [
        (key, value)
        for key, value in _entries(state.get("analyzed_files"))
        if key.replace("\\", "/").casefold() not in detailed_paths
    ]
    analyzed_budget = max(
        0,
        budget_chars
        - detail_chars
        - (len("[RELEVANT FILE SUMMARIES]") if detailed_entries else 0),
    )
    analyzed_entries, _, _ = _bounded_file_entries(
        state,
        _rank_entries(analyzed_candidates, objective_tokens),
        objective,
        objective_tokens,
        workspace_root,
        analyzed_budget,
        remaining_hash_bytes,
        referenced_only=False,
    )
    sections: list[tuple[str, Iterable[str]]] = [
        (
            "RELEVANT FILE SUMMARIES",
            (f"- {key}: {_as_text(value)}" for key, value in detailed_entries),
        ),
        (
            "ANALYZED FILE INDEX",
            (f"- {key}: {_as_text(value)}" for key, value in analyzed_entries),
        ),
    ]
    for section in ("key_findings", "notes", "todo"):
        sections.append((section.upper(), _section_lines(state, section, objective_tokens)))
    for section in ("project_map", "files_index"):
        sections.append(
            (
                section.upper(),
                _section_lines(state, section, objective_tokens, only_relevant=True),
            )
        )
    return _bounded_envelope(sections, budget_tokens)


__all__ = [
    "DEFAULT_MEMORY_PROMPT_BUDGET_TOKENS",
    "MEMORY_PROMPT_CHARS_PER_TOKEN",
    "build_memory_prompt_context",
    "file_fact_freshness",
]
