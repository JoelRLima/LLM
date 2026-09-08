"""Bounded, deterministic and untrusted memory projection for model prompts."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agent.memory.prompt_context_envelope import _bounded_envelope
from agent.runtime.path_safety import assert_no_link_ancestors, resolve_workspace_path

DEFAULT_MEMORY_PROMPT_BUDGET_TOKENS = 800
MEMORY_PROMPT_CHARS_PER_TOKEN = 4

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
    relevant = [
        entry
        for entry in entries
        if objective_tokens.intersection(_entry_tokens(entry[0], entry[1]))
    ]
    unrelated = [entry for entry in entries if entry not in relevant]
    return relevant + unrelated


def _referenced_file(path: str, objective: str, objective_tokens: set[str]) -> bool:
    normalized = path.replace("\\", "/").casefold()
    basename = normalized.rsplit("/", 1)[-1]
    references = {
        reference.replace("\\", "/").casefold()
        for reference in _FILE_REFERENCE_RE.findall(objective)
    }
    return normalized in references or basename in references or basename in objective_tokens


_SOURCE_HASH_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def _stored_source_hash(state: Mapping[str, Any], path: str) -> str | None:
    """Return the one current source hash exposed by existing memory state."""

    normalized_path = path.replace("\\", "/").casefold()

    def lookup(mapping: Mapping[str, Any], key: str) -> Any:
        if key in mapping:
            return mapping.get(key)
        for candidate, value in mapping.items():
            if str(candidate).replace("\\", "/").casefold() == normalized_path:
                return value
        return None

    hashes = state.get("file_hashes")
    if isinstance(hashes, Mapping):
        value = lookup(hashes, path)
        return value if isinstance(value, str) and _SOURCE_HASH_RE.fullmatch(value) else None
    cache_entries = state.get("file_cache_entries")
    if isinstance(cache_entries, Mapping):
        entry = lookup(cache_entries, path)
        if isinstance(entry, Mapping):
            value = entry.get("source_hash")
            return value if isinstance(value, str) and _SOURCE_HASH_RE.fullmatch(value) else None
    return None


def file_fact_freshness(
    state: Mapping[str, Any],
    path: str,
    *,
    workspace_root: str | Path | None = None,
    expected_hash: str | None = None,
) -> str:
    """Classify a file-derived memory fact without a model call or mutation."""

    stored_hash = expected_hash if expected_hash is not None else _stored_source_hash(state, path)
    if stored_hash is None:
        return "UNVERIFIED_LEGACY_FILE_FACT"
    if workspace_root is None:
        # The legacy public helper has no workspace argument.  Its callers
        # retain the historical projection; W13 callers always provide it.
        return "FRESH_FILE_FACT"
    try:
        root = Path(workspace_root).expanduser().resolve()
        current = resolve_workspace_path(root, path, require_file=True)
        assert_no_link_ancestors(current)
        current_hash = hashlib.sha256(current.read_bytes()).hexdigest()
    except (OSError, RuntimeError, ValueError):
        return "STALE_OR_INVALID_FILE_FACT"
    return "FRESH_FILE_FACT" if current_hash == stored_hash else "STALE_OR_INVALID_FILE_FACT"


def _include_file_fact(
    state: Mapping[str, Any],
    path: str,
    workspace_root: str | Path | None,
) -> bool:
    if workspace_root is None:
        return True
    return file_fact_freshness(state, path, workspace_root=workspace_root) == "FRESH_FILE_FACT"


def _section_lines(
    state: Mapping[str, Any],
    section: str,
    objective_tokens: set[str],
    *,
    only_relevant: bool = False,
) -> list[str]:
    entries = _entries(state.get(section))
    ranked = _rank_entries(entries, objective_tokens)
    if only_relevant and objective_tokens:
        ranked = [
            entry
            for entry in ranked
            if objective_tokens.intersection(_entry_tokens(entry[0], entry[1]))
        ]
    return [f"- {key}: {_as_text(value)}" for key, value in ranked]


def _append_bounded(body: str, text: str, available: int) -> tuple[str, bool]:
    separator = "\n" if body else ""
    remaining = available - len(body) - len(separator)
    if remaining <= 0:
        return body, False
    addition = separator + text
    if len(addition) <= remaining:
        return body + addition, True
    return body + addition[:remaining], False




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
    detailed_entries = [
        (key, value)
        for key, value in _entries(state.get("file_summaries"))
        if _referenced_file(key, objective, objective_tokens)
        and _include_file_fact(state, key, workspace_root)
    ]
    detailed_paths = {key.replace("\\", "/").casefold() for key, _ in detailed_entries}

    analyzed_entries = [
        (key, value)
        for key, value in _entries(state.get("analyzed_files"))
        if key.replace("\\", "/").casefold() not in detailed_paths
        and _include_file_fact(state, key, workspace_root)
    ]
    analyzed_entries = _rank_entries(analyzed_entries, objective_tokens)

    sections: list[tuple[str, list[str]]] = [
        (
            "RELEVANT FILE SUMMARIES",
            [f"- {key}: {_as_text(value)}" for key, value in detailed_entries],
        ),
        (
            "ANALYZED FILE INDEX",
            [f"- {key}: {_as_text(value)}" for key, value in analyzed_entries],
        ),
    ]

    for section in ("key_findings", "notes", "todo"):
        sections.append((section.upper(), _section_lines(state, section, objective_tokens)))
    for section in ("project_map", "files_index"):
        sections.append(
            (
                section.upper(),
                _section_lines(
                    state,
                    section,
                    objective_tokens,
                    only_relevant=True,
                ),
            )
        )

    return _bounded_envelope(sections, budget_tokens)


__all__ = [
    "DEFAULT_MEMORY_PROMPT_BUDGET_TOKENS",
    "MEMORY_PROMPT_CHARS_PER_TOKEN",
    "build_memory_prompt_context",
    "file_fact_freshness",
]
