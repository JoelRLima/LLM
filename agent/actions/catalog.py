"""Immutable action-definition catalog."""

from __future__ import annotations

from collections.abc import Iterable, Iterator

from agent.actions.models import ActionDefinition, ActionScope


class ActionCatalog:
    def __init__(self, definitions: Iterable[ActionDefinition]) -> None:
        values = tuple(definitions)
        index: dict[str, ActionDefinition] = {}
        for definition in values:
            if not isinstance(definition, ActionDefinition):
                raise TypeError("catalog definitions must be ActionDefinition values")
            if definition.action_id in index:
                raise ValueError(f"duplicate action id: {definition.action_id}")
            index[definition.action_id] = definition
        self._definitions = values
        self._index = index

    def get(self, action_id: str) -> ActionDefinition:
        try:
            return self._index[action_id]
        except KeyError as exc:
            raise KeyError(f"unknown action id: {action_id}") from exc

    def maybe_get(self, action_id: str) -> ActionDefinition | None:
        return self._index.get(action_id)

    def list(self, *, scope: ActionScope | None = None) -> tuple[ActionDefinition, ...]:
        values = (
            item for item in self._definitions
            if scope is None or item.scope is scope
        )
        return tuple(sorted(values, key=lambda item: (item.category.value, item.action_id)))

    def __iter__(self) -> Iterator[ActionDefinition]:
        return iter(self.list())

    def __len__(self) -> int:
        return len(self._definitions)


__all__ = ["ActionCatalog"]
