"""Keyboard-first finite selector with injected input/output ownership."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Callable

MAX_SELECTOR_ITEMS = 100
MAX_ITEM_ID_CHARS = 128
MAX_LABEL_CHARS = 160
MAX_DESCRIPTION_CHARS = 512


@dataclass(frozen=True, slots=True)
class SelectorItem:
    item_id: str
    label: str
    description: str = ""
    disabled_reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.item_id, str) or not self.item_id.strip() or len(self.item_id) > MAX_ITEM_ID_CHARS:
            raise ValueError("selector item_id is invalid")
        if not isinstance(self.label, str) or not self.label.strip() or len(self.label) > MAX_LABEL_CHARS:
            raise ValueError("selector label is invalid")
        if not isinstance(self.description, str) or len(self.description) > MAX_DESCRIPTION_CHARS:
            raise ValueError("selector description is invalid")
        if self.disabled_reason is not None and (
            not isinstance(self.disabled_reason, str) or not self.disabled_reason.strip()
        ):
            raise ValueError("disabled_reason is invalid")


@dataclass(frozen=True, slots=True)
class SelectorResult:
    item_id: str | None
    cancelled: bool


def _find_selection(items: tuple[SelectorItem, ...], value: str) -> tuple[SelectorItem | None, bool]:
    selected: SelectorItem | None = None
    if value.isdigit():
        index = int(value)
        if 1 <= index <= len(items):
            selected = items[index - 1]
    if selected is None:
        selected = next(
            (item for item in items if item.item_id.casefold() == value.casefold()),
            None,
        )
    if selected is not None or not value:
        return selected, False
    candidates = tuple(
        item
        for item in items
        if item.item_id.casefold().startswith(value.casefold())
        or item.label.casefold().startswith(value.casefold())
    )
    return (candidates[0], False) if len(candidates) == 1 else (None, len(candidates) > 1)


def _resolve_selection(
    items: tuple[SelectorItem, ...],
    default: SelectorItem | None,
    raw: str | None,
) -> tuple[SelectorResult | None, str | None]:
    if raw is None:
        return SelectorResult(None, True), None
    value = str(raw).strip()
    if not value and default is not None:
        return SelectorResult(default.item_id, False), None
    if value.casefold() in {"q", "quit", "cancel"}:
        return SelectorResult(None, True), None
    selected, ambiguous = _find_selection(items, value)
    if ambiguous:
        return None, "Seleção ambígua; escolha um item da lista."
    if selected is None:
        return None, "Seleção inválida; escolha um item da lista."
    if selected.disabled_reason is not None:
        return None, f"Item indisponível: {selected.disabled_reason}"
    return SelectorResult(selected.item_id, False), None


class TerminalSelector:
    def __init__(self, *, prompt_line: Callable[..., str | None], emit: Callable[[object], None]) -> None:
        if not callable(prompt_line) or not callable(emit):
            raise TypeError("selector requires injected prompt_line and emit callables")
        self._prompt_line = prompt_line
        self._emit = emit

    @staticmethod
    def _normalize(items: Iterable[SelectorItem]) -> tuple[SelectorItem, ...]:
        values = tuple(items)
        if len(values) > MAX_SELECTOR_ITEMS:
            raise ValueError("selector item limit exceeded")
        ids: set[str] = set()
        for item in values:
            if not isinstance(item, SelectorItem):
                raise TypeError("selector items must be SelectorItem values")
            key = item.item_id.casefold()
            if key in ids:
                raise ValueError("selector item IDs must be unique")
            ids.add(key)
        return values

    def _display(self, items: tuple[SelectorItem, ...], title: str) -> None:
        self._emit(title)
        for index, item in enumerate(items, start=1):
            suffix = f" — {item.disabled_reason}" if item.disabled_reason else ""
            self._emit(f"{index}. {item.label} [{item.item_id}]{suffix}")

    def choose(
        self,
        items: Iterable[SelectorItem],
        *,
        title: str,
        default_id: str | None = None,
    ) -> SelectorResult:
        values = self._normalize(items)
        if not isinstance(title, str) or not title.strip():
            raise ValueError("selector title is required")
        enabled = tuple(item for item in values if item.disabled_reason is None)
        default = next(
            (item for item in enabled if default_id is not None and item.item_id.casefold() == default_id.casefold()),
            None,
        )
        while True:
            self._display(values, title)
            try:
                raw = self._prompt_line("> ", default="")
            except (EOFError, KeyboardInterrupt):
                return SelectorResult(None, True)
            result, feedback = _resolve_selection(values, default, raw)
            if feedback is not None:
                self._emit(feedback)
                continue
            if result is not None:
                return result


__all__ = [
    "MAX_SELECTOR_ITEMS",
    "SelectorItem",
    "SelectorResult",
    "TerminalSelector",
]
