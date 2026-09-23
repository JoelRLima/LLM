"""Deterministic, duplicate-rejecting Discovery index builders."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import cast

from agent.discovery.contracts import (
    DiscoveryAvailability,
    DiscoveryEntryV1,
    DiscoverySourceKind,
)


def _entry_id(prefix: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("discovery source identifier is invalid")
    return f"{prefix}:{value}"


def action_entries(catalog: object, *, bindings: Iterable[object] = ()) -> tuple[DiscoveryEntryV1, ...]:
    """Project action ownership without moving action definitions into Discovery."""

    binding_by_action: dict[str, object] = {}
    for binding in bindings:
        action_id = getattr(binding, "action_id", None)
        if isinstance(action_id, str):
            binding_by_action[action_id] = binding
    entries: list[DiscoveryEntryV1] = []
    for item in cast(Iterable[object], catalog):
        action_id = getattr(item, "action_id", None)
        if not isinstance(action_id, str):
            raise TypeError("action catalog contains an invalid definition")
        binding = binding_by_action.get(action_id)
        preferred = getattr(binding, "preferred_path", None) if binding is not None else None
        if isinstance(preferred, tuple):
            invocation = " ".join(preferred)
        elif isinstance(preferred, (list,)):
            invocation = " ".join(str(value) for value in preferred)
        else:
            invocation = action_id
        aliases = tuple(" ".join(path) for path in getattr(binding, "aliases", ()) or ())
        entries.append(
            DiscoveryEntryV1(
                1,
                _entry_id("action", action_id),
                DiscoverySourceKind.ACTION,
                str(getattr(item, "title", action_id)),
                str(getattr(item, "description", action_id)),
                invocation,
                aliases=aliases,
                keywords=tuple(getattr(item, "keywords", ()) or ()),
                examples=tuple(getattr(item, "discovery_examples", ()) or ()),
            )
        )
    return tuple(entries)


def cli_entries_from_parser(parser: object) -> tuple[DiscoveryEntryV1, ...]:
    """Walk an argparse parser tree; no application/config/model is created."""

    import argparse

    entries: list[DiscoveryEntryV1] = []

    def visit(current: argparse.ArgumentParser, path: tuple[str, ...]) -> None:
        subparsers = next(
            (
                action
                for action in getattr(current, "_actions", ())
                if isinstance(action, argparse._SubParsersAction)
            ),
            None,
        )
        if subparsers is None:
            if path:
                command = " ".join(path)
                help_text = str(getattr(current, "description", "") or getattr(current, "prog", command))
                entries.append(
                    DiscoveryEntryV1(
                        1,
                        _entry_id("cli", command),
                        DiscoverySourceKind.CLI_COMMAND,
                        command,
                        help_text[:512] or command,
                        command,
                        examples=(command,),
                    )
                )
            return
        help_by_token = {
            str(getattr(choice, "dest", "")): str(getattr(choice, "help", "") or "")
            for choice in getattr(subparsers, "_choices_actions", ())
        }
        for token, child in sorted(subparsers.choices.items(), key=lambda pair: pair[0]):
            if not isinstance(token, str) or not isinstance(child, argparse.ArgumentParser):
                continue
            child_path = (*path, token)
            help_text = str(
                getattr(child, "description", "")
                or help_by_token.get(token, "")
                or token
            )
            entries.append(
                DiscoveryEntryV1(
                    1,
                    _entry_id("cli", " ".join(child_path)),
                    DiscoverySourceKind.CLI_COMMAND,
                    " ".join(child_path),
                    help_text[:512] or token,
                    " ".join(child_path),
                    examples=(" ".join(child_path),),
                )
            )
            child_subparsers = next(
                (
                    action
                    for action in getattr(child, "_actions", ())
                    if isinstance(action, argparse._SubParsersAction)
                ),
                None,
            )
            if child_subparsers is not None:
                visit(child, child_path)

    visit(cast(argparse.ArgumentParser, parser), ())
    return tuple(entries)


def engineering_entries(
    views: Iterable[object],
) -> tuple[tuple[DiscoveryEntryV1, ...], Mapping[str, DiscoveryAvailability]]:
    entries: list[DiscoveryEntryV1] = []
    availability: dict[str, DiscoveryAvailability] = {}
    for view in views:
        operation_id = getattr(view, "operation_id", None)
        if not isinstance(operation_id, str):
            raise TypeError("engineering projection contains an invalid operation")
        entry_id = _entry_id("engineering", operation_id)
        entry = DiscoveryEntryV1(
            1,
            entry_id,
            DiscoverySourceKind.ENGINEERING_OPERATION,
            operation_id,
            f"Engineering operation {operation_id}.",
            f"llm-agent test run {operation_id}",
            keywords=("engineering", "operation"),
            examples=(f"llm-agent test describe {operation_id}",),
        )
        entries.append(entry)
        available = getattr(view, "available", False)
        reason = getattr(view, "unavailable_reason", None)
        availability[entry_id] = DiscoveryAvailability(bool(available), reason if isinstance(reason, str) else None)
    return tuple(entries), availability


class DiscoveryCatalog:
    def __init__(self, entries: Iterable[DiscoveryEntryV1] = ()) -> None:
        values = tuple(entries)
        if len(values) > 512:
            raise ValueError("discovery catalog exceeds its bound")
        by_id: dict[str, DiscoveryEntryV1] = {}
        for item in values:
            if not isinstance(item, DiscoveryEntryV1):
                raise TypeError("discovery catalog contains an invalid entry")
            if item.entry_id in by_id:
                raise ValueError(f"duplicate discovery entry: {item.entry_id}")
            by_id[item.entry_id] = item
        self._entries = tuple(sorted(by_id.values(), key=lambda item: (item.title.casefold(), item.entry_id)))

    @classmethod
    def from_action_catalog(cls, catalog: object, *, bindings: Iterable[object] = ()) -> "DiscoveryCatalog":
        return cls(action_entries(catalog, bindings=bindings))

    def entries(self) -> tuple[DiscoveryEntryV1, ...]:
        return self._entries

    def get(self, entry_id: str) -> DiscoveryEntryV1 | None:
        return next((item for item in self._entries if item.entry_id == entry_id), None)


DiscoveryIndex = DiscoveryCatalog

__all__ = [
    "DiscoveryCatalog",
    "DiscoveryIndex",
    "action_entries",
    "cli_entries_from_parser",
    "engineering_entries",
]
