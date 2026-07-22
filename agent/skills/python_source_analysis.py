"""Typed AST extraction used by the legacy code-analyzer skill."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

AnalysisResult = dict[str, Any]


class CallGraphVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.current_function: str | None = None
        self.calls: dict[str, list[str]] = {}

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        previous = self.current_function
        self.current_function = node.name
        self.calls.setdefault(node.name, [])
        self.generic_visit(node)
        self.current_function = previous

    def visit_Call(self, node: ast.Call) -> None:
        called: str | None = None
        if isinstance(node.func, ast.Name):
            called = node.func.id
        elif isinstance(node.func, ast.Attribute):
            called = node.func.attr
        if called and self.current_function:
            self.calls[self.current_function].append(called)
        self.generic_visit(node)


def _function_info(node: ast.FunctionDef) -> dict[str, Any]:
    return {
        "name": node.name,
        "line": node.lineno,
        "end_line": node.end_lineno,
        "args": [argument.arg for argument in node.args.args],
        "docstring": ast.get_docstring(node) or "",
        "calls": [],
    }


def _class_info(node: ast.ClassDef) -> dict[str, Any]:
    methods = [_function_info(item) for item in node.body if isinstance(item, ast.FunctionDef)]
    for method in methods:
        method.pop("calls", None)
    return {
        "name": node.name,
        "line": node.lineno,
        "end_line": node.end_lineno,
        "methods": methods,
        "docstring": ast.get_docstring(node) or "",
    }


def _imports_from(node: ast.Import | ast.ImportFrom) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    module = node.module or ""
    return [f"{module}.{alias.name}" if module else alias.name for alias in node.names]


def _compact_data(tree: ast.AST, file_path: Path, base_dir: Path) -> AnalysisResult:
    functions = [node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)]
    classes = [
        {
            "name": node.name,
            "methods": [item.name for item in node.body if isinstance(item, ast.FunctionDef)],
        }
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
    ]
    return {
        "file": str(file_path.relative_to(base_dir)),
        "classes": classes,
        "functions": functions,
    }


def _add_sources(items: list[dict[str, Any]], source_lines: list[str]) -> None:
    for item in items:
        start = item.get("line")
        end = item.get("end_line")
        if isinstance(start, int) and isinstance(end, int):
            item["source"] = "".join(source_lines[start - 1 : end])


def _full_data(
    tree: ast.AST,
    source: str,
    file_path: Path,
    base_dir: Path,
    include_code: bool,
) -> AnalysisResult:
    imports: list[str] = []
    functions: list[dict[str, Any]] = []
    classes: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            imports.extend(_imports_from(node))
        elif isinstance(node, ast.FunctionDef):
            functions.append(_function_info(node))
        elif isinstance(node, ast.ClassDef):
            classes.append(_class_info(node))
    visitor = CallGraphVisitor()
    visitor.visit(tree)
    for function in functions:
        function["calls"] = sorted(set(visitor.calls.get(str(function["name"]), [])))
    if include_code:
        source_lines = source.splitlines(keepends=True)
        _add_sources(functions, source_lines)
        _add_sources(classes, source_lines)
        for class_info in classes:
            _add_sources(class_info["methods"], source_lines)
    return {
        "file": str(file_path.relative_to(base_dir)),
        "imports": imports,
        "functions": functions,
        "classes": classes,
        "call_graph": visitor.calls,
    }


def analyze_python_file(
    file_path: Path,
    base_dir: Path,
    *,
    include_code: bool = False,
    compact: bool = False,
) -> AnalysisResult:
    source = file_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    if compact:
        return _compact_data(tree, file_path, base_dir)
    return _full_data(tree, source, file_path, base_dir, include_code)
