"""Bounded source-level analysis for registered symbol bridges.

This module deliberately answers one narrow question: whether a module has
exactly one authorized direct ``ImportFrom`` binding for a registered local
symbol.  It does not execute Python, infer aliases, or model runtime effects.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Iterable, Protocol, cast


class StructuralStatus(str, Enum):
    """Finite outcomes of the bounded structural bridge proof."""

    UNBOUND = "UNBOUND"
    EXACT = "EXACT"
    AMBIGUOUS = "AMBIGUOUS"
    INVALID = "INVALID"


class _TypeAliasNode(Protocol):
    value: ast.expr
    name: ast.Name


@dataclass(frozen=True)
class StructuralOwner:
    """The canonical owner identified by an authorized direct import."""

    module: str
    symbol: str


@dataclass(frozen=True)
class BindingEvent:
    """One direct module-execution binding event for the tracked symbol."""

    local_name: str
    kind: str
    line: int
    authorized: bool = False
    imported_module: str | None = None
    imported_symbol: str | None = None


@dataclass(frozen=True)
class StructuralAnalysis:
    """Structural facts consumed by the existing lifecycle policy."""

    status: StructuralStatus
    owner: StructuralOwner | None = None
    events: tuple[BindingEvent, ...] = ()
    diagnostic: str | None = None

    @property
    def state(self) -> str:
        return self.status.value

    def is_exact_target(self, module: str, symbol: str) -> bool:
        return (
            self.status is StructuralStatus.EXACT
            and self.owner == StructuralOwner(module, symbol)
        )


class StructuralBindingCollector:
    """Collect module-scope binders without interpreting execution.

    Ordinary control-flow bodies are visited as module execution scope.  A
    function, class, lambda, or comprehension target scope is a scope
    boundary; its local body/targets are not treated as module bindings.
    Assignment expressions in expressions are still visited because their
    target is a direct syntactic binder in the containing scope.
    """

    def __init__(
        self,
        source_symbol: str,
        *,
        target_module: str,
        target_symbol: str,
    ) -> None:
        self.source_symbol = source_symbol
        self.target_module = target_module
        self.target_symbol = target_symbol
        self._events: list[BindingEvent] = []

    def collect(self, tree: ast.Module) -> tuple[BindingEvent, ...]:
        self._events = []
        self._visit_statements(tree.body)
        return tuple(self._events)

    def _line(self, node: ast.AST) -> int:
        return int(getattr(node, "lineno", 0) or 0)

    def _record(
        self,
        node: ast.AST,
        kind: str,
        *,
        imported_module: str | None = None,
        imported_symbol: str | None = None,
        authorized: bool = False,
    ) -> None:
        self._events.append(
            BindingEvent(
                self.source_symbol,
                kind,
                self._line(node),
                authorized,
                imported_module,
                imported_symbol,
            )
        )

    def _visit_statements(self, statements: Iterable[ast.stmt]) -> None:
        for statement in statements:
            self._visit_statement(statement)

    def _visit_statement(self, node: ast.stmt) -> None:
        handler = self._statement_handler(node)
        if handler is None:
            self._visit_generic_statement(node)
        else:
            handler(node)

    def _statement_handler(self, node: ast.stmt) -> Callable[[ast.AST], None] | None:
        candidates = (
            ((ast.Import,), self._visit_import),
            ((ast.ImportFrom,), self._visit_import_from),
            ((ast.FunctionDef, ast.AsyncFunctionDef), self._visit_function),
            ((ast.ClassDef,), self._visit_class),
            ((ast.Assign,), self._visit_assign),
            ((ast.AnnAssign,), self._visit_ann_assign),
            ((ast.AugAssign,), self._visit_aug_assign),
            ((ast.NamedExpr,), self._visit_named_expr),
            ((ast.Delete,), self._visit_delete),
            ((ast.For, ast.AsyncFor), self._visit_for),
            ((ast.While,), self._visit_while),
            ((ast.If,), self._visit_if),
            ((ast.With, ast.AsyncWith), self._visit_with),
            ((ast.Try, getattr(ast, "TryStar", ast.Try)), self._visit_try),
            ((ast.Match,), self._visit_match),
        )
        for node_types, handler in candidates:
            if isinstance(node, node_types):
                return cast(Callable[[ast.AST], None], handler)
        type_alias = getattr(ast, "TypeAlias", None)
        if type_alias is not None and isinstance(node, type_alias):
            return self._visit_type_alias
        return None

    def _visit_import(self, node: ast.Import) -> None:
        for alias in node.names:
            bound = alias.asname or alias.name.split(".", 1)[0]
            if bound == self.source_symbol:
                self._record(node, "import", imported_module=alias.name)

    def _visit_import_from(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if alias.name == "*":
                self._record(node, "wildcard_import", imported_module=node.module or "")
                continue
            bound = alias.asname or alias.name
            if bound != self.source_symbol:
                continue
            module = node.module or ""
            authorized = node.level == 0 and module == self.target_module and alias.name == self.target_symbol
            self._record(
                node,
                "import_from",
                imported_module=module,
                imported_symbol=alias.name,
                authorized=authorized,
            )

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        if node.name == self.source_symbol:
            kind = "async_function_definition" if isinstance(node, ast.AsyncFunctionDef) else "function_definition"
            self._record(node, kind)
        self._visit_function_signature(node)

    def _visit_class(self, node: ast.ClassDef) -> None:
        if node.name == self.source_symbol:
            self._record(node, "class_definition")
        for decorator in node.decorator_list:
            self._visit_expr(decorator)
        for base in node.bases:
            self._visit_expr(base)
        for keyword in node.keywords:
            self._visit_expr(keyword.value)
        for parameter in getattr(node, "type_params", ()):
            self._visit_expr_container(parameter)

    def _visit_assign(self, node: ast.Assign) -> None:
        self._visit_expr(node.value)
        for target in node.targets:
            self._visit_target(target, "assignment")

    def _visit_ann_assign(self, node: ast.AnnAssign) -> None:
        self._visit_expr(node.annotation)
        self._visit_expr(node.value)
        self._visit_target(node.target, "annotated_assignment")

    def _visit_aug_assign(self, node: ast.AugAssign) -> None:
        self._visit_expr(node.target)
        self._visit_expr(node.value)
        self._visit_target(node.target, "augmented_assignment")

    def _visit_named_expr(self, node: ast.NamedExpr) -> None:
        self._visit_expr(node.value)
        self._visit_target(node.target, "walrus")

    def _visit_delete(self, node: ast.Delete) -> None:
        for target in node.targets:
            self._visit_target(target, "delete")

    def _visit_for(self, node: ast.For | ast.AsyncFor) -> None:
        self._visit_expr(node.iter)
        kind = "async_for_target" if isinstance(node, ast.AsyncFor) else "for_target"
        self._visit_target(node.target, kind)
        self._visit_statements(node.body)
        self._visit_statements(node.orelse)

    def _visit_while(self, node: ast.While) -> None:
        self._visit_expr(node.test)
        self._visit_statements(node.body)
        self._visit_statements(node.orelse)

    def _visit_if(self, node: ast.If) -> None:
        self._visit_expr(node.test)
        self._visit_statements(node.body)
        self._visit_statements(node.orelse)

    def _visit_with(self, node: ast.With | ast.AsyncWith) -> None:
        kind = "async_with_target" if isinstance(node, ast.AsyncWith) else "with_target"
        for item in node.items:
            self._visit_expr(item.context_expr)
            if item.optional_vars is not None:
                self._visit_target(item.optional_vars, kind)
        self._visit_statements(node.body)

    def _visit_try(self, node: ast.Try) -> None:
        self._visit_statements(node.body)
        for handler in node.handlers:
            self._visit_handler(handler)
        self._visit_statements(node.orelse)
        self._visit_statements(node.finalbody)

    def _visit_match(self, node: ast.Match) -> None:
        self._visit_expr(node.subject)
        for case in node.cases:
            self._visit_pattern(case.pattern)
            self._visit_expr(case.guard)
            self._visit_statements(case.body)

    def _visit_type_alias(self, node: ast.AST) -> None:
        type_alias = cast(_TypeAliasNode, node)
        self._visit_expr(type_alias.value)
        self._visit_target(type_alias.name, "type_alias")

    def _visit_generic_statement(self, node: ast.stmt) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.stmt):
                self._visit_statement(child)
            elif isinstance(child, ast.expr):
                self._visit_expr(child)
            else:
                self._visit_expr_container(child)

    def _visit_handler(self, node: ast.ExceptHandler) -> None:
        if node.type is not None:
            self._visit_expr(node.type)
        if node.name == self.source_symbol:
            self._record(node, "except_target")
        self._visit_statements(node.body)

    def _visit_function_signature(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> None:
        for decorator in node.decorator_list:
            self._visit_expr(decorator)
        args = node.args
        for argument in (*args.posonlyargs, *args.args, *args.kwonlyargs):
            if argument.annotation is not None:
                self._visit_expr(argument.annotation)
        if args.vararg is not None and args.vararg.annotation is not None:
            self._visit_expr(args.vararg.annotation)
        if args.kwarg is not None and args.kwarg.annotation is not None:
            self._visit_expr(args.kwarg.annotation)
        for default in (*args.defaults, *(item for item in args.kw_defaults if item is not None)):
            self._visit_expr(default)
        if node.returns is not None:
            self._visit_expr(node.returns)
        for parameter in getattr(node, "type_params", ()):
            self._visit_expr_container(parameter)

    def _visit_target(self, node: ast.AST, kind: str) -> None:
        if isinstance(node, ast.Name):
            if node.id == self.source_symbol:
                self._record(node, kind)
            return
        if isinstance(node, (ast.Tuple, ast.List, ast.Starred)):
            values = node.elts if isinstance(node, (ast.Tuple, ast.List)) else (node.value,)
            for value in values:
                self._visit_target(value, kind)
            return
        if isinstance(node, (ast.Attribute, ast.Subscript)):
            self._visit_expr(node)

    def _visit_expr(self, node: ast.AST | None) -> None:
        if node is None:
            return
        if isinstance(node, ast.NamedExpr):
            self._visit_expr(node.value)
            self._visit_target(node.target, "walrus")
            return
        if isinstance(node, ast.Lambda):
            for default in (*node.args.defaults, *(item for item in node.args.kw_defaults if item is not None)):
                self._visit_expr(default)
            return
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            for generator in node.generators:
                self._visit_expr(generator.iter)
                for condition in generator.ifs:
                    self._visit_expr(condition)
            if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp)):
                self._visit_expr(node.elt)
            else:
                self._visit_expr(node.key)
                self._visit_expr(node.value)
            return
        self._visit_expr_children(node)

    def _visit_expr_children(self, node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.expr):
                self._visit_expr(child)
            elif not isinstance(child, ast.stmt):
                self._visit_expr_container(child)

    def _visit_expr_container(self, node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.expr):
                self._visit_expr(child)
            elif isinstance(child, ast.stmt):
                continue
            else:
                self._visit_expr_container(child)

    def _visit_pattern(self, node: ast.pattern) -> None:
        handler = self._pattern_handler(node)
        if handler is not None:
            handler(node)

    def _pattern_handler(self, node: ast.pattern) -> Callable[[ast.AST], None] | None:
        candidates = (
            (ast.MatchAs, self._visit_match_as),
            (ast.MatchStar, self._visit_match_star),
            (ast.MatchMapping, self._visit_match_mapping),
            (ast.MatchSequence, self._visit_match_sequence),
            (ast.MatchClass, self._visit_match_class),
            (ast.MatchOr, self._visit_match_or),
            (ast.MatchValue, self._visit_match_value),
        )
        for node_type, handler in candidates:
            if isinstance(node, node_type):
                return cast(Callable[[ast.AST], None], handler)
        return None

    def _record_match_capture(self, node: ast.AST, name: str | None) -> None:
        if name and name != "_" and name == self.source_symbol:
            self._record(node, "match_capture")

    def _visit_match_as(self, node: ast.MatchAs) -> None:
        self._visit_pattern(node.pattern) if node.pattern is not None else None
        self._record_match_capture(node, node.name)

    def _visit_match_star(self, node: ast.MatchStar) -> None:
        self._record_match_capture(node, node.name)

    def _visit_match_mapping(self, node: ast.MatchMapping) -> None:
        for key in node.keys:
            self._visit_expr(key)
        for pattern in node.patterns:
            self._visit_pattern(pattern)
        self._record_match_capture(node, node.rest)

    def _visit_match_sequence(self, node: ast.MatchSequence) -> None:
        for pattern in node.patterns:
            self._visit_pattern(pattern)

    def _visit_match_class(self, node: ast.MatchClass) -> None:
        self._visit_expr(node.cls)
        for pattern in (*node.patterns, *node.kwd_patterns):
            self._visit_pattern(pattern)

    def _visit_match_or(self, node: ast.MatchOr) -> None:
        for pattern in node.patterns:
            self._visit_pattern(pattern)

    def _visit_match_value(self, node: ast.MatchValue) -> None:
        self._visit_expr(node.value)


def collect_binding_events(
    tree: ast.Module,
    source_symbol: str,
    *,
    target_module: str,
    target_symbol: str,
) -> tuple[BindingEvent, ...]:
    """Return direct module-scope events for one registered local symbol."""

    return StructuralBindingCollector(
        source_symbol,
        target_module=target_module,
        target_symbol=target_symbol,
    ).collect(tree)


def analyze_symbol_bridge(
    tree: ast.Module,
    source_symbol: str,
    *,
    target_module: str,
    target_symbol: str,
) -> StructuralAnalysis:
    """Classify one source symbol using only the bounded structural grammar."""

    events = collect_binding_events(
        tree,
        source_symbol,
        target_module=target_module,
        target_symbol=target_symbol,
    )
    authorized = tuple(event for event in events if event.authorized)
    if not events:
        return StructuralAnalysis(StructuralStatus.UNBOUND, events=events)
    if len(events) == 1 and len(authorized) == 1:
        event = authorized[0]
        return StructuralAnalysis(
            StructuralStatus.EXACT,
            StructuralOwner(event.imported_module or "", event.imported_symbol or ""),
            events,
        )
    if authorized:
        return StructuralAnalysis(
            StructuralStatus.AMBIGUOUS,
            events=events,
            diagnostic="authorized import has competing direct module-scope bindings",
        )
    return StructuralAnalysis(
        StructuralStatus.INVALID,
        events=events,
        diagnostic="no authorized direct absolute ImportFrom binding",
    )


__all__ = [
    "BindingEvent",
    "StructuralAnalysis",
    "StructuralBindingCollector",
    "StructuralOwner",
    "StructuralStatus",
    "analyze_symbol_bridge",
    "collect_binding_events",
]
