"""AST resolution helpers for adversarial observability ownership checks."""

from __future__ import annotations

import ast
from collections.abc import Iterable


class SymbolResolver:
    """Resolve direct imports, import aliases and simple assignment aliases."""

    def __init__(self, tree: ast.AST) -> None:
        self.aliases = _import_aliases(tree)
        assignments = _assignments(tree)
        self._resolve_assignments(assignments)

    def _resolve_assignments(self, assignments: list[ast.Assign | ast.AnnAssign]) -> None:
        """Reach a fixed point for simple assignment aliases."""

        for _unused in range(len(assignments) + 1):
            if not self._resolve_assignment_pass(assignments):
                break

    def _resolve_assignment_pass(
        self, assignments: list[ast.Assign | ast.AnnAssign]
    ) -> bool:
        changed = False
        for node in assignments:
            resolved = self.resolve(node.value) if node.value is not None else None
            if resolved is None:
                continue
            targets: Iterable[ast.expr] = node.targets if isinstance(node, ast.Assign) else (node.target,)
            for target in targets:
                if isinstance(target, ast.Name) and self.aliases.get(target.id) != resolved:
                    self.aliases[target.id] = resolved
                    changed = True
        return changed

    def resolve(self, node: ast.AST) -> str | None:
        if isinstance(node, ast.Name):
            return self.aliases.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            owner = self.resolve(node.value)
            return f"{owner}.{node.attr}" if owner else node.attr
        return None

    def call_name(self, node: ast.AST) -> str | None:
        return self.resolve(node.func) if isinstance(node, ast.Call) else None


def _import_aliases(tree: ast.AST) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for item in node.names:
                bound = item.asname or item.name.split(".")[0]
                aliases[bound] = item.name if item.asname else bound
        elif isinstance(node, ast.ImportFrom) and node.module:
            for item in node.names:
                if item.name != "*":
                    aliases[item.asname or item.name] = f"{node.module}.{item.name}"
    return aliases


def _assignments(tree: ast.AST) -> list[ast.Assign | ast.AnnAssign]:
    return [node for node in ast.walk(tree) if isinstance(node, (ast.Assign, ast.AnnAssign))]


def parent_map(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    return {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }


def enclosing_function(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> str | None:
    current = node
    while current in parents:
        current = parents[current]
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return current.name
    return None


def _snapshot_test(node: ast.AST) -> bool | None:
    if not isinstance(node, ast.Compare) or len(node.ops) != 1 or len(node.comparators) != 1:
        return None
    left = node.left
    right = node.comparators[0]
    if not isinstance(left, ast.Name) or left.id != "snapshot" or not isinstance(right, ast.Constant):
        return None
    if right.value is not None:
        return None
    if isinstance(node.ops[0], ast.Is):
        return True
    if isinstance(node.ops[0], ast.IsNot):
        return False
    return None


def inside_snapshotless_boundary(
    node: ast.AST,
    parents: dict[ast.AST, ast.AST],
) -> bool:
    current = node
    while current in parents:
        parent = parents[current]
        if isinstance(parent, ast.If):
            none_branch = _snapshot_test(parent.test)
            if none_branch is not None:
                in_body = current in parent.body
                if (none_branch and in_body) or (not none_branch and not in_body):
                    return True
        if isinstance(parent, ast.IfExp):
            none_branch = _snapshot_test(parent.test)
            if none_branch is not None:
                if (none_branch and current is parent.body) or (
                    not none_branch and current is parent.orelse
                ):
                    return True
        current = parent
    return False


def event_envelope(node: ast.AST, resolver: SymbolResolver) -> bool:
    if isinstance(node, ast.Dict):
        keys = {
            key.value
            for key in node.keys
            if isinstance(key, ast.Constant) and isinstance(key.value, str)
        }
        return {"type", "data"}.issubset(keys)
    if isinstance(node, ast.Call) and resolver.resolve(node.func) == "dict":
        keys = {keyword.arg for keyword in node.keywords if keyword.arg is not None}
        return {"type", "data"}.issubset(keys)
    return False


def raw_event_aliases(tree: ast.AST, resolver: SymbolResolver) -> frozenset[str]:
    aliases: set[str] = set()
    assignments = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.Assign, ast.AnnAssign))
    ]
    for _unused in range(len(assignments) + 1):
        changed = False
        for node in assignments:
            value = node.value
            if value is None:
                continue
            is_event = event_envelope(value, resolver) or (
                isinstance(value, ast.Name) and value.id in aliases
            )
            if not is_event:
                continue
            targets = node.targets if isinstance(node, ast.Assign) else (node.target,)
            for target in targets:
                if isinstance(target, ast.Name) and target.id not in aliases:
                    aliases.add(target.id)
                    changed = True
        if not changed:
            break
    return frozenset(aliases)


def is_raw_event_value(
    node: ast.AST,
    resolver: SymbolResolver,
    aliases: frozenset[str],
) -> bool:
    return event_envelope(node, resolver) or (
        isinstance(node, ast.Name) and node.id in aliases
    )


__all__ = [
    "SymbolResolver",
    "enclosing_function",
    "event_envelope",
    "inside_snapshotless_boundary",
    "is_raw_event_value",
    "parent_map",
    "raw_event_aliases",
]
