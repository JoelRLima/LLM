"""Bounded Python AST discovery used by target grounding."""

from __future__ import annotations

import ast

from .target_grounding_model import GroundingError


def symbol_parts(value: str) -> tuple[str, ...]:
    parts = tuple(value.split("."))
    if not parts or any(not part or not part.isidentifier() for part in parts):
        raise GroundingError("GROUNDING_SYMBOL_INVALID")
    return parts


def _target_names(node: ast.AST) -> tuple[str, ...]:
    if isinstance(node, ast.Name):
        return (node.id,)
    if isinstance(node, ast.Starred):
        return _target_names(node.value)
    if isinstance(node, (ast.Tuple, ast.List)):
        names: list[str] = []
        for item in node.elts:
            names.extend(_target_names(item))
        return tuple(names)
    return ()


def _definition_name(node: ast.AST, parts: tuple[str, ...], parents: tuple[str, ...]) -> bool:
    if not isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.NamedExpr, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return False
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        name = node.name
    elif isinstance(node, ast.Assign):
        names = tuple(name for target in node.targets for name in _target_names(target))
        return any(parents + (name,) == parts for name in names)
    else:
        names = _target_names(node.target)
        return any(parents + (name,) == parts for name in names)
    return (parents + (name,)) == parts or (not parents and parts == (name,))


def _definition_location(node: ast.AST) -> tuple[int, int, int, int]:
    return (
        int(getattr(node, "lineno", 0)),
        int(getattr(node, "col_offset", 0)),
        int(getattr(node, "end_lineno", getattr(node, "lineno", 0))),
        int(getattr(node, "end_col_offset", getattr(node, "col_offset", 0))),
    )


class _DefinitionVisitor(ast.NodeVisitor):
    def __init__(self, parts: tuple[str, ...]) -> None:
        self.parts = parts
        self.parents: tuple[str, ...] = ()
        self.locations: list[tuple[int, int, int, int]] = []

    def _visit_scope(self, node: ast.AST, name: str) -> None:
        previous = self.parents
        self.parents = previous + (name,)
        self.generic_visit(node)
        self.parents = previous

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        if _definition_name(node, self.parts, self.parents):
            self.locations.append(_definition_location(node))
        self._visit_scope(node, node.name)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if _definition_name(node, self.parts, self.parents):
            self.locations.append(_definition_location(node))
        self._visit_scope(node, node.name)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        if _definition_name(node, self.parts, self.parents):
            self.locations.append(_definition_location(node))
        self._visit_scope(node, node.name)

    def visit_Assign(self, node: ast.Assign) -> None:
        if _definition_name(node, self.parts, self.parents):
            self.locations.append(_definition_location(node))
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if _definition_name(node, self.parts, self.parents):
            self.locations.append(_definition_location(node))
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        if _definition_name(node, self.parts, self.parents):
            self.locations.append(_definition_location(node))
        self.generic_visit(node)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        if _definition_name(node, self.parts, self.parents):
            self.locations.append(_definition_location(node))
        self.generic_visit(node)


def locations_for_source(source: bytes, symbol: str, resource: str) -> tuple[tuple[int, int, int, int], ...]:
    try:
        tree = ast.parse(source.decode("utf-8"), filename=resource)
    except (UnicodeDecodeError, SyntaxError):
        return ()
    visitor = _DefinitionVisitor(symbol_parts(symbol))
    visitor.visit(tree)
    return tuple(visitor.locations)


def structural_locations_for_source(
    source: bytes,
    symbol: str,
    resource: str,
) -> tuple[tuple[int, int, int, int], ...]:
    """Classify one source after a positive bounded identifier prefilter."""

    try:
        tree = ast.parse(source.decode("utf-8"), filename=resource)
    except (UnicodeDecodeError, SyntaxError) as exc:
        raise GroundingError("GROUNDING_SOURCE_UNCLASSIFIABLE") from exc
    visitor = _DefinitionVisitor(symbol_parts(symbol))
    visitor.visit(tree)
    return tuple(visitor.locations)
